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

مراجعة B2 (الدورة الثانية، docs/reports/B2-review-2.md) — R10/R11/R12/R14:
    R10: POST /admin/discovery/merge-locations-cleanup — تنظيف رجعي لمرة
        واحدة: يدمج صفوف jobs المكرّرة فعليًا (نفس apply_url المطبّع لنفس
        المصدر، أو نفس company+title+location حين لا يوجد url) بعد تغيّر
        صيغة dedup_key (R10) — يُبقي أقدم صفّ ويُوحّد `locations`، ثم يعيد
        حساب dedup_key لكل الصفوف الباقية بالصيغة الجديدة.
    R11: POST /admin/discovery/recompute-region — يعيد حساب country_code/
        out_of_region لكل صفوف jobs بمنطق compute_region المُصلَح (لا رجوع
        لنص العنوان/الوصف إلا حين location_text فارغ تمامًا).
    R12: family_classified_pct_in_region يستثني الآن العائلات المُستبعدة
        عمدًا (sales_excluded) من المقام لا فقط من البسط — كانت تُحسب ضمن
        "غير مصنّف" رغم استبعادها المتعمّد.
    R14: dup_ratio_24h_in_region أُعيد تعريفه ليعتمد على apply_url المطبّع
        (هوية الإعلان الحقيقية بعد R10) بدل تجميع (شركة+مسمى+مدينة) الأخشن.

    POST /admin/sources                 إضافة مصدر (تحقّق بجلب فوري)
    GET  /admin/sources?active=true|false  سرد المصادر
    POST /admin/sources/{id}/disable    تعطيل يدوي
    POST /admin/sources/{id}/purge-jobs حذف وظائف مصدر مُلوّث (يدوي عمدي فقط —
                                         لا يُستدعى تلقائيًا؛ الاستبعاد التلقائي
                                         للمنطقة الآن عبر jobs.out_of_region،
                                         بلا حذف صفوف — "لا نحذف من jobs أبدًا،
                                         نُعلّم فقط")
    POST /admin/sources/{id}/enable     إعادة تفعيل يدوي
    GET  /admin/stats                   مقاييس الاكتشاف الكاملة (مُعاد بناؤها R3-R6، R12/R14)
    POST /admin/discovery/run-now       جولة فورية بالخلفية (لا تنتظر)
    POST /admin/discovery/seed-sources  إعادة بذر data/sources_seed.csv يدويًا
    POST /admin/discovery/backfill-locations  إعادة استخراج الموقع/المنطقة لكل الصفوف (R1، لمرة واحدة)
    POST /admin/discovery/dedupe-cleanup      تنظيف يدوي لمرة واحدة (تاريخي — راجع merge-locations-cleanup لما بعد R10)
    POST /admin/discovery/merge-locations-cleanup  تنظيف رجعي لمرة واحدة لتكرار apply_url متعدد المواقع (R10)
    POST /admin/discovery/recompute-region    إعادة حساب المنطقة الجغرافية لكل الصفوف (R11، لمرة واحدة)
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
from app.collectors.normalizer import company_key, dedup_key, normalize_apply_url, normalize_text

logger = logging.getLogger("masar.discovery_api")

router = APIRouter(prefix="/admin", tags=["discovery"], dependencies=[Depends(require_admin_token)])

BACKFILL_BATCH_SIZE = 500

# مراجعة B2 R12: عائلات مُستبعدة عمدًا من مقياس "التصنيف الفعلي" — راجع
# discovery.EXCLUDED_FAMILY_NAMES (نفس القيمة، مكرّرة هنا كسلسلة SQL جاهزة
# لأن استعلامات هذا الملف نصّية مباشرة).
# مراجعة B2b: sales_excluded أُعيدت تسميته out_of_scope بـtaxonomy_local.yaml؛
# الاسمان معًا هنا للتوافق الرجعي مع أي صفّ قديم لم يُعِد reclassify تصنيفه.
_EXCLUDED_FAMILIES_SQL_LIST = "('out_of_scope', 'sales_excluded')"


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
    زورًا قبل أن يُوجد jobs.out_of_region أصلًا). بعد مراجعة B2 R3، الاستبعاد
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
    """أداة يدوية لمرة واحدة (تاريخية — قبل R10) — استثناء موثّق آخر على مبدأ
    "لا نحذف من jobs أبدًا" (كـ purge-jobs أعلاه): أصلحت تراكم صفوف مكرّرة
    نتج عن تغيّر صيغة dedup_key من platform:external_id إلى sha1 بالمدينة
    المُستخرَجة عبر NLP، إلى sha1 بـlocation_text الخام (R6). معيار "نفس
    الوظيفة" هنا: (company_name, title, url) متطابقة حرفيًّا — يُبقي أقدم
    صفّ ويحذف الباقي. **لمعالجة التكرار المتبقي الأكبر (تعدّد المواقع لنفس
    apply_url — R10) استخدم POST /admin/discovery/merge-locations-cleanup
    بدلًا من هذه النقطة.**"""
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
        "dedupe-cleanup: حُذف %s صف مكرّر (company_name+title+url متطابقة حرفيًّا)",
        result.rowcount,
    )
    return {"ok": True, "deleted": result.rowcount}


