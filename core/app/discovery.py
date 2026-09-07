"""
Masar Core — محرك الاكتشاف الفعلي (B2، المرحلة 2 من الدليل §9).

هذا الملف هو نقطة الحقيقة الوحيدة لمنطق جولة الجامع: يقرأ `sources` النشطة،
يستدعي جامع كل نوع مصدر (core/app/collectors/*)، يطبّع كل وظيفة عبر
field_extractor + normalizer، يُدرج الجديد فقط في `jobs` (ON CONFLICT على
dedup_key)، يحدّث صحة كل مصدر (last_ok_at/last_error/avg_per_day)، يعطّل
المصدر تلقائيًا بعد 3 أخطاء متتالية، ويكتب مقاييس لكل ساعة (عامة ولكل
عائلة مهنية). يُستدعى من core/app/scheduler_main.py (كل 30 دقيقة + فورًا
عند الإقلاع) ومن core/app/discovery_api.py (`POST /admin/discovery/run-now`).

بلا SQLAlchemy ORM عمدًا (لا نماذج بعد) — استعلامات SQL صريحة عبر
sqlalchemy.text()، لأن هذه أول وحدة تكتب بيانات فعلية والمخطط لا يزال يتطور
بسرعة بالمرحلتين 2-3؛ ORM كامل يُضاف حين تستقر الجداول (بعد B3).
"""
from __future__ import annotations

import csv
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import yaml
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app.collectors import (
    ashby,
    greenhouse,
    lever,
    recruitee,
    rss,
    sitemap_jsonld,
    smartrecruiters,
    workable,
)
from app.collectors.field_extractor import (
    classify_application_type,
    extract_cities,
    extract_seniority,
    extract_skills,
    extract_years_required,
    is_saudi_only,
)
from app.collectors.normalizer import dedup_key

logger = logging.getLogger("masar.discovery")

PER_SOURCE_TIMEOUT = 30.0
ROUND_BUDGET_SECONDS = 20 * 60

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

_engine_singleton: Engine | None = None


def _data_dir() -> Path:
    return Path(os.environ.get("DATA_DIR", "/app/data"))


def get_engine() -> Engine:
    global _engine_singleton
    if _engine_singleton is not None:
        return _engine_singleton
    url = os.environ["DATABASE_URL"]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
    _engine_singleton = create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5)
    return _engine_singleton


# ---------------------------------------------------------------------------
# تصنيف العائلة المهنية — يقرأ data/taxonomy_local.yaml (مُركّب read-only)
# ---------------------------------------------------------------------------

_families_cache: dict | None = None


def _load_families() -> dict[str, dict]:
    global _families_cache
    if _families_cache is not None:
        return _families_cache
    path = _data_dir() / "taxonomy_local.yaml"
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except OSError:
        logger.warning("تعذّرت قراءة taxonomy_local.yaml من %s", path)
        data = {}
    _families_cache = data.get("families", {}) or {}
    return _families_cache


def classify_family(title: str | None) -> str | None:
    """يرجّع أول عائلة مهنية تُطابق المسمى عبر معجم taxonomy_local.yaml
    (كلمات إنجليزية غير حساسة لحالة الأحرف، وعربية بالمطابقة المباشرة)."""
    if not title:
        return None
    lowered = title.lower()
    for family, spec in _load_families().items():
        if spec.get("excluded"):
            continue
        for kw in spec.get("keywords_en", []) or []:
            if kw.lower() in lowered:
                return family
        for kw in spec.get("keywords_ar", []) or []:
            if kw in title:
                return family
    return None


# ---------------------------------------------------------------------------
# استخراج نص الوصف من الحمولة الخام لكل نوع مصدر (أسماء الحقول تختلف)
# ---------------------------------------------------------------------------


def _extract_description(raw_job: dict) -> str:
    node = raw_job.get("raw")
    if not isinstance(node, dict):
        return ""
    for key in ("content", "description", "descriptionPlain", "jobDescription", "summary"):
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            stripped = _HTML_TAG_RE.sub(" ", value)
            return _WS_RE.sub(" ", stripped).strip()[:5000]
    return ""


