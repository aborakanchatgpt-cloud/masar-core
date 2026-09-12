"""
Masar Core — المخطِّط اليومي (B3، الدليل §3.8).

يُشغّل من core/app/scheduler_main.py (06:00 الرياض ثم كل ساعة حتى 15:00
الرياض — راجع تعليق الجدولة هناك) ومن core/app/customers_api.py
(`POST /plan/run-now`، ولاختبار العميل المفرد أثناء القبول). لا يرفع أي
استثناء أبدًا لخطأ عميل واحد — بنفس فلسفة discovery.run_round().

**التصفية بمجموعات (set-based) عبر SQL** ثم **الدرجة ببايثون** على مجموعة
المرشّحين فقط (لا فرز/درجة كل صف jobs لكل عميل — القيد الأدائي بمعيار قبول
B3: 1500 عميل × 3000 وظيفة في < 5 دقائق):

    1. تمريرة أساسية: JOIN واحد على كل العملاء النشطين معًا عبر
       `LATERAL jsonb_array_elements_text(customers.families)` — يُرجع فقط
       الوظائف التي تقع ضمن عائلات كل عميل المختارة (مفهرس على
       jobs(family, out_of_region, first_seen_at) — ترحيل 0004)، مع فلترة
       المدينة (jsonb `?`) والسنوات مباشرة بالـSQL قبل أي نقل بيانات لبايثون.
    2. الدرجة/الاستبعاد القاطع (core/app/matching.py) على مجموعة المرشّحين
       المُصفّاة فقط، بايثون خالص.
    3. العملاء الذين لم يبلغوا هدفهم اليومي من التمريرة الأساسية فقط
       (عادة أقلية) يحصلون على **تمريرة توسيع ثانية** (عائلات غير مختارة،
       نفس فلاتر المدينة/السنوات) مقيّدة بقائمة معرّفاتهم فقط
       (`c.id = ANY(:short_ids)`) — لا نُشغّل التوسيع لكل عميل افتراضيًا.
    4. إدراج دفعي واحد (executemany عبر SQLAlchemy) لكل الفرص المختارة —
       لا إدراج صف بصف.

**schema (اختباري فقط):** `run_plan_round(schema=...)` يضبط `search_path`
لجلسة الاتصال — يُستخدم حصريًا من scripts/bench_planner.py لتشغيل نفس منطق
المخطِّط الحقيقي فوق جداول اصطناعية بمخطّط (schema) منفصل تمامًا عن
`public` الإنتاجي، بلا خطر لمس بيانات حقيقية. القيمة تُتحقّق بنمط صارم
(اسم مخطّط PostgreSQL صالح فقط) قبل تمريرها بأي SQL حتى مع كونها غير
مكشوفة لأي مستخدم خارجي فعليًا (القيمة الوحيدة القادمة من خارج الكود هي
ثابت سكربت البنش نفسه).
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from app import company_directory, matching
from app.collectors.normalizer import company_key as normalize_company_key
from app.discovery import get_engine

logger = logging.getLogger("masar.planner")

DEFAULT_TARGET_DAILY = 17
MAX_DAILY = 22
COOLDOWN_DAYS = 60
WEEKLY_CAP_WINDOW_DAYS = 7
WEEKLY_CAP_CUSTOMERS = 3

_SCHEMA_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

# توقيت الرياض ثابت UTC+3 طوال السنة (لا يوجد توقيت صيفي بالسعودية) — لا
# نعتمد على zoneinfo/tzdata (قد لا تتوفر قاعدة بيانات المناطق الزمنية داخل
# صورة python:3.12-slim الأساسية).
RIYADH_OFFSET = timedelta(hours=3)


def now_riyadh() -> datetime:
    return datetime.now(timezone.utc) + RIYADH_OFFSET


def _set_schema(conn: Connection, schema: str) -> None:
    """SET LOCAL عمدًا (لا SET عادية): محدود بنطاق المعاملة الحالية فقط
    وينعكس تلقائيًا عند COMMIT أو ROLLBACK — يمنع تسرّب search_path إلى
    اتصالات أخرى بنفس تجمّع الاتصالات (pool) المشترك مع بقية Core (خطر
    حقيقي مع `SET` العادية داخل `engine.begin()` التي تُثبّت بعد commit
    على الاتصال الفعلي وتبقى له حتى إعادة تعيينها)."""
    if schema == "public":
        return
    if not _SCHEMA_RE.match(schema):
        raise ValueError(f"اسم مخطّط غير صالح: {schema}")
    conn.execute(text(f'SET LOCAL search_path TO "{schema}", public'))


def _to_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except (ValueError, TypeError):
            return []
    return []


# ---------------------------------------------------------------------------
# قراءة العملاء النشطين + رصيدهم
# ---------------------------------------------------------------------------


def _fetch_active_customers(conn: Connection, customer_ids: list[int] | None) -> list[dict]:
    sql = """
        SELECT c.id, c.target_daily, c.cities, c.families, c.speculative_enabled,
               p.years_exp, p.seniority, p.nationality_saudi, p.titles, p.skills, p.degree,
               COALESCE(w.balance, 0) AS wallet_balance
        FROM customers c
        JOIN profiles p ON p.customer_id = c.id
        LEFT JOIN wallets w ON w.customer_id = c.id
        WHERE c.status = 'active'
    """
    params: dict = {}
    if customer_ids:
        sql += " AND c.id = ANY(:ids)"
        params["ids"] = customer_ids
    rows = conn.execute(text(sql), params).mappings().all()
    return [dict(r) for r in rows]


def _fetch_existing_planned(
    conn: Connection, customer_ids: list[int], planned_for: date
) -> tuple[dict[int, set[int]], dict[int, set[str]]]:
    """وظائف/شركات مخطّطة أصلًا اليوم لكل عميل (planned/queued/sent) — تُستخدم
    لاستبعادها من مجموعة المرشّحين قبل الاختيار بالتشغيلات اللاحقة (التعبئة
    الساعية — §3.8)، لسببين:
    1. بلا استبعاد job_id: إعادة اختيار وظيفة مخطّطة أصلًا تمر عبر
       `ON CONFLICT DO UPDATE` بلا خطأ لكنها **تستهلك حصة من الهدف المتبقي
       بلا أن تضيف فرصة جديدة فعليًا**، فيبقى العميل دون هدفه رغم توفّر
       مرشّحين جدد كافين (خلل رُصِد أثناء اختبار القبول المحلي لـB3، أُصلح
       قبل الدفع).
    2. بلا استبعاد company_key: قاعدة "فرصة واحدة لكل شركة بكل دورة تخطيط"
       بـ`_select_for_customer` تصبح بلا معنى فعليًا مع التعبئة الساعية —
       دورة لاحقة بنفس اليوم قد تختار وظيفة *أخرى* بنفس الشركة التي
       اتصل بها العميل أصلًا اليوم بدورة سابقة."""
    if not customer_ids:
        return {}, {}
    rows = conn.execute(
        text(
            """
            SELECT o.customer_id, o.job_id, j.company_name
            FROM opportunities o JOIN jobs j ON j.id = o.job_id
            WHERE o.planned_for = :planned_for AND o.status IN ('planned','queued','sent')
              AND o.customer_id = ANY(:ids)
            """
        ),
        {"planned_for": planned_for, "ids": customer_ids},
    ).all()
    job_ids: dict[int, set[int]] = {}
    companies: dict[int, set[str]] = {}
    for cid, jid, company_name in rows:
        job_ids.setdefault(cid, set()).add(jid)
        ck = normalize_company_key(company_name)
        if ck:
            companies.setdefault(cid, set()).add(ck)
    return job_ids, companies


# ---------------------------------------------------------------------------
# تبريد الشركة (60 يومًا لكل عميل) وسقف 3 عملاء/أسبوع لكل شركة — الدليل §3.6
# البندان الأخيران بتكليف B3. المصدران (applications + company_cooldowns)
# يُجمَعان (union) — company_cooldowns سيُغذّيه B4 لاحقًا لأداء أفضل، أما
# applications.company_key فمصدر الحقيقة الفعلي بمجرد بدء الإرسال.
# ---------------------------------------------------------------------------


def fetch_cooldown_pairs(conn: Connection, now: datetime) -> set[tuple[int, str]]:
    cutoff = now - timedelta(days=COOLDOWN_DAYS)
    rows = conn.execute(
        text(
            """
            SELECT DISTINCT customer_id, company_key FROM (
                SELECT customer_id, company_key FROM applications
                WHERE sent_at >= :cutoff AND company_key IS NOT NULL
                UNION ALL
                SELECT customer_id, company_key FROM company_cooldowns
                WHERE last_sent_at >= :cutoff
            ) u
            """
        ),
        {"cutoff": cutoff},
    ).all()
    return {(r[0], r[1]) for r in rows}


def fetch_weekly_cap_companies(conn: Connection, now: datetime) -> set[str]:
    cutoff = now - timedelta(days=WEEKLY_CAP_WINDOW_DAYS)
    rows = conn.execute(
        text(
            """
            SELECT company_key FROM applications
            WHERE sent_at >= :cutoff AND company_key IS NOT NULL
            GROUP BY company_key
            HAVING count(DISTINCT customer_id) >= :cap
            """
        ),
        {"cutoff": cutoff, "cap": WEEKLY_CAP_CUSTOMERS},
    ).all()
    return {r[0] for r in rows}


# ---------------------------------------------------------------------------
# مرشّح المرشّحين بمجموعات (SQL) — أساسي (عائلات العميل) وموسّع (باقي
# العائلات، للعملاء القصيرين فقط)
# ---------------------------------------------------------------------------

_CANDIDATE_COLUMNS = """
    j.id AS job_id, j.title, j.family, j.years_min, j.seniority, j.saudi_only,
    j.city, j.skills, j.company_id, j.company_name, j.apply_mode, j.first_seen_at
