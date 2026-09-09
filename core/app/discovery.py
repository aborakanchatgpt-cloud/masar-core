"""
Masar Core — محرك الاكتشاف الفعلي (B2، المرحلة 2 من الدليل §9).

هذا الملف هو نقطة الحقيقة الوحيدة لمنطق جولة الجامع: يقرأ `sources` النشطة،
يستدعي جامع كل نوع مصدر (core/app/collectors/*)، يطبّع كل وظيفة عبر
field_extractor + normalizer، يُدرج الجديد فقط في `jobs` (ON CONFLICT على
dedup_key)، يحدّث صحة كل مصدر (last_ok_at/last_error/avg_per_day)، يعطّل
المصدر تلقائيًا بعد 3 أخطاء متتالية أو بعد جولتين متتاليتين بلا أي وظيفة
خليجية واحدة (مراجعة B2 R3/R4)، ويكتب مقاييس لكل ساعة (عامة ولكل عائلة
مهنية). يُستدعى من core/app/scheduler_main.py (كل 30 دقيقة + فورًا عند
الإقلاع) ومن core/app/discovery_api.py (`POST /admin/discovery/run-now`).

بلا SQLAlchemy ORM عمدًا (لا نماذج بعد) — استعلامات SQL صريحة عبر
sqlalchemy.text()، لأن هذه أول وحدة تكتب بيانات فعلية والمخطط لا يزال يتطور
بسرعة بالمرحلتين 2-3؛ ORM كامل يُضاف حين تستقر الجداول (بعد B3).

مراجعة B2 R10 (docs/reports/B2-review-2.md): تكرار حقيقي بنسبة ~55% كان ينتج
لأن بعض المصادر (خصوصًا Workable لوكالات التوظيف) تُرجع نفس الوظيفة (نفس
apply_url) مرارًا ضمن استجابة واحدة، مرة لكل مدينة "مرشّحة"، وصيغة
dedup_key السابقة (R5) كانت تُدرج المدينة بالمفتاح فتُنتج صفًا منفصلًا لكل
مدينة. الإصلاح: `_group_raw_jobs_by_identity()` يُجمّع raw_jobs المجلوبة
بنفس الجولة حسب هوية الإعلان (dedup_key الجديد المعتمد على apply_url وحده
حين متوفر) *قبل* الإدراج، ويُنتج صفًا واحدًا لكل إعلان فعلي بحقل
`jobs.locations` (مصفوفة JSON) يجمع كل المواقع المذكورة. `jobs.location`
يبقى النص الخام لأول ظهور (تمثيلي فقط)، و`jobs.city` يبقى من الاستخراج
الأدق للعرض/الفلترة.

عمود `jobs.locations` (JSONB) أُضيف بتعديل مخطّط idempotent وقت الإقلاع
(`_ensure_schema`) لا بترحيل Alembic رسمي — B3 (منفّذ آخر يملك core/app/main.py
وترحيل 0004) يجري بالتوازي؛ سيُضاف ترحيل 0005 رسمي لاحقًا بعد استقرار 0004
على main بدل الآن لتفادي أي تصادم مراجعة.
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
    compute_region,
    extract_cities,
    extract_seniority,
    extract_skills,
    extract_years_required,
    is_saudi_only,
)
from app.collectors.normalizer import dedup_key, normalize_text

logger = logging.getLogger("masar.discovery")

PER_SOURCE_TIMEOUT = 30.0
ROUND_BUDGET_SECONDS = 20 * 60

# مراجعة B2 R3/R4: بعد جولتين متتاليتين بلا أي وظيفة خليجية واحدة (saudi_hits
# = 0 كِلا الجولتين)، يُعطّل المصدر تلقائيًا (مصادر region_filter='gcc' فقط).
GCC_ZERO_ROUNDS_DISABLE_THRESHOLD = 2

# مراجعة B2 R12 (docs/reports/B2-review-2.md): عائلات مُستبعدة عمدًا من
# التصنيف (مثل out_of_scope) يجب ألا تُحسب ضمن "غير مصنّف" — تُفصل صراحة
# عن مقياس family_classified_pct_in_region (discovery_api.py) بدل الخلط
# بينها وبين فجوة معجم حقيقية.
# مراجعة B2b: أُعيدت تسمية sales_excluded → out_of_scope (استُعمل عمومًا لأي
# دور خارج نطاق المنصّة صراحة، لا مبيعات فقط) — الاسم القديم مُبقى
# للتوافق مع أي صفّ لم يُعِد reclassify تصنيفه بعد.
EXCLUDED_FAMILY_NAMES = {"out_of_scope", "sales_excluded"}

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

_engine_singleton: Engine | None = None
_schema_ensured = False


def _data_dir() -> Path:
    return Path(os.environ.get("DATA_DIR", "/app/data"))


def _ensure_schema(engine: Engine) -> None:
    """مراجعة B2 R10: تعديل مخطّط idempotent بلا ترحيل Alembic رسمي (منفّذ
    B3 يملك حاليًا آخر ترحيل/main.py؛ راجع تعليق أعلى الملف). يُنفّذ مرة
    واحدة فقط لكل عملية حيّة، مضمون التكرار الآمن (`IF NOT EXISTS`)."""
    global _schema_ensured
    if _schema_ensured:
        return
    try:
        with engine.begin() as conn:
            conn.execute(
                text("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS locations JSONB DEFAULT '[]'::jsonb")
            )
        _schema_ensured = True
    except Exception:
        logger.exception("تعذّر التأكد من عمود jobs.locations — سيُعاد المحاولة بالاستدعاء التالي")


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def get_engine() -> Engine:
    """محرّك SQLAlchemy وحيد (singleton) لكل عملية (core أو core-scheduler —
    كل حاوية عملية منفصلة، بمجمّع اتصالات منفصل خاص بها).

    B6 (تصليب الحمل لـ1,500 عميل، docs/reports/B6-executor.md): الأحجام
    الافتراضية كانت ثابتة بالكود (pool_size=5, max_overflow=5 بلا
    pool_recycle ولا statement_timeout/lock_timeout) — أصبحت قابلة للضبط
    عبر متغيّرات بيئة منفصلة لكل خدمة (docker-compose.yml يمرّر قيمًا مختلفة
    لـcore وcore-scheduler)، مبنية على: 2 vCPU + Postgres
    `max_connections=120` (مرفوع من الافتراضي 100 — نفس عنقود Postgres
    يخدم أيضًا n8n بقاعدة بيانات منفصلة). الحساب المرجعي الموثّق بالتقرير:
    core (API، طلبات قصيرة) 10+5=15 كحد أقصى + core-scheduler (SEND_WORKERS
    حتى 20 خيطًا يفتح اتصاله الخاص بكل استعلام قصير عبر `engine.begin()`
    منفصلة — راجع sender.py) 15+15=30 كحد أقصى = 45 من Core وحده، هامش
    كبير تحت 120 حتى مع اتصالات n8n وجلسات `ops psql` اليدوية.

    - `DB_POOL_SIZE`/`DB_MAX_OVERFLOW` (افتراضي 5/5 — نفس القيم القديمة إن
      لم يُضبط شيء، لا تغيير سلوك بلا env صريح).
    - `DB_POOL_RECYCLE_SECONDS` (افتراضي 1800): يمنع استخدام اتصال قديم قد
      يكون Postgres أو موازن شبكة أغلقه بصمت (شبكة docker داخلية مستقرة
      عادة، لكن هامش أمان رخيص لعملية طويلة العمر كـcore-scheduler).
    - `DB_STATEMENT_TIMEOUT_MS` (افتراضي 0 = بلا حد — مُفعّل صراحة فقط
      لعملية core-scheduler عبر compose، الدليل: "statement_timeout لجلسات
      المجدوِل" — استعلامات الجامع/الإرسال/الوارد يجب ألا تُعلّق العملية
      كلها للأبد على استعلام واحد عالق، بعكس API الذي يُفضّل له بلا حد
      صارم كي لا يقطع طلب إداري بطيء لكن مشروع مثل `/admin/quality-sample`).
    - `DB_LOCK_TIMEOUT_MS` (افتراضي 5000): افتراضي معقول لكل الجلسات — صفّ
      مقفل (مثال: alembic أو معاملة أخرى) لا يُعلّق الاستعلام أكثر من 5
      ثوانٍ قبل أن يفشل بخطأ واضح بدل الانتظار الصامت.
    """
    global _engine_singleton
    if _engine_singleton is not None:
        return _engine_singleton
    url = os.environ["DATABASE_URL"]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://") :]

    pool_size = _int_env("DB_POOL_SIZE", 5)
    max_overflow = _int_env("DB_MAX_OVERFLOW", 5)
    pool_recycle = _int_env("DB_POOL_RECYCLE_SECONDS", 1800)
    statement_timeout_ms = _int_env("DB_STATEMENT_TIMEOUT_MS", 0)
    lock_timeout_ms = _int_env("DB_LOCK_TIMEOUT_MS", 5000)

    options_parts = [f"-c lock_timeout={lock_timeout_ms}"]
    if statement_timeout_ms > 0:
        options_parts.append(f"-c statement_timeout={statement_timeout_ms}")
    connect_args = {"options": " ".join(options_parts)}

    _engine_singleton = create_engine(
        url,
        pool_pre_ping=True,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_recycle=pool_recycle,
        connect_args=connect_args,
    )
    _ensure_schema(_engine_singleton)
    return _engine_singleton


# ---------------------------------------------------------------------------
# تصنيف العائلة المهنية — يقرأ data/taxonomy_local.yaml (مُركّب read-only)
#
# مراجعة B2 R6: مطابقة بحدود كلمة صريحة (لا سلسلة فرعية) عبر تعابير نمطية
# مُجمّعة مسبقًا لكل عائلة، على العنوان أولًا ثم الوصف كملاذ أخير (مراجعة
# B2b — كان سابقًا العنوان + أول 300 حرف من الوصف معًا دومًا).
#
# مراجعة B2 R12: العائلات المُستبعدة (`excluded: true`، مثل out_of_scope)
# كانت تُتخطّى بالكامل بـ`_build_family_patterns()` فتُحسب أي وظيفة مبيعات
# ضمن "غير مصنّف" رغم استبعادها عمدًا — الآن تُبنى أنماطها أيضًا وتُرجع
# كاسم عائلة فعلي (وليس None) حين تُطابَق، مع فصلها لاحقًا بمقياس
# family_classified_pct_in_region (discovery_api.py) عبر EXCLUDED_FAMILY_NAMES.
#
# مراجعة B2b: خطوة تطبيع قبل المطابقة (على النص المُبحوث عنه وعلى كل كلمة
# مفتاحية بالمعجم بنفس الدالة، حتى تبقى المقارنة متسقة الجهتين):
#   - عربي: إعادة استخدام normalizer.normalize_text (تشكيل/همزات/تاء مربوطة
#     — نفس منطق dedup_key بالضبط، مصدر حقيقة واحد لا تكرار منطق).
#   - "&" → " and " قبل التطبيع (يوحّد "Food & Beverage"/"food and beverage").
#   - Sr./Jr. → Senior/Junior، ورقم روماني لاحق (` II`/`-III`...) يُحذف.
#   - علامات الترقيم/الشرطات المائلة تتحوّل لمسافات ضمن normalize_text نفسها.
# ---------------------------------------------------------------------------

# ملاحظة تشغيلية (مراجعة B2): هذان الكاشان يُملآان مرة واحدة فقط لكل عملية
# (process) حيّة — تعديل data/taxonomy_local.yaml وحده لا يكفي لتفعيل
# كلمات مفتاحية جديدة على core الحيّ، يلزم إعادة تشغيل حاوية core فعليًا
# (`POST /admin/ops {"cmd":"up","args":[]}` بعد push كودي يُغيّر تجزئة طبقة
# COPY app ./app لإجبار إعادة البناء، أو أي تعديل كودي حقيقي بهذا الملف).
# مراجعة B2b: هذا بالضبط سبب وجود core/app/reclassify.py — يُشغّل كعملية
# بايثون طازجة منفصلة (`docker compose exec -T core python -m app.reclassify`)
# فيبني الكاشين من الصفر بالمعجم الحالي، بدل انتظار إعادة تشغيل الحاوية.
_families_cache: dict | None = None
_family_patterns_cache: list[tuple[str, re.Pattern[str]]] | None = None

DESCRIPTION_MATCH_CHARS = 300

# TAXONOMY_BUILD_MARK: يُحدّث هذا التعليق عمدًا مع كل push يرافق تعديلًا في
# data/taxonomy_local.yaml (انظر الملاحظة أعلاه) — تغييره وحده يكفي لإجبار
# طبقة Docker COPY app ./app على إعادة البناء دون أي تعديل منطقي فعلي هنا.
# آخر تحديث: مراجعة B2b — توسعة رابعة (out_of_scope بدل sales_excluded) +
# خطوة تطبيع + تصنيف عنوان-أولاً-ثم-وصف.

_AMP_RE = re.compile(r"&")
_SR_JR_RE = re.compile(r"\b(sr|jr)\.?\b", re.IGNORECASE)
_ROMAN_TAIL_RE = re.compile(r"[\s\-]+[ivx]{1,4}$", re.IGNORECASE)


def _normalize_for_match(text: str | None) -> str:
    """خطوة تطبيع قبل المطابقة (مراجعة B2b) — تُطبّق على نص العنوان/الوصف
    المُبحوث فيه وعلى كل كلمة مفتاحية بالمعجم بنفس الدالة (اتساق الجهتين):
    رقم روماني لاحق يُحذف، "&"→" and "، Sr./Jr.→Senior/Junior، ثم
    normalizer.normalize_text (تشكيل عربي/همزات/تاء مربوطة/ترقيم→مسافات/
    أحرف صغيرة) — نفس دالة تطبيع dedup_key بالضبط، لا منطق مكرّر."""
    if not text:
        return ""
    t = _ROMAN_TAIL_RE.sub("", text)
    t = _AMP_RE.sub(" and ", t)
    t = _SR_JR_RE.sub(lambda m: "senior" if m.group(1).lower() == "sr" else "junior", t)
    return normalize_text(t)


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


def _build_family_patterns() -> list[tuple[str, re.Pattern[str]]]:
    global _family_patterns_cache
    if _family_patterns_cache is not None:
        return _family_patterns_cache
    patterns: list[tuple[str, re.Pattern[str]]] = []
    for family, spec in _load_families().items():
        # مراجعة B2 R12: لم نعد نتخطّى العائلات المُستبعدة (excluded: true) —
        # تُبنى أنماطها أيضًا وتُرجع كاسم عائلة فعلي حين تُطابَق، بدل ترك
        # الوظيفة بلا أي تصنيف (family=None) فتختلط بفجوة معجم حقيقية.
        keywords = list(spec.get("keywords_en") or []) + list(spec.get("keywords_ar") or [])
        if not keywords:
            continue
        # مراجعة B2b: كل كلمة مفتاحية تمرّ بنفس تطبيع النص المُبحوث فيه
        # (_normalize_for_match) قبل بناء النمط، حتى تبقى المقارنة متسقة.
        norm_keywords = sorted(
            {nk for kw in keywords if (nk := _normalize_for_match(kw))}, key=len, reverse=True
        )
        if not norm_keywords:
            continue
        escaped = [re.escape(kw) for kw in norm_keywords]
        patterns.append((family, re.compile(r"\b(?:" + "|".join(escaped) + r")\b", re.IGNORECASE)))
    _family_patterns_cache = patterns
    return patterns


def classify_family(title: str | None, description: str | None = None) -> str | None:
    """يرجّع أول عائلة مهنية تُطابق معجم taxonomy_local.yaml، بحدود كلمة
    صريحة بعد تطبيع (مراجعة B2b) — العنوان أولًا، فإن لم يُطابق شيئًا (ملاذ
    أخير فقط) يُجرّب أول 300 حرف من الوصف وحده. قد تكون النتيجة اسم عائلة
    مُستبعدة (مثل out_of_scope — مراجعة B2 R12) — المستدعي مسؤول عن
    استثنائها من مقاييس "التصنيف الفعلي" حين يلزم."""
    patterns = _build_family_patterns()

    norm_title = _normalize_for_match(title)
    if norm_title:
        for family, pattern in patterns:
            if pattern.search(norm_title):
                return family

    norm_description = _normalize_for_match((description or "")[:DESCRIPTION_MATCH_CHARS])
    if norm_description:
        for family, pattern in patterns:
            if pattern.search(norm_description):
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
# مراجعة B2 R10: تجميع raw_jobs المجلوبة بنفس الجولة حسب هوية الإعلان
# ---------------------------------------------------------------------------


def _group_raw_jobs_by_identity(
    raw_jobs: list[dict], company_name: str | None, source_id: int | str | None
) -> list[dict]:
    """يُجمّع raw_jobs حسب dedup_key (الذي يعتمد الآن على apply_url وحده حين
    متوفر — مراجعة B2 R10) *قبل* أي إدراج بقاعدة البيانات. بعض المصادر
    (خصوصًا Workable لوكالات التوظيف كـEram Talent/Hudson Manpower) تُرجع
    نفس الوظيفة (نفس apply_url) مرارًا ضمن استجابة واحدة، مرة لكل مدينة
    "مرشّحة" — يجب أن تُصبح صفًا واحدًا بحقل `locations` يجمع كل المواقع
    المذكورة، لا صفًا منفصلًا لكل مدينة. دالة نقية بلا اتصال قاعدة بيانات —
    قابلة للاختبار مباشرة (core/tests/test_discovery_grouping.py).

    يُرجع قائمة عناصر بترتيب أول ظهور، كل عنصر:
        {dedup_key, raw_job (أول ظهور)، title, location_text (أول ظهور)،
         apply_url, locations (قائمة كل نصوص الموقع الفريدة المذكورة)}
    """
    groups: dict[str, dict] = {}
    order: list[str] = []
    for raw_job in raw_jobs:
        title = (raw_job.get("title") or "").strip()
        if not title:
            continue
        location_text = (raw_job.get("location") or "").strip()
        apply_url = raw_job.get("url")
        key = dedup_key(company_name, title, location_text or None, apply_url, source_id=source_id)
        entry = groups.get(key)
        if entry is None:
            entry = {
                "dedup_key": key,
                "raw_job": raw_job,
                "title": title,
                "location_text": location_text,
                "apply_url": apply_url,
                "locations": [],
            }
            groups[key] = entry
            order.append(key)
        if location_text and location_text not in entry["locations"]:
            entry["locations"].append(location_text)
    return [groups[k] for k in order]


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
                    SELECT s.id, s.company_id, s.source_type, s.source_url, s.region_filter,
                           c.name AS company_name
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
        region_filter = row["region_filter"] or "gcc"

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
        gcc_hits_this_source = 0

        # مراجعة B2 R10: تجميع raw_jobs حسب هوية الإعلان *قبل* الإدراج —
        # يمنع إدراج صف منفصل لكل مدينة "مرشّحة" يذكرها المصدر لنفس
        # apply_url فعليًا (انظر توثيق _group_raw_jobs_by_identity أعلاه).
        grouped_entries = _group_raw_jobs_by_identity(raw_jobs, company_name, source_id)

        try:
            with engine.begin() as conn:
                for entry in grouped_entries:
                    raw_job = entry["raw_job"]
                    title = entry["title"]
                    location_text = entry["location_text"]
                    apply_url = entry["apply_url"]
                    locations = entry["locations"] or ([location_text] if location_text else [])
                    d_key = entry["dedup_key"]

                    description = _extract_description(raw_job)
                    combined_text = "\n".join(filter(None, [title, location_text, description]))

                    cities = extract_cities(combined_text)
                    city = cities[0] if cities else None
                    years_min, _years_max = extract_years_required(combined_text)
                    seniority = extract_seniority(combined_text, title=title)
                    saudi_only = is_saudi_only(combined_text)
                    skills = extract_skills(combined_text)
                    family = classify_family(title, description)
                    apply_mode = classify_application_type(apply_url, source_type)

                    # مراجعة B2 R11: location_text البنيوي فقط يُستخدم حين
                    # متوفرًا — النص الإضافي (عنوان+وصف) لا يُمرّر إلا ليكون
                    # ملاذًا أخيرًا حين location_text فارغًا تمامًا (compute_region
                    # نفسها تُطبّق هذا الشرط داخليًا الآن).
                    country_code, out_of_region = compute_region(
                        location_text or None, f"{title}\n{description[:300]}"
                    )
                    if not out_of_region:
                        gcc_hits_this_source += 1

                    inserted = conn.execute(
                        text(
                            """
                            INSERT INTO jobs (
                                source_id, company_id, external_id, title, url, location,
                                locations, family, dedup_key, raw_json, company_name, city,
                                years_min, seniority, saudi_only, skills, apply_mode,
                                description_snippet, country_code, out_of_region
                            ) VALUES (
                                :source_id, :company_id, :external_id, :title, :url, :location,
                                :locations, :family, :dedup_key, :raw_json, :company_name, :city,
                                :years_min, :seniority, :saudi_only, :skills, :apply_mode,
                                :description_snippet, :country_code, :out_of_region
                            )
                            ON CONFLICT (dedup_key) DO UPDATE SET
                                locations = (
                                    SELECT COALESCE(jsonb_agg(DISTINCT loc), '[]'::jsonb) FROM (
                                        SELECT jsonb_array_elements_text(COALESCE(jobs.locations, '[]'::jsonb)) AS loc
                                        UNION
                                        SELECT jsonb_array_elements_text(EXCLUDED.locations) AS loc
                                    ) u
                                ),
                                last_seen_at = now()
                            RETURNING (xmax = 0) AS was_insert
                            """
                        ),
                        {
                            "source_id": source_id,
                            "company_id": company_id,
                            "external_id": str(raw_job.get("external_id"))
                            if raw_job.get("external_id") is not None
                            else None,
                            "title": title[:2000],
                            "url": apply_url,
                            "location": location_text[:255] if location_text else None,
                            "locations": json.dumps(locations, ensure_ascii=False),
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
                            "country_code": country_code,
                            "out_of_region": out_of_region,
                        },
                    ).first()

                    if inserted is not None and inserted[0]:
                        inserted_this_source += 1
                        if family:
                            family_counts[family] = family_counts.get(family, 0) + 1

                # مراجعة B2 R3/R4: تتبّع عدد الوظائف الخليجية بهذه الجولة لكل
                # مصدر، وعدّاد الجولات المتتالية بلا أي وظيفة خليجية —
                # تعطيل تلقائي عند بلوغه العتبة (مصادر region_filter='gcc' فقط).
                zero_gcc_expr = (
                    "CASE WHEN :gcc_hits = 0 THEN consecutive_zero_gcc_rounds + 1 ELSE 0 END"
                    if region_filter == "gcc"
                    else "consecutive_zero_gcc_rounds"
                )
                conn.execute(
                    text(
                        f"""
                        UPDATE sources SET
                            last_ok_at = :now,
                            last_error = NULL,
                            last_count = :count,
                            consecutive_errors = 0,
                            consecutive_zero_rounds = CASE WHEN :count = 0
                                THEN consecutive_zero_rounds + 1 ELSE 0 END,
                            saudi_hits = :gcc_hits,
                            consecutive_zero_gcc_rounds = {zero_gcc_expr},
                            avg_per_day = ROUND((COALESCE(avg_per_day, 0) * 0.8 + :count * 48 * 0.2)::numeric, 2),
                            updated_at = :now
                        WHERE id = :id
                        """
                    ),
                    {
                        "now": started_at,
                        "count": len(raw_jobs),
                        "gcc_hits": gcc_hits_this_source,
                        "id": source_id,
                    },
                )

                if region_filter == "gcc":
                    disable_row = conn.execute(
                        text(
                            """
                            SELECT consecutive_zero_gcc_rounds FROM sources WHERE id = :id
                            """
                        ),
                        {"id": source_id},
                    ).first()
                    if disable_row and disable_row[0] >= GCC_ZERO_ROUNDS_DISABLE_THRESHOLD:
                        conn.execute(
                            text(
                                """
                                UPDATE sources SET enabled = false, disabled_reason = 'no_gcc_jobs'
                                WHERE id = :id
                                """
                            ),
                            {"id": source_id},
                        )
                        logger.warning(
                            "تعطيل المصدر #%s تلقائيًا: 0 وظيفة خليجية عبر %s جولة متتالية",
                            source_id,
                            disable_row[0],
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