@router.post("/discovery/merge-locations-cleanup")
async def merge_locations_cleanup() -> dict:
    """مراجعة B2 R10 — أداة يدوية لمرة واحدة (استثناء موثّق آخر على مبدأ
    "لا نحذف من jobs أبدًا"، بنفس نمط dedupe-cleanup/purge-jobs): تدمج صفوف
    jobs التي تبيّن أنها نفس الإعلان الحقيقي مكرّرًا (مرة لكل مدينة مذكورة
    بالمصدر) بموجب هوية الإعلان الجديدة:

        url متوفر:  (source_id, normalize_apply_url(url))
        بلا url:    (company_key, normalize(title), normalize(location))

    لكل مجموعة فيها أكثر من صفّ: تُبقي أقدم صفّ (أصغر id)، تُوحّد `locations`
    (اتحاد كل قيم location/locations بالمجموعة)، وتحذف الباقي. ثم تعيد حساب
    `dedup_key` لكل الصفوف الباقية بالصيغة الجديدة (apply_url-محورية) —
    خطوة ضرورية لأن dedup_key المخزّن للصفوف الباقية لا يزال بصيغة قديمة.
    لا تُستدعى تلقائيًا من أي منطق بالنظام؛ استدعاء يدوي واحد كافٍ بعد نشر
    إصلاح discovery.py (R10) — الجولات القادمة لن تُنتج هذا النمط من
    التكرار من الأساس (_group_raw_jobs_by_identity)."""
    engine = discovery.get_engine()
    groups_merged = 0
    rows_deleted = 0

    with engine.begin() as conn:
        # المرحلة 1: صفوف بها url — تُجمّع حسب (source_id, normalize_apply_url).
        url_rows = conn.execute(
            text(
                """
                SELECT id, source_id, company_name, title, location, locations, url
                FROM jobs WHERE url IS NOT NULL AND url <> ''
                ORDER BY id
                """
            )
        ).mappings().all()

        url_groups: dict[tuple, list] = {}
        for r in url_rows:
            norm = normalize_apply_url(r["url"])
            if not norm:
                continue
            url_groups.setdefault((r["source_id"], norm), []).append(r)

        # المرحلة 2: صفوف بلا url — تُجمّع حسب (company_key, title, location).
        nourl_rows = conn.execute(
            text(
                """
                SELECT id, source_id, company_name, title, location, locations, url
                FROM jobs WHERE url IS NULL OR url = ''
                ORDER BY id
                """
            )
        ).mappings().all()

        nourl_groups: dict[tuple, list] = {}
        for r in nourl_rows:
            k = (company_key(r["company_name"]), normalize_text(r["title"]), normalize_text(r["location"]))
            nourl_groups.setdefault(k, []).append(r)

        for group_rows in list(url_groups.values()) + list(nourl_groups.values()):
            if len(group_rows) <= 1:
                continue
            group_rows_sorted = sorted(group_rows, key=lambda r: r["id"])
            keep = group_rows_sorted[0]
            others = group_rows_sorted[1:]

            locs: set[str] = set()
            for r in group_rows_sorted:
                existing = r["locations"]
                if isinstance(existing, list):
                    locs.update(x for x in existing if isinstance(x, str) and x)
                if r["location"]:
                    locs.add(r["location"])

            conn.execute(
                text("UPDATE jobs SET locations = :locs WHERE id = :id"),
                {"locs": json.dumps(sorted(locs), ensure_ascii=False), "id": keep["id"]},
            )
            ids_to_delete = [o["id"] for o in others]
            conn.execute(text("DELETE FROM jobs WHERE id = ANY(:ids)"), {"ids": ids_to_delete})
            rows_deleted += len(ids_to_delete)
            groups_merged += 1

        # المرحلة 3: إعادة حساب dedup_key لكل الصفوف الباقية بالصيغة الجديدة
        # (apply_url-محورية حين متوفر). دفعية (executemany) لتفادي عشرات
        # آلاف الرحلات المنفصلة على قاعدة بيانات محلية بالحاوية نفسها.
        remaining = conn.execute(
            text("SELECT id, source_id, company_name, title, location, url FROM jobs")
        ).mappings().all()
        updates = [
            {
                "id": r["id"],
                "k": dedup_key(r["company_name"], r["title"], r["location"], r["url"], source_id=r["source_id"]),
            }
            for r in remaining
        ]
        if updates:
            conn.execute(text("UPDATE jobs SET dedup_key = :k WHERE id = :id"), updates)

    logger.warning(
        "merge-locations-cleanup: %s مجموعة دُمجت، %s صف مكرّر حُذف، %s dedup_key أُعيد حسابه",
        groups_merged,
        rows_deleted,
        len(updates) if "updates" in locals() else 0,
    )
    return {
        "ok": True,
        "groups_merged": groups_merged,
        "rows_deleted": rows_deleted,
        "dedup_keys_recomputed": len(updates) if "updates" in locals() else 0,
    }