"""


def _fetch_primary_candidates(conn: Connection, customer_ids: list[int], years_slack: int) -> list[dict]:
    if not customer_ids:
        return []
    # DISTINCT ON (c.id, j.id) لا DISTINCT الكاملة عمدًا: j.skills بنوع json
    # العادي (لا jsonb) بجدول jobs الإنتاجي (ترحيل 0002) — Postgres لا يملك
    # عامل مساواة لنوع json (فقط jsonb)، فـSELECT DISTINCT الكاملة (التي
    # تقارن كل عمود مُخرَج بما فيها json) تفشل بخطأ
    # "could not identify an equality operator for type json". DISTINCT ON
    # يقارن مفتاح التجميع فقط (c.id, j.id، كلاهما bigint) — يحل التكرار
    # النظري الوحيد الممكن هنا (تكرار حرفي بمصفوفة customers.families لنفس
    # العميل) بلا هذه المشكلة، والصفوف المكرّرة متطابقة المحتوى أصلًا فلا
    # يهم أيها يُبقيه ORDER BY.
    rows = conn.execute(
        text(
            f"""
            SELECT DISTINCT ON (c.id, j.id) c.id AS customer_id, {_CANDIDATE_COLUMNS}
            FROM customers c
            JOIN profiles p ON p.customer_id = c.id
            CROSS JOIN LATERAL jsonb_array_elements_text(
                CASE WHEN jsonb_typeof(c.families) = 'array' THEN c.families ELSE '[]'::jsonb END
            ) AS fam(family_name)
            JOIN jobs j ON j.family = fam.family_name
                AND j.out_of_region = false
                AND (j.years_min IS NULL OR j.years_min <= COALESCE(p.years_exp, 0) + :slack)
                AND (
                    j.city IS NULL
                    OR EXISTS (
                        SELECT 1 FROM jsonb_array_elements_text(
                            CASE WHEN jsonb_typeof(c.cities) = 'array' THEN c.cities ELSE '[]'::jsonb END
                        ) AS city(v) WHERE lower(v) = lower(j.city)
                    )
                )
            WHERE c.id = ANY(:ids)
            ORDER BY c.id, j.id
            """
        ),
        {"ids": customer_ids, "slack": years_slack},
    ).mappings().all()
    return [dict(r) for r in rows]


def _fetch_widened_candidates(conn: Connection, customer_ids: list[int], years_slack: int) -> list[dict]:
    if not customer_ids:
        return []
    # لا DISTINCT هنا: JOIN مباشر (بلا LATERAL/fan-out) بين customers وjobs —
    # كل زوج (c.id, j.id) يظهر مرة واحدة على الأكثر ببنية الاستعلام نفسها،
    # فلا داعي لأي إزالة تكرار (وبالتالي لا خطر مقارنة عمود json — راجع
    # تعليق _fetch_primary_candidates أعلاه لتفصيل المشكلة الأصلية).
    excluded = ",".join(f"'{name}'" for name in sorted(matching.EXCLUDED_FAMILY_NAMES))
    rows = conn.execute(
        text(
            f"""
            SELECT c.id AS customer_id, {_CANDIDATE_COLUMNS}
            FROM customers c
            JOIN profiles p ON p.customer_id = c.id
            JOIN jobs j ON j.out_of_region = false
                AND j.family IS NOT NULL
                AND j.family NOT IN ({excluded or "''"})
                AND NOT (
                    jsonb_typeof(c.families) = 'array' AND c.families ? j.family
                )
                AND (j.years_min IS NULL OR j.years_min <= COALESCE(p.years_exp, 0) + :slack)
                AND (
                    j.city IS NULL
                    OR EXISTS (
                        SELECT 1 FROM jsonb_array_elements_text(
                            CASE WHEN jsonb_typeof(c.cities) = 'array' THEN c.cities ELSE '[]'::jsonb END
                        ) AS city(v) WHERE lower(v) = lower(j.city)
                    )
                )
            WHERE c.id = ANY(:ids)
            """
        ),
        {"ids": customer_ids, "slack": years_slack},
    ).mappings().all()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# بناء كائنات matching.py من صفوف SQL
# ---------------------------------------------------------------------------


def row_to_profile(customer_row: dict) -> matching.CustomerProfile:
    years = customer_row.get("years_exp")
    return matching.CustomerProfile(
        customer_id=customer_row["id"],
        years_exp=float(years) if years is not None else None,
        seniority=customer_row.get("seniority"),
        nationality_saudi=bool(customer_row.get("nationality_saudi")),
        families=_to_list(customer_row.get("families")),
        cities=_to_list(customer_row.get("cities")),
        titles=_to_list(customer_row.get("titles")),
        skills=_to_list(customer_row.get("skills")),
        degree_family=[customer_row["degree"]] if customer_row.get("degree") else [],
    )


def row_to_job(row: dict) -> matching.JobCandidate:
    return matching.JobCandidate(
        job_id=row["job_id"],
        title=row.get("title") or "",
        family=row.get("family"),
        years_min=row.get("years_min"),
        seniority=row.get("seniority"),
        saudi_only=bool(row.get("saudi_only")),
        city=row.get("city"),
        skills=_to_list(row.get("skills")),
        company_name=row.get("company_name"),
        apply_mode=row.get("apply_mode"),
        first_seen_at=row.get("first_seen_at"),
        out_of_region=False,
    )


# ---------------------------------------------------------------------------
# اختيار الفرص لكل عميل من مجموعة مرشّحين مُصفّاة/مُقيّمة مسبقًا
# ---------------------------------------------------------------------------


def _select_for_customer(
    candidates: list[dict],
    profile: matching.CustomerProfile,
    remaining: int,
    *,
    now: datetime,
    widened: bool,
    cooldown_pairs: set[tuple[int, str]],
    weekly_cap_companies: set[str],
    already_planned_job_ids: set[int],
    already_planned_companies: set[str],
) -> list[tuple[matching.MatchResult, dict]]:
    if remaining <= 0 or not candidates:
        return []
    scored: list[tuple[matching.MatchResult, dict]] = []
    for row in candidates:
        if row["job_id"] in already_planned_job_ids:
            continue  # مخطّطة أصلًا اليوم — لا تستهلك حصة الهدف المتبقي مجددًا
        job = row_to_job(row)
        ck = normalize_company_key(row.get("company_name"))
        if ck and ck in already_planned_companies:
            continue  # شركة اتصل بها العميل أصلًا اليوم بدورة سابقة (§ملاحظة الدالة أعلاه)
        result = matching.evaluate(
            profile,
            job,
            now=now,
            widened_family=widened,
            cooldown_active=bool(ck) and (profile.customer_id, ck) in cooldown_pairs,
            weekly_cap_reached=bool(ck) and ck in weekly_cap_companies,
        )
        if result.disqualified:
            continue
        if widened and result.tier != "C2":
            continue
        if not widened and result.tier not in ("A", "B", "C"):
            continue
        scored.append((result, row))

    scored.sort(key=lambda pair: (-pair[0].score, pair[1]["job_id"]))

    selected: list[tuple[matching.MatchResult, dict]] = []
    used_companies: set[str] = set()
    for result, row in scored:
        if len(selected) >= remaining:
            break
        ck = normalize_company_key(row.get("company_name"))
        if ck and ck in used_companies:
            continue  # لا أكثر من فرصة واحدة لنفس الشركة بنفس دورة التخطيط
        if ck:
            used_companies.add(ck)
        selected.append((result, row))
    return selected


# ---------------------------------------------------------------------------
# الإدراج الدفعي
# ---------------------------------------------------------------------------


def _insert_opportunities(conn: Connection, rows: list[dict]) -> None:
    if not rows:
        return
    conn.execute(
        text(
            """
            INSERT INTO opportunities (customer_id, job_id, score, tier, reasons, planned_for, status, created_at)
            VALUES (:customer_id, :job_id, :score, :tier, :reasons, :planned_for, 'planned', now())
            ON CONFLICT (customer_id, job_id) DO UPDATE SET
                score = EXCLUDED.score,
                tier = EXCLUDED.tier,
                reasons = EXCLUDED.reasons
            WHERE opportunities.status = 'planned'
            """
        ),
        rows,
    )


# ---------------------------------------------------------------------------
# نقطة الدخول الرئيسية
# ---------------------------------------------------------------------------


def run_plan_round(
    schema: str = "public",
    customer_ids: list[int] | None = None,
    engine: Engine | None = None,
) -> dict:
    """جولة تخطيط واحدة كاملة — idempotent (يُعاد استدعاؤها كل ساعة/يدويًا
    بلا أثر جانبي غير المرغوب: العملاء الذين بلغوا هدفهم اليومي أصلًا
    يُتخطَّون، ومن لم يبلغه يُكمّل الباقي فقط)."""
    engine = engine or get_engine()
    started = time.monotonic()
    now = now_riyadh()
    planned_for = now.date()

    with engine.connect() as conn:
        _set_schema(conn, schema)
        customers = _fetch_active_customers(conn, customer_ids)
        if not customers:
            return {
                "ok": True,
                "customers_active": 0,
                "customers_planned": 0,
                "opportunities_planned": 0,
                "seconds": round(time.monotonic() - started, 3),
            }
        all_ids = [c["id"] for c in customers]
        existing_job_ids, existing_companies = _fetch_existing_planned(conn, all_ids, planned_for)
        cooldown_pairs = fetch_cooldown_pairs(conn, now)
        weekly_cap_companies = fetch_weekly_cap_companies(conn, now)
        primary_rows = _fetch_primary_candidates(conn, all_ids, matching.YEARS_SLACK)

    primary_by_customer: dict[int, list[dict]] = {}
    for row in primary_rows:
        primary_by_customer.setdefault(row["customer_id"], []).append(row)

    to_insert: list[dict] = []
    short_targets: dict[int, int] = {}
    customers_with_selection: set[int] = set()
    # نُراكم استبعاد الوظائف/الشركات مع كل اختيار (تمريرة أساسية ثم موسّعة)
    # حتى لا تختار التمريرة الموسّعة شركة استُخدمت أصلًا بالتمريرة الأساسية
    # بنفس هذه الجولة تحديدًا — كل مجموعة تبدأ بنسخة عن الوظائف/الشركات
    # المخطّطة أصلًا اليوم (تشغيلات سابقة نفس اليوم).
    planned_job_ids: dict[int, set[int]] = {cid: set(v) for cid, v in existing_job_ids.items()}
    planned_companies: dict[int, set[str]] = {cid: set(v) for cid, v in existing_companies.items()}

    profiles_by_id: dict[int, matching.CustomerProfile] = {}
    for c in customers:
        cid = c["id"]
        profile = row_to_profile(c)
        profiles_by_id[cid] = profile
        target = min(c.get("target_daily") or DEFAULT_TARGET_DAILY, MAX_DAILY, c.get("wallet_balance") or 0)
        already = len(existing_job_ids.get(cid, set()))
        remaining = target - already
        if remaining <= 0:
            continue
        sel = _select_for_customer(
            primary_by_customer.get(cid, []),
            profile,
            remaining,
            now=now,
            widened=False,
            cooldown_pairs=cooldown_pairs,
            weekly_cap_companies=weekly_cap_companies,
            already_planned_job_ids=planned_job_ids.get(cid, set()),
            already_planned_companies=planned_companies.get(cid, set()),
        )
        if sel:
            customers_with_selection.add(cid)
        for result, row in sel:
            to_insert.append(_result_to_insert_row(cid, result, planned_for))
            planned_job_ids.setdefault(cid, set()).add(result.job_id)
            ck = normalize_company_key(row.get("company_name"))
            if ck:
                planned_companies.setdefault(cid, set()).add(ck)
        still_short = remaining - len(sel)
        if still_short > 0:
            short_targets[cid] = still_short

    # B12.3: نتتبّع الباقي بعد تمريرة التوسيع لكل عميل — يُغذّي تمريرة
    # التقديم المبادر أدناه (تعمل فقط للعملاء الذين لم يكفهم حتى التوسيع).
    remaining_after_widened: dict[int, int] = {}

    if short_targets:
        short_ids = list(short_targets.keys())
        with engine.connect() as conn:
            _set_schema(conn, schema)
            widened_rows = _fetch_widened_candidates(conn, short_ids, matching.YEARS_SLACK)
        widened_by_customer: dict[int, list[dict]] = {}
        for row in widened_rows:
            widened_by_customer.setdefault(row["customer_id"], []).append(row)
        for cid, remaining in short_targets.items():
            sel = _select_for_customer(
                widened_by_customer.get(cid, []),
                profiles_by_id[cid],
                remaining,
                now=now,
                widened=True,
                cooldown_pairs=cooldown_pairs,
                weekly_cap_companies=weekly_cap_companies,
                already_planned_job_ids=planned_job_ids.get(cid, set()),
                already_planned_companies=planned_companies.get(cid, set()),
            )
            if sel:
                customers_with_selection.add(cid)
            for result, row in sel:
                to_insert.append(_result_to_insert_row(cid, result, planned_for))
                # B12.3: يجب تحديث المجموعتين هنا أيضًا (لم يكن ضروريًا قبل
                # وجود تمريرة إضافية بعد التوسيع — التقديم المبادر أدناه
                # يعتمد عليهما لتفادي اختيار شركة استُخدمت أصلًا بهذه الجولة).
                planned_job_ids.setdefault(cid, set()).add(result.job_id)
                ck = normalize_company_key(row.get("company_name"))
                if ck:
                    planned_companies.setdefault(cid, set()).add(ck)
            leftover = remaining - len(sel)
            if leftover > 0:
                remaining_after_widened[cid] = leftover

    # -----------------------------------------------------------------
    # B12.3: التقديم المبادر — يُكمّل من company_directory فقط للعملاء
    # الذين لم يبلغوا هدفهم اليومي حتى بعد التوسيع، وفعّلوا الميزة
    # (customers.speculative_enabled، افتراضي true). بحد أقصى
    # MAX_SPECULATIVE_PER_CUSTOMER_PER_DAY/يوم/عميل، بنفس تبريد 60 يومًا
    # وسقف 3 عملاء/شركة/أسبوع المُجلَبين أصلًا أعلى الدالة (company_key
    # الشركة الحقيقية — راجع docstring company_directory.ensure_company_and_job).
    # -----------------------------------------------------------------
    to_insert_speculative: list[dict] = []
    if remaining_after_widened:
        customers_by_id = {c["id"]: c for c in customers}
        with engine.begin() as conn:
            _set_schema(conn, schema)
            for cid, remaining in remaining_after_widened.items():
                customer_row = customers_by_id.get(cid) or {}
                sel = _select_speculative_for_customer(
                    conn,
                    customer_row,
                    profiles_by_id[cid],
                    remaining,
                    cooldown_pairs=cooldown_pairs,
                    weekly_cap_companies=weekly_cap_companies,
                    already_planned_companies=planned_companies.setdefault(cid, set()),
                )
                if sel:
                    customers_with_selection.add(cid)
                for item in sel:
                    to_insert_speculative.append(_speculative_insert_row(cid, item, planned_for))

    with engine.begin() as conn:
        _set_schema(conn, schema)
        _insert_opportunities(conn, to_insert)
        _insert_speculative_opportunities(conn, to_insert_speculative)

    elapsed = round(time.monotonic() - started, 3)
    result = {
        "ok": True,
        "customers_active": len(customers),
        "customers_planned": len(customers_with_selection),
        "opportunities_planned": len(to_insert),
        "opportunities_speculative": len(to_insert_speculative),
        "seconds": elapsed,
        "planned_for": planned_for.isoformat(),
    }
    logger.info("جولة تخطيط انتهت: %s", result)
    return result


def _result_to_insert_row(customer_id: int, result: matching.MatchResult, planned_for: date) -> dict:
    return {
        "customer_id": customer_id,
        "job_id": result.job_id,
        "score": result.score,
        "tier": result.tier,
        "reasons": json.dumps(result.score_parts, ensure_ascii=False),
        "planned_for": planned_for,
    }


# ---------------------------------------------------------------------------
# B12.3: التقديم المبادر — اختيار من company_directory (بلا درجة/مطابقة،
# فلترة مباشرة بالعائلة/المدينة فقط) وإدراجه بعلامة speculative=true.
# ---------------------------------------------------------------------------


def _select_speculative_for_customer(
    conn: Connection,
    customer_row: dict,
    profile: matching.CustomerProfile,
    remaining: int,
    *,
    cooldown_pairs: set[tuple[int, str]],
    weekly_cap_companies: set[str],
    already_planned_companies: set[str],
) -> list[dict]:
    if remaining <= 0:
        return []
    if not customer_row.get("speculative_enabled", True):
        return []
    families = [f for f in profile.families if f]
    if not families:
        return []

    cap = min(remaining, company_directory.MAX_SPECULATIVE_PER_CUSTOMER_PER_DAY)
    flexible = company_directory.is_flexible_cities(profile.cities)
    # نجلب أكثر من cap مرشّحًا (×5) لأن جزءًا منهم سيُستبعَد بالتبريد/السقف/
    # التكرار بنفس الجولة — احتياط رخيص بدل استعلامات متكررة لكل استبعاد.
    candidates = company_directory.fetch_candidates_for_customer(
        conn, families, profile.cities, flexible, cap * 5
    )

    selected: list[dict] = []
    for cand in candidates:
        if len(selected) >= cap:
            break
        ck = normalize_company_key(cand.get("name"))
        if not ck or ck in already_planned_companies:
            continue
        if (profile.customer_id, ck) in cooldown_pairs:
            continue
        if ck in weekly_cap_companies:
            continue
        job_id = company_directory.ensure_company_and_job(conn, cand)
        already_planned_companies.add(ck)
        selected.append(
            {
                "job_id": job_id,
                "company_directory_id": cand["id"],
                "sector_family": cand["sector_family"],
            }
        )
    return selected


def _speculative_insert_row(customer_id: int, item: dict, planned_for: date) -> dict:
    return {
        "customer_id": customer_id,
        "job_id": item["job_id"],
        "reasons": json.dumps(
            {"speculative": True, "company_directory_id": item["company_directory_id"], "sector_family": item["sector_family"]},
            ensure_ascii=False,
        ),
        "planned_for": planned_for,
    }


def _insert_speculative_opportunities(conn: Connection, rows: list[dict]) -> None:
    if not rows:
        return
    # DO NOTHING عمدًا (لا DO UPDATE كـ_insert_opportunities العادية): مرة
    # واحدة تكفي لكل زوج (عميل، شركة دليل) — لا داعٍ لتحديث `score`/`tier`
    # الثابتين هنا بكل جولة، ولا لإعادة لمس صفّ ربما تقدّم حالته فعليًا
    # (queued/sent) بجولة سابقة.
    conn.execute(
        text(
            """
            INSERT INTO opportunities (customer_id, job_id, score, tier, reasons, planned_for, status, speculative, created_at)
            VALUES (:customer_id, :job_id, 0.5, 'C', :reasons, :planned_for, 'planned', true, now())
            ON CONFLICT (customer_id, job_id) DO NOTHING
            """
        ),
        rows,
    )
