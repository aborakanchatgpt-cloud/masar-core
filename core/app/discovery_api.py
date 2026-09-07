"""
Masar Core — نقاط نهاية إدارة الاكتشاف (B2، الدليل §9 المرحلة 2).

مراجعة B2 (ACCEPT-WITH-FIXES) — إضافات/تعديلات هذا الملف:
    R1: POST /admin/discovery/backfill-locations — يعيد استخراج city/location
        من raw_json المخزّن لكل صف (يُصلح صفوف Workable التي كانت بلا موقع
        قبل إصلاح app/collectors/workable.py)، ويعيد حساب country_code/
        out_of_region لكل صفوف jobs القديمة (كانت كلها out_of_region=true
        افتراضيًا من الترحيل 0003 حتى تُعاد فعليًا بهذه النقطة).
    R3/R4: /admin/stats الآن يفصل jobs_in_region (خليجي فعليًا حسب compute_region)
        عن jobs_total الخام، ويُضيف jobs_sa (السعودية تحديدًا) ونسبة gcc_share،
        ويُظهر مصادر region_filter='gcc' القريبة من التعطيل التلقائي
        (consecutive_zero_gcc_rounds > 0 لكن دون العتبة بعد).
    R5: dup_ratio_24h يُحسب الآن فقط على صفوف jobs.out_of_region = false
        (كان يشمل ضجيج المصادر خارج النطاق سابقًا).
    R6: family_classified_pct_in_region — نسبة التصنيف الفعلية على الصفوف
        داخل النطاق فقط (كانت الحسابات القديمة تشمل كل الصفوف بلا تمييز).

    POST /admin/sources                 إضافة مصدر (تحقّق بجلب فوري)
    GET  /admin/sources?active=true|false  سرد المصادر
    POST /admin/sources/{id}/disable    تعطيل يدوي
    POST /admin/sources/{id}/purge-jobs حذف وظائف مصدر مُلوّث (يدوي عمدي فقط —
                                         لا يُستدعى تلقائيًا؛ الاستبعاد التلقائي
                                         للمنطقة الآن عبر jobs.out_of_region،
                                         بلا حذف صفوف — "لا نحذف من jobs أبدًا،
                                         نُعلّم فقط")
    POST /admin/sources/{id}/enable     إعادة تفعيل يدوي
    GET  /admin/stats                   مقاييس الاكتشاف الكاملة (مُعاد بناؤها R3-R6)
    POST /admin/discovery/run-now       جولة فورية بالخلفية (لا تنتظر)
    POST /admin/discovery/seed-sources  إعادة بذر data/sources_seed.csv يدويًا
    POST /admin/discovery/backfill-locations  إعادة استخراج الموقع/المنطقة لكل الصفوف (R1، لمرة واحدة)
    POST /admin/discovery/dedupe-cleanup      تنظيف يدوي لمرة واحدة لصفوف مكرّرة تراكمت
                                         بسبب تغيّر صيغة dedup_key أثناء المراجعة (استثناء موثّق آخر)
    GET  /admin/quality-sample?n=50     عينة أحدث الوظائف بحقولها المستخرجة
    GET  /admin/dup-breakdown?limit=20  تشخيص أكثر مجموعات التكرار (داخل النطاق فقط الآن) خلال 24 ساعة
    GET  /admin/unclassified-sample?limit=30  أكثر عناوين الوظائف (داخل النطاق) غير المصنّفة عائليًا تكرارًا (R6)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text

from app import discovery
from app.auth import require_admin_token
from app.collectors.field_extractor import compute_region, extract_cities

logger = logging.getLogger("masar.discovery_api")

router = APIRouter(prefix="/admin", tags=["discovery"], dependencies=[Depends(require_admin_token)])

BACKFILL_BATCH_SIZE = 500


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
               s.terms_note, c.country, s.region_filter, s.saudi_hits,
               s.consecutive_zero_gcc_rounds
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
    """يحذف كل الوظائف التي أدرجها هذا المصدر تحديدًا من jobs — أداة يدوية
    استثنائية فقط (مثال تاريخي: Jobgether وكالة إعادة نشر ضخّمت dup_ratio_24h
    زورًا قبل أن يُوجَد jobs.out_of_region أصلًا). بعد مراجعة B2 R3، الاستبعاد
    القياسي لمصدر/وظيفة خارج النطاق الجغرافي يتم عبر العلم jobs.out_of_region
    (بلا حذف أي صفّ) — هذه النقطة تبقى فقط لحالات تلوّث بيانات استثنائية
    فعلية، ولا تُستدعى تلقائيًا من أي منطق بالنظام."""
    engine = discovery.get_engine()
    with engine.begin() as conn:
        result = conn.execute(
            text("DELETE FROM jobs WHERE source_id = :id"), {"id": source_id}
        )
    return {"ok": True, "source_id": source_id, "deleted": result.rowcount}


@router.post("/discovery/dedupe-cleanup")
async def dedupe_cleanup() -> dict:
    """أداة يدوية لمرة واحدة — استثناء موثّق آخر على مبدأ "لا نحذف من jobs
    أبدًا" (كـ purge-jobs أعلاه): تُصلح تراكم صفوف مكرّرة فعليًا نتج عن
    تغيّر صيغة dedup_key عدّة مرات أثناء مراجعة B2 (من platform:external_id،
    إلى sha1 بالمدينة المُستخرَجة عبر NLP وهي غير ثابتة بين الجلبات، إلى
    sha1 بـ location_text الخام الثابت) — كل تغيّر صيغة يجعل
    ON CONFLICT (dedup_key) يفشل في مطابقة الصفوف المُدرجة سابقًا بصيغة
    مختلفة لنفس الوظيفة الحقيقية، فتتراكم نسخ لها. معيار "نفس الوظيفة" هنا
    الأكثر موثوقية المتاح للتنظيف الرجعي: (company_name, title, url) متطابقة
    حرفيًا (وليس dedup_key نفسه، وهو أصل المشكلة) — يُبقي أقدم صفّ (أصغر id)
    لكل مجموعة ويحذف الباقي. لا تُستدعى تلقائيًا من أي منطق بالنظام؛ استدعاء
    يدوي واحد كافٍِ بعد إصلاح discovery.py (استخدام location_text بدل city)
    لتنظيف التراكم السابق فقط — الجولات القادمة لن تُنتج مكرّرات جديدة."""
    engine = discovery.get_engine()
    with engine.begin() as conn:
        result = conn.execute(
            text(
                """
                DELETE FROM jobs WHERE id IN (
                    SELECT id FROM (
                        SELECT id, ROW_NUMBER() OVER (
                            PARTITION BY company_name, title, url ORDER BY id
                        ) AS rn
                        FROM jobs WHERE url IS NOT NULL
                    ) t WHERE rn > 1
                )
                """
            )
        )
    logger.warning(
        "dedupe-cleanup: حُذف %s صف مكرّر (company_name+title+url متطابقة حرفيًا)",
        result.rowcount,
    )
    return {"ok": True, "deleted": result.rowcount}


@router.post("/sources/{source_id}/enable")
async def enable_source(source_id: int) -> dict:
    engine = discovery.get_engine()
    with engine.begin() as conn:
        result = conn.execute(
            text(
                """
                UPDATE sources SET enabled = true, disabled_reason = NULL, consecutive_errors = 0,
                    consecutive_zero_gcc_rounds = 0
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

        # مراجعة B2 R3/R4: العدد الفعلي "داخل النطاق" (خليجي بالكامل حسب
        # compute_region — سعودية/إمارات/قطر/الكويت/البحرين/عُمان، أو ريموت
        # مع ذكر صريح لدولة خليجية) — هذا الرقم هو المعتمَد لقبول B2، لا jobs_total.
        jobs_in_region = conn.execute(
            text("SELECT count(*) FROM jobs WHERE out_of_region = false")
        ).scalar()
        jobs_sa = conn.execute(
            text("SELECT count(*) FROM jobs WHERE out_of_region = false AND country_code = 'SA'")
        ).scalar()
        # gcc_share: من صفوف "داخل النطاق"، كم فعليًا لها country_code ضمن
        # مجموعة الخليج الصريحة (وليس فقط ريموت-مع-ذكر-خليجي بلا country_code
        # محدّد) — بالبناء يُفترض قريبًا من 100% لأن compute_region لا يُدخل
        # صفًا بالنطاق أصلًا إلا إن كان خليجيًا أو ريموت+ذكر خليجي.
        gcc_country_hits = conn.execute(
            text(
                """
                SELECT count(*) FROM jobs
                WHERE out_of_region = false
                  AND country_code IN ('SA','AE','QA','KW','BH','OM')
                """
            )
        ).scalar()
        gcc_share = (gcc_country_hits / jobs_in_region) if jobs_in_region else 0.0

        jobs_new_24h = conn.execute(
            text("SELECT count(*) FROM jobs WHERE first_seen_at >= :since"), {"since": since_24h}
        ).scalar()
        by_family_rows = conn.execute(
            text(
                """
                SELECT COALESCE(family, 'غير مصنّف') AS family, count(*) AS c
                FROM jobs WHERE first_seen_at >= :since AND out_of_region = false
                GROUP BY family ORDER BY c DESC
                """
            ),
            {"since": since_24h},
        ).all()
        jobs_new_24h_in_region_by_family = {r[0]: r[1] for r in by_family_rows}

        # مراجعة B2 R6: نسبة التصنيف العائلي الفعلية — على كل صفوف "داخل
        # النطاق" (وليس فقط آخر 24 ساعة) لأنها المقياس المطلوب لقبول B2
        # (≥80% من الوظائف الخليجية مصنّفة عائليًا).
        family_row = conn.execute(
            text(
                """
                SELECT
                    count(*) FILTER (WHERE family IS NOT NULL)::float AS classified,
                    count(*)::float AS total
                FROM jobs WHERE out_of_region = false
                """
            )
        ).first()
        family_classified_pct_in_region = (
            (family_row[0] / family_row[1]) if family_row and family_row[1] else 0.0
        )

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
        # مصادر gcc قريبة من التعطيل التلقائي (جولة صفرية واحدة حتى الآن،
        # ستُعطّل تلقائيًا بعد جولة صفرية ثانية متتالية) — مفيدة لمتابعة
        # منطق R3(c) قبل وقوعه، لا بعده فقط.
        near_disable_rows = conn.execute(
            text(
                """
                SELECT s.id, c.name AS company, s.source_type, s.consecutive_zero_gcc_rounds
                FROM sources s JOIN companies c ON c.id = s.company_id
                WHERE s.enabled = true AND s.region_filter = 'gcc'
                  AND s.consecutive_zero_gcc_rounds > 0
                ORDER BY s.consecutive_zero_gcc_rounds DESC, s.id
                """
            )
        ).mappings().all()
        companies_total = conn.execute(text("SELECT count(*) FROM companies")).scalar()

        # dup_ratio_24h (مراجعة B2 R5): يُحسب الآن فقط على صفوف داخل النطاق —
        # كان سابقًا يشمل كل صفوف آخر 24 ساعة بلا تمييز جغرافي، فيضخّمه ضجيج
        # مصادر عالمية لا علاقة لها بالسوق المستهدف. مفتاح التطابق (شركة+
        # مسمى+مدينة/موقع خام) تقريب مستقل عن dedup_key نفسه — يكشف أي تسرّب
        # فعلي رغم منطق ON CONFLICT (dedup_key).
        dup_row = conn.execute(
            text(
                """
                WITH recent AS (
                    SELECT lower(regexp_replace(coalesce(company_name, ''), '[^a-z0-9؀-ۿ]+', ' ', 'gi'))
                           || '::' ||
                           lower(regexp_replace(coalesce(title, ''), '[^a-z0-9؀-ۿ]+', ' ', 'gi'))
                           || '::' ||
                           lower(regexp_replace(coalesce(nullif(city, ''), location, ''), '[^a-z0-9؀-ۿ]+', ' ', 'gi')) AS k
                    FROM jobs WHERE first_seen_at >= :since AND out_of_region = false
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
        dup_ratio_24h_in_region = (dup_rows / total_rows) if total_rows else 0.0

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
        "jobs_in_region": jobs_in_region,
        "jobs_sa": jobs_sa,
        "gcc_share_of_in_region": round(gcc_share, 4),
        "jobs_new_24h": jobs_new_24h,
        "jobs_new_24h_in_region_by_family": jobs_new_24h_in_region_by_family,
        "family_classified_pct_in_region": round(family_classified_pct_in_region, 4),
        "sources_active": sources_active,
        "sources_disabled": [dict(r) for r in disabled_rows],
        "sources_near_gcc_auto_disable": [dict(r) for r in near_disable_rows],
        "companies_total": companies_total,
        "dup_ratio_24h_in_region": round(dup_ratio_24h_in_region, 4),
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


def _reextract_location(source_type: str, raw: dict) -> str | None:
    """يعيد استخراج نص الموقع من raw_json الخام حسب نوع المصدر — نفس منطق
    الجامع المُصلَح لكل نوع، لكن مطبّق رجعيًا على صفوف jobs الموجودة أصلًا
    (مراجعة B2 R1). Workable تحديدًا: raw_json لا يحمل مفتاح 'location' إطلاقًا
    (كان هذا سبب الفراغ) — الحقول الحقيقية city/region/state/country/telecommuting
    مباشرة على الجذر."""
    if not isinstance(raw, dict):
        return None
    if source_type == "workable":
        parts = [raw.get("city"), raw.get("region") or raw.get("state"), raw.get("country")]
        joined = ", ".join(p.strip() for p in parts if isinstance(p, str) and p.strip())
        if joined:
            return joined
        if raw.get("telecommuting"):
            return "Remote"
        return None
    # مصادر أخرى (sitemap_jsonld خصوصًا) قد تحمل نص موقع مباشرة بعقدة raw
    loc = raw.get("location")
    if isinstance(loc, str) and loc.strip():
        return loc.strip()
    job_location = raw.get("jobLocation")
    if isinstance(job_location, dict):
        address = job_location.get("address")
        if isinstance(address, dict):
            parts = [address.get("addressLocality"), address.get("addressCountry")]
            joined = ", ".join(p for p in parts if isinstance(p, str) and p.strip())
            if joined:
                return joined
    return None


@router.post("/discovery/backfill-locations")
async def backfill_locations() -> dict:
    """مراجعة B2 R1/R6 — عملية لمرة واحدة، تُستدعى يدويًا فقط (لا جدولة تلقائية):
    تمشي على كل صفوف jobs دفعة دفعة (BACKFILL_BATCH_SIZE)، تعيد استخراج
    location/city من raw_json المخزّن حين تكون فارغة (يُصلح فعليًا صفوف
    Workable التي رصدتها المراجعة)، ثم تعيد حساب country_code/out_of_region
    لكل صفّ (المطلوب أصلًا لأن الترحيل 0003 وضع كل الصفوف القديمة على
    out_of_region=true افتراضيًا حتى تُعاد فعليًا هنا)، وأيضًا تعيد حساب family
    لأي صفّ family IS NULL (R6: المعجم تَوسّع بعد إدراج هذه الصفوف أصلًا،
    فالإدراج التاريخي لم يستفد من العائلات السبع الجديدة ولا من مطابقة حدود
    الكلمة — بلا هذه الخطوة تبقى نسبة التصنيف على الصفوف القديمة صفرًا تقريبًا
    رغم توسعة data/taxonomy_local.yaml). لا تحذف ولا تُدرج أي صفّ — تُحدّث فقط."""
    engine = discovery.get_engine()
    total = 0
    location_backfilled = 0
    family_backfilled = 0
    last_id = 0
    while True:
        with engine.begin() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT j.id, j.title, j.location, j.city, j.description_snippet,
                           j.raw_json, j.family, s.source_type
                    FROM jobs j JOIN sources s ON s.id = j.source_id
                    WHERE j.id > :last_id
                    ORDER BY j.id
                    LIMIT :batch
                    """
                ),
                {"last_id": last_id, "batch": BACKFILL_BATCH_SIZE},
            ).mappings().all()
            if not rows:
                break

            for row in rows:
                last_id = row["id"]
                total += 1
                raw = row["raw_json"]
                if isinstance(raw, str):
                    try:
                        raw = json.loads(raw)
                    except (ValueError, TypeError):
                        raw = {}
                raw = raw or {}

                location_text = row["location"]
                city = row["city"]
                if not location_text:
                    new_location = _reextract_location(row["source_type"], raw)
                    if new_location:
                        location_text = new_location
                        location_backfilled += 1

                new_city = city
                if not new_city and location_text:
                    found_cities = extract_cities(location_text)
                    new_city = found_cities[0] if found_cities else city

                description = row["description_snippet"] or ""
                country_code, out_of_region = compute_region(
                    location_text or None, f"{row['title'] or ''}\n{description[:300]}"
                )

                new_family = row["family"]
                if new_family is None:
                    computed_family = discovery.classify_family(row["title"], description)
                    if computed_family:
                        new_family = computed_family
                        family_backfilled += 1

                conn.execute(
                    text(
                        """
                        UPDATE jobs SET
                            location = :location,
                            city = :city,
                            country_code = :country_code,
                            out_of_region = :out_of_region,
                            family = :family
                        WHERE id = :id
                        """
                    ),
                    {
                        "location": location_text,
                        "city": new_city[:120] if new_city else None,
                        "country_code": country_code,
                        "out_of_region": out_of_region,
                        "family": new_family,
                        "id": row["id"],
                    },
                )

    logger.info(
        "backfill-locations: %s صف مُعالَج، %s موقع أُعيد استخراجه من raw_json، %s عائلة أُعيد تصنيفها",
        total,
        location_backfilled,
        family_backfilled,
    )
    return {
        "ok": True,
        "total_rows": total,
        "location_backfilled": location_backfilled,
        "family_backfilled": family_backfilled,
    }