@router.post("/discovery/recompute-region")
async def recompute_region() -> dict:
    """مراجعة B2 R11 — عملية لمرة واحدة (لا جدولة تلقائية): يعيد حساب
    country_code/out_of_region لكل صفوف jobs بمنطق compute_region المُصلَح
    (location_text البنيوي فقط حين متوفرًا؛ لا رجوع لنص العنوان/الوصف إلا
    حين location_text فارغ تمامًا) — يُصلح تلوّث jobs_in_region/jobs_sa الذي
    رصدته B2-review-2 (17.1% من "داخل النطاق" مواقعها الخام غير خليجية
    فعليًا بسبب ذكر عرَضي بالوصف كان يتجاوز location_text). لا تحذف ولا
    تُدرج أي صفّ — تُحدّث فقط."""
    engine = discovery.get_engine()
    total = 0
    changed_to_out = 0
    changed_to_in = 0
    last_id = 0
    while True:
        with engine.begin() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT id, title, location, description_snippet, out_of_region
                    FROM jobs WHERE id > :last_id ORDER BY id LIMIT :batch
                    """
                ),
                {"last_id": last_id, "batch": BACKFILL_BATCH_SIZE},
            ).mappings().all()
            if not rows:
                break
            for row in rows:
                last_id = row["id"]
                total += 1
                description = row["description_snippet"] or ""
                extra_text = f"{row['title'] or ''}\n{description[:300]}"
                country_code, out_of_region = compute_region(row["location"] or None, extra_text)
                if out_of_region != row["out_of_region"]:
                    if out_of_region:
                        changed_to_out += 1
                    else:
                        changed_to_in += 1
                conn.execute(
                    text("UPDATE jobs SET country_code = :cc, out_of_region = :oor WHERE id = :id"),
                    {"cc": country_code, "oor": out_of_region, "id": row["id"]},
                )

    logger.info(
        "recompute-region: %s صف مُعالَج، %s تحوّل لخارج النطاق، %s تحوّل لداخل النطاق",
        total,
        changed_to_out,
        changed_to_in,
    )
    return {
        "ok": True,
        "total_rows": total,
        "changed_to_out_of_region": changed_to_out,
        "changed_to_in_region": changed_to_in,
    }


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

        # مراجعة B2 R12 + B2b: نسبة التصنيف — على كل صفوف "داخل النطاق".
        # classified_n = عائلة حقيقية (ليست out_of_scope/sales_excluded).
        # excluded_n = out_of_scope (وظيفة خارج نطاق المنصّة صراحة، مثل مبيعات).
        #
        # مراجعة B2b (PLAN.md §B2b): المقياسان الآن يقسمان على jobs_in_region
        # الكامل (total_n) لا (total_n − excluded_n) كما كانت الصيغة القديمة —
        # تلك الصيغة كانت تُخرج out_of_scope من المقام كليًا فتُخفي حجمها
        # الفعلي عن القارئ. الصيغة الجديدة تطابق هدف B2b حرفيًا:
        #   family_classified_pct_in_region = (مصنَّف حقيقي + out_of_scope) / الكل  (الهدف ≥80%)
        #   family_real_pct_in_region       = مصنَّف حقيقي فقط / الكل             (الهدف ≥70%)
        family_row = conn.execute(
            text(
                f"""
                SELECT
                    count(*) FILTER (
                        WHERE family IS NOT NULL AND family NOT IN {_EXCLUDED_FAMILIES_SQL_LIST}
                    )::float AS classified,
                    count(*) FILTER (WHERE family IN {_EXCLUDED_FAMILIES_SQL_LIST})::float AS excluded,
                    count(*)::float AS total
                FROM jobs WHERE out_of_region = false
                """
            )
        ).first()
        classified_n, excluded_n, total_n = family_row[0], family_row[1], family_row[2]
        family_real_pct_in_region = (classified_n / total_n) if total_n else 0.0
        family_classified_pct_in_region = ((classified_n + excluded_n) / total_n) if total_n else 0.0

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

        # dup_ratio_24h_in_region (مراجعة B2 R14 — يستبدل صيغة R5 الأخشن):
        # هوية الإعلان = apply_url المطبّع (نفس مفهوم dedup_key بعد R10)
        # حين متوفر، وإلا (شركة+مسمى+موقع) مطبّعة كملاذ أخير. هذا يطابق
        # فعليًا ما يمنعه الآن القيد uq_jobs_dedup_key (بعد R10 وتنظيف
        # merge-locations-cleanup)، فيُفترض أن يقترب من الصفر بعد التنظيف.
        dup_row = conn.execute(
            text(
                """
                WITH scoped AS (
                    SELECT id,
                           COALESCE(
                               NULLIF(regexp_replace(lower(trim(url)), '\\?.*$|#.*$', ''), ''),
                               'noURL:' || lower(regexp_replace(trim(coalesce(company_name,'')), '\\s+', ' ', 'g')) || '|' ||
                                          lower(regexp_replace(trim(title), '\\s+', ' ', 'g')) || '|' ||
                                          lower(regexp_replace(trim(coalesce(location,'')), '\\s+', ' ', 'g'))
                           ) AS identity_key
                    FROM jobs
                    WHERE out_of_region = false AND first_seen_at >= :since
                ),
                grp AS (SELECT identity_key, COUNT(*) c FROM scoped GROUP BY identity_key)
                SELECT
                    (SELECT COUNT(*) FROM scoped) AS total_in_region_24h,
                    COALESCE((SELECT SUM(c) - COUNT(*) FROM grp WHERE c > 1), 0)::float AS excess_duplicate_rows
                """
            ),
            {"since": since_24h},
        ).first()
        total_rows, dup_rows = dup_row[0], dup_row[1]
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
        "family_real_pct_in_region": round(family_real_pct_in_region, 4),
        "family_excluded_in_region": int(excluded_n),
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
    لكل صفّ، وأيضًا تعيد حساب family لأي صفّ family IS NULL. لا تحذف ولا
    تُدرج أي صفّ — تُحدّث فقط. لإعادة حساب المنطقة فقط لكل الصفوف (بلا
    استخراج location/family) بعد إصلاح R11، استخدم
    POST /admin/discovery/recompute-region بدل هذه النقطة (أخف وأسرع)."""
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
    """تشخيص dup_ratio_24h_in_region — مراجعة B2 R14: يُرجع الآن أكثر
    مجموعات (source_id + apply_url مطبّع، أو company+title+location حين لا
    يوجد url) تكرارًا خلال آخر 24 ساعة، مطابقًا لتعريف dup_ratio الجديد."""
    engine = discovery.get_engine()
    since_24h = datetime.now(timezone.utc) - timedelta(hours=24)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                WITH recent AS (
                    SELECT j.id, j.source_id, s.source_type, c.name AS company_src,
                           COALESCE(
                               NULLIF(regexp_replace(lower(trim(j.url)), '\\?.*$|#.*$', ''), ''),
                               'noURL:' || lower(regexp_replace(trim(coalesce(j.company_name,'')), '\\s+', ' ', 'g')) || '|' ||
                                          lower(regexp_replace(trim(j.title), '\\s+', ' ', 'g')) || '|' ||
                                          lower(regexp_replace(trim(coalesce(j.location,'')), '\\s+', ' ', 'g'))
                           ) AS k
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
        SELECT j.id, c.name AS company, j.title, j.city, j.location, j.locations,
               j.country_code, j.out_of_region, j.years_min, j.seniority, j.saudi_only,
               j.family, j.skills, j.apply_mode, j.url, j.first_seen_at, j.description_snippet
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
    """مراجعة B2 R6/R12: أكثر عناوين الوظائف (داخل النطاق، باستثناء العائلات
    المُستبعدة عمدًا) غير المصنّفة عائليًا تكرارًا — يُستخدم لبناء/توسيع
    data/taxonomy_local.yaml بدل تخمين الكلمات المفتاحية الناقصة."""
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