# ---------------------------------------------------------------------------
# دالّات الجلب لكل نوع مصدر — تأخذ رابط API الكامل المخزّن في sources.source_url
# وتستخرج منه المعرّف الذي يحتاجه الجامع المقابل (core/app/collectors/*)،
# فترفض أي رابط لا يطابق نمط الـAPI الرسمي المعروف بدل تخمين معامِلات خطرة.
# ---------------------------------------------------------------------------

_GREENHOUSE_RE = re.compile(r"boards-api\.(eu\.)?greenhouse\.io/v1/boards/([^/]+)/jobs")
_LEVER_RE = re.compile(r"api\.lever\.co/v0/postings/([^/?]+)")
_ASHBY_RE = re.compile(r"api\.ashbyhq\.com/posting-api/job-board/([^/?]+)")
_SMARTRECRUITERS_RE = re.compile(r"api\.smartrecruiters\.com/v1/companies/([^/]+)/postings")
_WORKABLE_RE = re.compile(r"workable\.com/api/v1/widget/accounts/([^/?]+)")
_RECRUITEE_RE = re.compile(r"https?://([a-zA-Z0-9-]+)\.recruitee\.com")


def _fetch_greenhouse(url: str, timeout: float) -> list[dict]:
    m = _GREENHOUSE_RE.search(url)
    if not m:
        raise ValueError(f"رابط Greenhouse غير متوقع: {url}")
    api_base = "https://boards-api.eu.greenhouse.io" if m.group(1) else "https://boards-api.greenhouse.io"
    return greenhouse.fetch_jobs(m.group(2), timeout=timeout, api_base=api_base)


def _fetch_lever(url: str, timeout: float) -> list[dict]:
    m = _LEVER_RE.search(url)
    if not m:
        raise ValueError(f"رابط Lever غير متوقع: {url}")
    return lever.fetch_jobs(m.group(1), timeout=timeout)


def _fetch_ashby(url: str, timeout: float) -> list[dict]:
    m = _ASHBY_RE.search(url)
    if not m:
        raise ValueError(f"رابط Ashby غير متوقع: {url}")
    return ashby.fetch_jobs(m.group(1), timeout=timeout)


def _fetch_smartrecruiters(url: str, timeout: float) -> list[dict]:
    m = _SMARTRECRUITERS_RE.search(url)
    if not m:
        raise ValueError(f"رابط SmartRecruiters غير متوقع: {url}")
    return smartrecruiters.fetch_jobs(m.group(1), timeout=timeout)


def _fetch_workable(url: str, timeout: float) -> list[dict]:
    m = _WORKABLE_RE.search(url)
    if not m:
        raise ValueError(f"رابط Workable غير متوقع: {url}")
    return workable.fetch_jobs(m.group(1), timeout=timeout)


def _fetch_recruitee(url: str, timeout: float) -> list[dict]:
    m = _RECRUITEE_RE.search(url)
    if not m:
        raise ValueError(f"رابط Recruitee غير متوقع: {url}")
    return recruitee.fetch_jobs(m.group(1), timeout=timeout)


def _fetch_sitemap_jsonld(url: str, timeout: float) -> list[dict]:
    return sitemap_jsonld.fetch_jobs(url, timeout=timeout)


def _fetch_rss(url: str, timeout: float) -> list[dict]:
    return rss.fetch_jobs(url, timeout=timeout)


DISPATCH: dict[str, Callable[[str, float], list[dict]]] = {
    "greenhouse": _fetch_greenhouse,
    "lever": _fetch_lever,
    "ashby": _fetch_ashby,
    "smartrecruiters": _fetch_smartrecruiters,
    "workable": _fetch_workable,
    "recruitee": _fetch_recruitee,
    "sitemap_jsonld": _fetch_sitemap_jsonld,
    "rss": _fetch_rss,
}