@router.get("/dup-breakdown")
async def dup_breakdown(limit: int = Query(default=20, ge=1, le=200)) -> list[dict]:
    """تشخيص dup_ratio_24h_in_region (مراجعة B2 R5: داخل النطاق فقط الآن):
    يُرجع أكثر مجموعات (شركة+مسمى+مدينة) المطبّعة تكرارًا خلال آخر 24 ساعة،
    مع اسم المصدر ومعرّفه."""
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
                           lower(regexp_replace(coalesce(nullif(j.city, ''), j.location, ''), '[^a-z0-9؀-ۿ]+', ' ', 'gi')) AS k
                    FROM jobs j
                    JOIN sources s ON s.id = j.source_id
                    JOIN companies c ON c.id = s.company_id
                    WHERE j.first_seen_at >= :since AND j.out_of_region = false
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
async def quality_sample(n: int = Query(default=50, ge=1, le=200), in_region_only: bool = Query(default=False)) -> list[dict]:
    engine = discovery.get_engine()
    sql = """
        SELECT j.id, c.name AS company, j.title, j.city, j.location, j.country_code,
               j.out_of_region, j.years_min, j.seniority, j.saudi_only, j.family,
               j.skills, j.apply_mode, j.url, j.first_seen_at, j.description_snippet
        FROM jobs j JOIN companies c ON c.id = j.company_id
    """
    params: dict = {"n": n}
    if in_region_only:
        sql += " WHERE j.out_of_region = false"
    sql += " ORDER BY j.first_seen_at DESC LIMIT :n"
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
    return [dict(r) for r in rows]


@router.get("/unclassified-sample")
async def unclassified_sample(limit: int = Query(default=30, ge=1, le=200)) -> list[dict]:
    """مراجعة B2 R6: أكثر عناوين الوظائف (داخل النطاق فقط) غير المصنّفة
    عائليًا تكرارًا — يُستخدم لبناء/توسعة data/taxonomy_local.yaml بدل تخمين
    الكلمات المفتاحية الناقصة."""
    engine = discovery.get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT title, count(*) AS n
                FROM jobs
                WHERE out_of_region = false AND family IS NULL AND title IS NOT NULL
                GROUP BY title
                ORDER BY n DESC, title
                LIMIT :limit
                """
            ),
            {"limit": limit},
        ).mappings().all()
    return [dict(r) for r in rows]
