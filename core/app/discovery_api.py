"""
Masar Core — نقاط نهاية إدارة الاكتشاف (B2، الدليل §9 المرحلة 2):

    POST /admin/sources                 إضافة مصدر (تحقّق بجلب فوري)
    GET  /admin/sources?active=true|false  سرد المصادر
    POST /admin/sources/{id}/disable    تعطيل يدوي
    POST /admin/sources/{id}/purge-jobs حذف وظائف مصدر مُلوِّث (يدوي عمدي)
    POST /admin/sources/{id}/enable     إعادة تفعيل يدوي
    GET  /admin/stats                   مقاييس الاكتشاف الكاملة
    POST /admin/discovery/run-now       جولة فورية بالخلفية (لا تنتظر)
    POST /admin/discovery/seed-sources  إعادة بذر data/sources_seed.csv يدويًا
    GET  /admin/quality-sample?n=50     عينة أحدث الوظائف بحقولها المستخرجة
    GET  /admin/dup-breakdown?limit=20  تشخيص أكثر مجموعات التكرار (لأي مصدر) خلال 24 ساعة
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text

from app import discovery
from app.auth import require_admin_token

logger = logging.getLogger("masar.discovery_api")

router = APIRouter(prefix="/admin", tags=["discovery"], dependencies=[Depends(require_admin_token)])


class SourceCreateRequest(BaseModel):
    company: str
    type: str
    url: str
    country: str | None = None
    terms_note: str | None = None


@router.post("/sources")
async def create_source(body: SourceCreateRequest) -> dict:
    """يتحقق بجلب فعلي فوري واحد (لا يُدرج أي وظيفة بجدول jobs)، ثم يُدرج/
    يحدّث صفّ المصدر بغض النظر عن نتيجة التحقق — مصدر يفشل الآن قد ينجح لاحقًا
    (مؤقت شبكة)، وعدّاد consecutive_errors في جولات الجامع سيتكفّل بتعطيله
    تلقائيًا إن استمر الفشل ثلاث مرات متتالية."""
    if body.type not in discovery.DISPATCH:
        raise HTTPException(status_code=400, detail=f"نوع مصدر غير مدعوم: {body.type}")

    try:
        count = discovery.validate_source(body.type, body.url)
        error: str | None = None
    except Exception as exc:  # noqa: BLE001 — نرجع الخطأ بالاستجابة، لا نرفع 500
        count = None
        error = str(exc)[:1000]

    engine = discovery.get_engine()
    with engine.begin() as conn:
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
            {"name": body.company, "country": body.country},
        ).first()
        company_id = company_row[0]

        source_row = conn.execute(
            text(
                """
                INSERT INTO sources (company_id, source_type, source_url, terms_note, enabled, last_error)
                VALUES (:company_id, :type, :url, :terms_note, true, :error)
                ON CONFLICT (source_url) DO UPDATE SET
                    terms_note = COALESCE(EXCLUDED.terms_note, sources.terms_note),
                    last_error = EXCLUDED.last_error
                RETURNING id
                """
            ),
            {
                "company_id": company_id,
                "type": body.type,
                "url": body.url,
                "terms_note": body.terms_note,
                "error": error,
            },
        ).first()

    return {
        "ok": error is None,
        "source_id": source_row[0],
        "company_id": company_id,
        "jobs_found": count,
        "error": error,
    }


@router.get("/sources")
async def list_sources(active: bool | None = Query(default=None)) -> list[dict]:
    engine = discovery.get_engine()
    sql = """
        SELECT s.id, c.name AS company, s.source_type AS type, s.source_url AS url,
               s.enabled, s.last_ok_at, s.last_error, s.last_count, s.avg_per_day,
               s.consecutive_errors, s.consecutive_zero_rounds, s.disabled_reason,
               s.terms_note, c.country
        FROM sources s JOIN companies c ON c.id = s.company_id
    """
    params: dict = {}
    if active is not None:
        sql += " WHERE s.enabled = :active"
        params["active"] = active
    sql += " ORDER BY s.id"
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
    return [dict(r) for r in rows]


@router.post("/sources/{source_id}/disable")
async def disable_source(source_id: int) -> dict:
    engine = discovery.get_engine()
    with engine.begin() as conn:
        result = conn.execute(
            text(
                """
                UPDATE sources SET enabled = false,
                    disabled_reason = 'تعطيل يدوي عبر /admin/sources/{id}/disable'
                WHERE id = :id RETURNING id
                """
            ),
            {"id": source_id},
        ).first()
    if not result:
        raise HTTPException(status_code=404, detail="مصدر غير موجود")
    return {"ok": True, "id": source_id, "enabled": False}


@router.post("/sources/{source_id}/purge-jobs")
async def purge_source_jobs(source_id: int) -> dict:
    """يحذف كل الوظائف التي أدرجها هذا المصدر تحديدًا من jobs — للاستخدام
    عند تبيّن أن مصدرًا (مُعطَّلًا عادة) لوّث الجدول بوظائف غير مناسبة (مثال
    B2: Jobgether وكالة إعادة نشر تكرّر نفس المسمى بمدن/دول مختلفة بلا مدينة
    مستخرجة، ما ضخّم dup_ratio_24h زورًا). لا يمسّ صفّ المصدر نفسه ولا صحته —
    فقط صفوف jobs المرتبطة به. لا يُفعَّل تلقائيًا؛ استدعاء يدوي عمدي فقط."""
    engine = discovery.get_engine()
    with engine.begin() as conn:
        result = conn.execute(
            text("DELETE FROM jobs WHERE source_id = :id"), {"id": source_id}
        )
    return {"ok": True, "source_id": source_id, "deleted": result.rowcount}


@router.post("/sources/{source_id}/enable")
async def enable_source(source_id: int) -> dict:
    engine = discovery.get_engine()
    with engine.begin() as conn:
        result = conn.execute(
            text(
                """
                UPDATE sources SET enabled = true, disabled_reason = NULL, consecutive_errors = 0
                WHERE id = :id RETURNING id
                """
            ),
            {"id": source_id},
        ).first()
    if not result:
        raise HTTPException(status_code=404, detail="مصدر غير موجود")
    return {"ok": True, "id": source_id, "enabled": True}


@router.get("/stats")
async def stats() -> dict:
    engine = discovery.get_engine()
    since_24h = datetime.now(timezone.utc) - timedelta(hours=24)
    with engine.connect() as conn:
        jobs_total = conn.execute(text("SELECT count(*) FROM jobs")).scalar()
        jobs_new_24h = conn.execute(
            text("SELECT count(*) FROM jobs WHERE first_seen_at >= :since"), {"since": since_24h}
        ).scalar()
        by_family_rows = conn.execute(
            text(
                """
                SELECT COALESCE(family, 'غير مصنّف') AS family, count(*) AS c
                FROM jobs WHERE first_seen_at >= :since
                GROUP BY family ORDER BY c DESC
                """
            ),
            {"since": since_24h},
        ).all()
        jobs_new_24h_by_family = {r[0]: r[1] for r in by_family_rows}

        sources_active = conn.execute(text("SELECT count(*) FROM sources WHERE enabled = true")).scalar()
        disabled_rows = conn.execute(
            text(
                """
                SELECT s.id, c.name AS company, s.source_type, s.disabled_reason
                FROM sources s JOIN companies c ON c.id = s.company_id
                WHERE s.enabled = false
                ORDER BY s.id
                """
            )
        ).mappings().all()
        companies_total = conn.execute(text("SELECT count(*) FROM companies")).scalar()

        # dup_ratio_24h: نسبة صفوف آخر 24 ساعة التي تتكرر فيها (شركة+مسمى+مدينة)
        # المطبَّعان — تقريب مستقل عن dedup_key نفسه (الذي يفترض منع هذا
        # التكرار أصلًا)، يكشف أي تسرّب فعلي (رابط منصّة بمعرّفين مختلفين
        # لنفس الإعلان، أو اختلاف طفيف بالعنوان الخام). المدينة مُضمَّنة هنا
        # عمدًا (تطابقًا مع مكوّنات dedup_key الفعلية) لأن نفس الشركة قد
        # تنشر نفس المسمى الوظيفي بمدن مختلفة فعليًا (وظائف حقيقية منفصلة
        # وليست تكرارًا) — تجاهل المدينة كان يُضخّم هذا المقياس زورًا.
        dup_row = conn.execute(
            text(
                """
                WITH recent AS (
                    SELECT lower(regexp_replace(coalesce(company_name, ''), '[^a-z0-9؀-ۿ]+', ' ', 'gi'))
                           || '::' ||
                           lower(regexp_replace(coalesce(title, ''), '[^a-z0-9؀-ۿ]+', ' ', 'gi'))
                           || '::' ||
                           lower(regexp_replace(coalesce(city, ''), '[^a-z0-9؀-ۿ]+', ' ', 'gi')) AS k
                    FROM jobs WHERE first_seen_at >= :since
                ),
                counted AS (
                    SELECT k, count(*) AS n FROM recent GROUP BY k
                )
                SELECT
                    COALESCE(SUM(GREATEST(n - 1, 0)), 0)::float AS dup_rows,
                    COALESCE(SUM(n), 0)::float AS total_rows
                FROM counted
                """
            ),
            {"since": since_24h},
        ).first()
        dup_rows, total_rows = dup_row[0], dup_row[1]
        dup_ratio_24h = (dup_rows / total_rows) if total_rows else 0.0

        last_round = conn.execute(
            text(
                """
                SELECT started_at, finished_at, sources_processed, sources_ok,
                       sources_error, jobs_fetched, jobs_new
                FROM discovery_rounds ORDER BY id DESC LIMIT 1
                """
            )
        ).mappings().first()

    last_round_at = None
    last_round_seconds = None
    last_round_out = None
    if last_round:
        last_round_out = dict(last_round)
        if last_round["finished_at"]:
            last_round_at = last_round["finished_at"].isoformat()
            last_round_seconds = (last_round["finished_at"] - last_round["started_at"]).total_seconds()

    return {
        "jobs_total": jobs_total,
        "jobs_new_24h": jobs_new_24h,
        "jobs_new_24h_by_family": jobs_new_24h_by_family,
        "sources_active": sources_active,
        "sources_disabled": [dict(r) for r in disabled_rows],
        "companies_total": companies_total,
        "dup_ratio_24h": round(dup_ratio_24h, 4),
        "last_round_at": last_round_at,
        "last_round_seconds": last_round_seconds,
        "last_round": last_round_out,
    }


@router.get("/discovery/probe")
async def probe_source(type: str = Query(...), url: str = Query(...)) -> dict:  # noqa: A002
    """نقطة تحقق مؤقتة (بلا كتابة بقاعدة البيانات إطلاقًا) — تُستخدم أثناء بناء
    data/sources_seed.csv للتحقق من عشرات المرشّحين بسرعة قبل إضافتهم للملف،
    بدل تلويث جدولي companies/sources بمحاولات فاشلة عبر POST /admin/sources.
    نفس منطق التحقق (جلب فعلي واحد فقط، بلا إدراج وظائف)."""
    if type not in discovery.DISPATCH:
        raise HTTPException(status_code=400, detail=f"نوع مصدر غير مدعوم: {type}")
    try:
        count = discovery.validate_source(type, url)
        return {"ok": True, "type": type, "url": url, "jobs_found": count}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "type": type, "url": url, "error": str(exc)[:500]}


@router.post("/discovery/run-now")
async def run_now(background_tasks: BackgroundTasks) -> dict:
    """يُشغّل جولة كاملة بالخلفية (FastAPI threadpool) ويرجع فورًا — استخدم
    GET /admin/stats بعد ذلك لمعرفة النتيجة (last_round_at/last_round_seconds)."""
    background_tasks.add_task(discovery.run_round)
    return {"ok": True, "started": True}


@router.post("/discovery/seed-sources")
async def seed_sources_now() -> dict:
    """يعيد قراءة data/sources_seed.csv ويطبّق upsert idempotent — مفيد
    للتحقق اليدوي بعد تحديث ملف البذر بلا انتظار إعادة تشغيل core-scheduler."""
    return discovery.seed_sources()


@router.get("/dup-breakdown")
async def dup_breakdown(limit: int = Query(default=20, ge=1, le=200)) -> list[dict]:
    """تشخيص dup_ratio_24h: يُرجع أكثر مجموعات (شركة+مسمى+مدينة) المطبَّعة
    تكرارًا خلال آخر 24 ساعة، مع اسم المصدر ومعرّفه — لتحديد أي مصدر يسبب
    التضخّم دون الحاجة لاستعلام psql يدوي (القناة عبر ops للقراءة فقط وبطيئة)."""
    engine = discovery.get_engine()
    since_24h = datetime.now(timezone.utc) - timedelta(hours=24)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                WITH recent AS (
                    SELECT j.source_id, s.source_type, c.name AS company_src,
                           lower(regexp_replace(coalesce(j.company_name, ''), '[^a-z0-9؀-ۿ]+', ' ', 'gi'))
                           || '::' ||
                           lower(regexp_replace(coalesce(j.title, ''), '[^a-z0-9؀-ۿ]+', ' ', 'gi'))
                           || '::' ||
                           lower(regexp_replace(coalesce(j.city, ''), '[^a-z0-9؀-ۿ]+', ' ', 'gi')) AS k
                    FROM jobs j
                    JOIN sources s ON s.id = j.source_id
                    JOIN companies c ON c.id = s.company_id
                    WHERE j.first_seen_at >= :since
                )
                SELECT k, source_id, source_type, company_src, count(*) AS n
                FROM recent
                GROUP BY k, source_id, source_type, company_src
                HAVING count(*) > 1
                ORDER BY n DESC
                LIMIT :limit
                """
            ),
            {"since": since_24h, "limit": limit},
        ).mappings().all()
    return [dict(r) for r in rows]


@router.get("/quality-sample")
async def quality_sample(n: int = Query(default=50, ge=1, le=200)) -> list[dict]:
    engine = discovery.get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT j.id, c.name AS company, j.title, j.city, j.location, j.years_min,
                       j.seniority, j.saudi_only, j.family, j.skills, j.apply_mode, j.url,
                       j.first_seen_at, j.description_snippet
                FROM jobs j JOIN companies c ON c.id = j.company_id
                ORDER BY j.first_seen_at DESC
                LIMIT :n
                """
            ),
            {"n": n},
        ).mappings().all()
    return [dict(r) for r in rows]