def validate_source(source_type: str, url: str, timeout: float = PER_SOURCE_TIMEOUT) -> int:
    """يُستخدم من `POST /admin/sources`: يجلب مرة واحدة فقط ويرجع عدد الوظائف
    الموجودة — لا يُدرج شيئًا في `jobs`. يرفع الاستثناء كما هو للمستدعي."""
    fetch_fn = DISPATCH.get(source_type)
    if fetch_fn is None:
        raise ValueError(f"نوع مصدر غير مدعوم: {source_type}")
    jobs = fetch_fn(url, timeout)
    return len(jobs)


# ---------------------------------------------------------------------------
# بذر المصادر من data/sources_seed.csv — idempotent (upsert بالاسم/الرابط)
# ---------------------------------------------------------------------------


def seed_sources() -> dict:
    path = _data_dir() / "sources_seed.csv"
    if not path.exists():
        logger.warning("ملف sources_seed.csv غير موجود في %s — تخطي البذر", path)
        return {"inserted": 0, "updated": 0, "skipped": 0}

    engine = get_engine()
    inserted = updated = skipped = 0
    with open(path, encoding="utf-8") as f, engine.begin() as conn:
        reader = csv.DictReader(f)
        for row in reader:
            company = (row.get("company") or "").strip()
            source_type = (row.get("type") or "").strip()
            url = (row.get("url") or "").strip()
            country = (row.get("country") or "").strip() or None
            terms_note = (row.get("terms_note") or "").strip() or None
            if not company or not source_type or not url or source_type not in DISPATCH:
                skipped += 1
                continue

            company_row = conn.execute(
                text(
                    """
                    INSERT INTO companies (name, country, status)
                    VALUES (:name, :country, 'active')
                    ON CONFLICT (name) DO UPDATE SET
                        country = COALESCE(EXCLUDED.country, companies.country)
                    RETURNING id
                    """
                ),
                {"name": company, "country": country},
            ).first()
            company_id = company_row[0]

            result = conn.execute(
                text(
                    """
                    INSERT INTO sources (company_id, source_type, source_url, terms_note, enabled)
                    VALUES (:company_id, :source_type, :url, :terms_note, true)
                    ON CONFLICT (source_url) DO UPDATE SET
                        terms_note = COALESCE(EXCLUDED.terms_note, sources.terms_note)
                    RETURNING (xmax = 0) AS was_insert
                    """
                ),
                {
                    "company_id": company_id,
                    "source_type": source_type,
                    "url": url,
                    "terms_note": terms_note,
                },
            ).first()
            if result and result[0]:
                inserted += 1
            else:
                updated += 1

    logger.info("بذر المصادر: %s جديد، %s محدّث، %s متخطّى", inserted, updated, skipped)
    return {"inserted": inserted, "updated": updated, "skipped": skipped}


# ---------------------------------------------------------------------------
# جولة الجامع الكاملة
# ---------------------------------------------------------------------------


def _record_source_error(engine: Engine, source_id: int, error_text: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE sources SET
                    last_error = :err,
                    consecutive_errors = consecutive_errors + 1,
                    updated_at = now()
                WHERE id = :id
                """
            ),
            {"err": error_text[:2000], "id": source_id},
        )
        row = conn.execute(
            text("SELECT consecutive_errors FROM sources WHERE id = :id"), {"id": source_id}
        ).first()
        if row and row[0] >= 3:
            conn.execute(
                text(
                    """
                    UPDATE sources SET enabled = false,
                        disabled_reason = :reason
                    WHERE id = :id
                    """
                ),
                {"reason": f"تعطيل تلقائي بعد 3 أخطاء متتالية: {error_text[:500]}", "id": source_id},
            )
            logger.warning("تعطيل المصدر %s تلقائيًا بعد 3 أخطاء متتالية", source_id)


def run_round() -> dict:
    """جولة واحدة كاملة: يُنادى كل 30 دقيقة من scheduler_main.py، وأيضًا فورًا
    عبر `POST /admin/discovery/run-now`. لا يرفع أي استثناء أبدًا — كل خطأ
    محلي لمصدر واحد يُسجّل ويُتابَع للمصدر التالي (اشتراط الدليل)."""
    engine = get_engine()
    started_at = datetime.now(timezone.utc)
    deadline = time.monotonic() + ROUND_BUDGET_SECONDS
    hour_bucket = started_at.replace(minute=0, second=0, microsecond=0)

    sources_processed = sources_ok = sources_error = jobs_fetched = jobs_new = 0

    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT s.id, s.company_id, s.source_type, s.source_url, c.name AS company_name
                    FROM sources s
                    JOIN companies c ON c.id = s.company_id
                    WHERE s.enabled = true
                    ORDER BY s.id
                    """
                )
            ).mappings().all()
    except Exception:
        logger.exception("تعذّرت قراءة جدول sources — إنهاء الجولة بلا معالجة")
        return {"ok": False, "sources_processed": 0}

    for row in rows:
        if time.monotonic() > deadline:
            logger.warning("انتهت ميزانية الجولة (20 دقيقة) — الباقي يُكمَل بالجولة التالية")
            break

        sources_processed += 1
        source_id = row["id"]
        source_type = row["source_type"]
        source_url = row["source_url"]
        company_id = row["company_id"]
        company_name = row["company_name"]

        try:
            fetch_fn = DISPATCH.get(source_type)
            if fetch_fn is None:
                raise ValueError(f"نوع مصدر غير مدعوم: {source_type}")
            raw_jobs = fetch_fn(source_url, PER_SOURCE_TIMEOUT)
        except Exception as exc:  # noqa: BLE001 — عزل خطأ مصدر واحد عن بقية الجولة
            logger.warning("فشل المصدر #%s (%s %s): %s", source_id, source_type, source_url, exc)
            try:
                _record_source_error(engine, source_id, str(exc))
            except Exception:
                logger.exception("تعذّر تسجيل خطأ المصدر #%s بالقاعدة", source_id)
            sources_error += 1
            continue

        jobs_fetched += len(raw_jobs)
        inserted_this_source = 0
        family_counts: dict[str, int] = {}

        try:
            with engine.begin() as conn:
                for raw_job in raw_jobs:
                    title = (raw_job.get("title") or "").strip()
                    if not title:
                        continue
                    location_text = (raw_job.get("location") or "").strip()
                    description = _extract_description(raw_job)
                    combined_text = "\n".join(filter(None, [title, location_text, description]))

                    cities = extract_cities(combined_text)
                    city = cities[0] if cities else None
                    years_min, _years_max = extract_years_required(combined_text)
                    seniority = extract_seniority(combined_text)
                    saudi_only = is_saudi_only(combined_text)
                    skills = extract_skills(combined_text)
                    family = classify_family(title)
                    apply_mode = classify_application_type(raw_job.get("url"), source_type)

                    external_id = raw_job.get("external_id")
                    external_key = f"{source_type}:{external_id}" if external_id else None
                    d_key = dedup_key(company_name, title, city, external_key)

                    inserted = conn.execute(
                        text(
                            """
                            INSERT INTO jobs (
                                source_id, company_id, external_id, title, url, location,
                                family, dedup_key, raw_json, company_name, city, years_min,
                                seniority, saudi_only, skills, apply_mode, description_snippet
                            ) VALUES (
                                :source_id, :company_id, :external_id, :title, :url, :location,
                                :family, :dedup_key, :raw_json, :company_name, :city, :years_min,
                                :seniority, :saudi_only, :skills, :apply_mode, :description_snippet
                            )
                            ON CONFLICT (dedup_key) DO NOTHING
                            RETURNING id
                            """
                        ),
                        {
                            "source_id": source_id,
                            "company_id": company_id,
                            "external_id": str(external_id) if external_id is not None else None,
                            "title": title[:2000],
                            "url": raw_job.get("url"),
                            "location": location_text[:255] if location_text else None,
                            "family": family,
                            "dedup_key": d_key,
                            "raw_json": json.dumps(raw_job.get("raw") or {}, ensure_ascii=False, default=str)[
                                :200000
                            ],
                            "company_name": (company_name or "")[:255] or None,
                            "city": city[:120] if city else None,
                            "years_min": years_min,
                            "seniority": seniority,
                            "saudi_only": saudi_only,
                            "skills": json.dumps(skills, ensure_ascii=False),
                            "apply_mode": apply_mode,
                            "description_snippet": description[:2000] if description else None,
                        },
                    ).first()

                    if inserted is not None:
                        inserted_this_source += 1
                        if family:
                            family_counts[family] = family_counts.get(family, 0) + 1

                conn.execute(
                    text(
                        """
                        UPDATE sources SET
                            last_ok_at = :now,
                            last_error = NULL,
                            last_count = :count,
                            consecutive_errors = 0,
                            consecutive_zero_rounds = CASE WHEN :count = 0
                                THEN consecutive_zero_rounds + 1 ELSE 0 END,
                            avg_per_day = ROUND((COALESCE(avg_per_day, 0) * 0.8 + :count * 48 * 0.2)::numeric, 2),
                            updated_at = :now
                        WHERE id = :id
                        """
                    ),
                    {"now": started_at, "count": len(raw_jobs), "id": source_id},
                )

                conn.execute(
                    text(
                        """
                        INSERT INTO metrics_hourly (source_id, hour_bucket, jobs_seen)
                        VALUES (:source_id, :hour_bucket, :count)
                        ON CONFLICT (source_id, hour_bucket)
                        DO UPDATE SET jobs_seen = metrics_hourly.jobs_seen + EXCLUDED.jobs_seen
                        """
                    ),
                    {"source_id": source_id, "hour_bucket": hour_bucket, "count": len(raw_jobs)},
                )

                for family, count in family_counts.items():
                    conn.execute(
                        text(
                            """
                            INSERT INTO metrics_family_hourly (family, hour_bucket, jobs_new)
                            VALUES (:family, :hour_bucket, :count)
                            ON CONFLICT (family, hour_bucket)
                            DO UPDATE SET jobs_new = metrics_family_hourly.jobs_new + EXCLUDED.jobs_new
                            """
                        ),
                        {"family": family, "hour_bucket": hour_bucket, "count": count},
                    )
        except Exception:  # noqa: BLE001 — خطأ إدراج لا يجب أن يوقف الجولة
            logger.exception("خطأ أثناء معالجة/إدراج وظائف المصدر #%s", source_id)
            try:
                _record_source_error(engine, source_id, "خطأ داخلي أثناء الإدراج بقاعدة البيانات")
            except Exception:
                pass
            sources_error += 1
            continue

        sources_ok += 1
        jobs_new += inserted_this_source

    finished_at = datetime.now(timezone.utc)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO discovery_rounds (
                        started_at, finished_at, sources_processed, sources_ok,
                        sources_error, jobs_fetched, jobs_new
                    ) VALUES (:started_at, :finished_at, :sp, :sok, :serr, :jf, :jn)
                    """
                ),
                {
                    "started_at": started_at,
                    "finished_at": finished_at,
                    "sp": sources_processed,
                    "sok": sources_ok,
                    "serr": sources_error,
                    "jf": jobs_fetched,
                    "jn": jobs_new,
                },
            )
    except Exception:
        logger.exception("تعذّر تسجيل جولة الاكتشاف بجدول discovery_rounds")

    logger.info(
        "جولة اكتشاف انتهت: %s مصدر (نجح %s، فشل %s)، %s وظيفة مجلوبة، %s وظيفة جديدة، %.1f ثانية",
        sources_processed,
        sources_ok,
        sources_error,
        jobs_fetched,
        jobs_new,
        (finished_at - started_at).total_seconds(),
    )

    return {
        "ok": True,
        "sources_processed": sources_processed,
        "sources_ok": sources_ok,
        "sources_error": sources_error,
        "jobs_fetched": jobs_fetched,
        "jobs_new": jobs_new,
        "seconds": (finished_at - started_at).total_seconds(),
    }
