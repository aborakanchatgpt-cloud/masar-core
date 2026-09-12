"""
Masar Core — بناء طابور الإرسال (B4، الدليل: "17-22 رسالة/يوم لكل عميل،
موزّعة بتباعد ≥48 دقائق ضمن نافذة الإرسال، مع إحماء تدريجي وحدود التبريد").

يُستدعى كل 10 دقائق من core/app/scheduler_main.py (فقط ضمن نافذة الإرسال —
الفحص هناك) وعبر `POST /admin/mail/queue-now` يدويًا. لكل عميل نشط يملك
صندوق بريد بحالة 'ok': يحسب الهدف اليومي المتبقي (أدنى: target_daily،
سقف الإحماء اليوم، MAX_DAILY، رصيد المحفظة)، يمرّ على فرص اليوم المخططة
(`opportunities.status='planned'`) التي لم تُدرأج بطابور الإرسال بعد، يختار بريد
التقديم (apply_email.select_apply_email)، يتحقق من التبريد/السقف
الأسبوعي لكل شركة (نفس منطق planner.py، استعلامات مباشرة هنا لتجنّب أي
استيراد متبادل)، يبني الرسالة (composer.build_email) والسيرة الذاتية
(cv_builder.ensure_cv_variant)، يخصم رصيدًا واحدًا فورًا (قبل الإرسال
الفعلي — الاسترداد يحدث لاحقًا فقط عند ارتداد مؤكّد، عبر inbox.py)، ثم
يُدرج صفّ send_queue بموعد إرسال مجدول (pacing.next_slot ضمن نافذة اليوم).

لا يلمس customers_api.py ولا planner.py ولا matching.py — قراءة فقط من
جداولها (opportunities/customers/profiles/wallets)، واستعلامات SQL مستقلة
هنا لأي منطق يحتاج تكراره (تبريد/سقف أسبوعي) بدل استيراد داخلي متقاطع بين
منفّذين مستقلين.
"""
from __future__ import annotations

import json
import logging
import random
from datetime import date, datetime, timedelta
from datetime import time as dt_time

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from app import apply_email, composer, cv_builder, pacing, wallet
from app.collectors.normalizer import company_key as normalize_company_key
from app.discovery import get_engine

logger = logging.getLogger("masar.send_builder")

# سقف يومي على عدد الرسائل المرسلة لعنوان "عام" (info@/contact@) فقط —
# الدليل: "info@/contact@ كملاذ أخير فقط" — لا نسمح لأغلب دفعة عميل واحد
# أن تعتمد على عناوين عامة حتى لو توفرت لعدد كبير من الوظائف.
MAX_GENERIC_PER_DAY = 3

COOLDOWN_DAYS = 60
WEEKLY_CAP_WINDOW_DAYS = 7
WEEKLY_CAP_CUSTOMERS = 3


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


def _fetch_active_customers_with_mail(conn: Connection, customer_ids: list[int] | None) -> list[dict]:
    # B10: billing_mode/wallet_balance_sar مُضافان هنا فقط للقراءة — تحديد
    # أي سقف رصيد يُطبَّق (wallets.balance القديم مقابل floor(wallet_balance_sar/rate)
    # الجديد) يقع لاحقًا بـbuild_queue_for_customer، لا هنا.
    sql = """
        SELECT c.id, c.name, c.phone, c.cities, c.target_daily, c.billing_mode, c.wallet_balance_sar,
               p.years_exp, p.skills, p.cv_text, p.seniority, p.degree, p.certs, p.titles, p.languages,
               COALESCE(w.balance, 0) AS wallet_balance,
               ml.id AS mail_link_id, ml.address AS mail_address, ml.created_at AS mail_created_at,
               ml.status AS mail_status
        FROM customers c
        JOIN profiles p ON p.customer_id = c.id
        JOIN mail_links ml ON ml.customer_id = c.id
        LEFT JOIN wallets w ON w.customer_id = c.id
        WHERE c.status = 'active' AND ml.status = 'ok'
    """
    params: dict = {}
    if customer_ids:
        sql += " AND c.id = ANY(:ids)"
        params["ids"] = customer_ids
    rows = conn.execute(text(sql), params).mappings().all()
    return [dict(r) for r in rows]


MAX_CANDIDATES_PER_CUSTOMER = 300


def _fetch_candidate_opportunities(conn: Connection, customer_id: int, today: date) -> list[dict]:
    """فرص اليوم (وأي فرص متبقية من أيام سابقة لم تُرسل بعد — planned_for
    <= today عمدًا لا = فقط: يلتقط أي تراكم متأخر من فرص planner.py التي
    لم تصل قط لطابور إرسال، قرار تصميم بنطاق B4 موثّق هنا صراحة، لا يعدّل
    planner.py نفسه) غير المُدرجة بطابور إرسال فعّال (أي صفّ send_queue
    غير 'cancelled' لنفس opportunity_id يمنع إعادة إدراجها).

    `LIMIT MAX_CANDIDATES_PER_CUSTOMER` (تصحيح Low 3 بمراجعة B4 الأوفلاين،
    docs/reports/B4-offline-review.md): بلا حدّ أعلى، عميل مُعلّق طويلاً ثم
    أُعيد تفعيله بتراكم كبير من فرص planned قديمة قد يُحمّل آلاف الصفوف
    بالذاكرة لعميل واحد رغم أن الحلقة المستدعية تتوقف مبكرًا عند بلوغ الهدف
    اليومي (remaining) — الترتيب `score DESC` أصلًا يضمن مرور أفضل المرشّحين
    أولًا فلا يتأثر السلوك الفعلي، فقط الاستهلاك الأقصى للذاكرة."""
    rows = conn.execute(
        text(
            """
            SELECT o.id AS opportunity_id, o.job_id, o.score, o.tier,
                   j.title, j.company_name, j.company_id, j.description_snippet, j.raw_json
            FROM opportunities o
            JOIN jobs j ON j.id = o.job_id
            WHERE o.customer_id = :cid AND o.status = 'planned' AND o.planned_for <= :today
              AND NOT EXISTS (
                  SELECT 1 FROM send_queue sq
                  WHERE sq.opportunity_id = o.id AND sq.status != 'cancelled'
              )
              -- B5a البند 2 (customer_company_exclusions، ترحيل 0008): عميل
              -- استبعد هذه الشركة (👎 عبر feedback_api.py) — تُستبعَد فورًا
              -- من طابور الإرسال حتى لو كانت مخطّطة أصلًا قبل الاستبعاد
              -- (لا يلمس opportunities.status نفسه — يبقى 'planned' بصمت،
              -- planner.py يملك المسؤولية الوحيدة لتعديل تلك الصفوف).
              -- مفهرس عبر uq_customer_company_exclusions_customer_company
              -- (customer_id, company_id) — نفس أعمدة شرط EXISTS بالضبط.
              AND NOT EXISTS (
                  SELECT 1 FROM customer_company_exclusions cce
                  WHERE cce.customer_id = :cid AND cce.company_id = j.company_id
              )
            ORDER BY o.score DESC, o.id
            LIMIT :max_candidates
            """
        ),
        {"cid": customer_id, "today": today, "max_candidates": MAX_CANDIDATES_PER_CUSTOMER},
    ).mappings().all()
    return [dict(r) for r in rows]


def _count_committed_today(conn: Connection, customer_id: int, today_utc_start: datetime, today_utc_end: datetime) -> int:
    row = conn.execute(
        text(
            """
            SELECT count(*) FROM send_queue
            WHERE customer_id = :cid AND status != 'cancelled' AND synthetic = false
              AND send_after >= :start AND send_after < :end
            """
        ),
        {"cid": customer_id, "start": today_utc_start, "end": today_utc_end},
    ).scalar()
    return int(row or 0)


def _count_generic_today(conn: Connection, customer_id: int, today_utc_start: datetime, today_utc_end: datetime) -> int:
    """يقدّر عدد الرسائل المُرسَلة اليوم لعنوان "عام" (info@/contact@) عبر مطابقة
    الجزء المحلي لـto_email مباشرة بالـSQL (لا عمود email_class مخصّص بمخطّط
    send_queue — الأبسط والأصح هو إعادة استخدام نفس بادئات
    apply_email.GENERIC_PREFIXES هنا بدل إضافة عمود جديد لهذا التقدير وحده)."""
    prefixes = tuple(apply_email.GENERIC_PREFIXES)
    like_patterns = [f"{p}@%" for p in prefixes]
    row = conn.execute(
        text(
            """
            SELECT count(*) FROM send_queue
            WHERE customer_id = :cid AND status != 'cancelled' AND synthetic = false
              AND send_after >= :start AND send_after < :end
              AND lower(to_email) LIKE ANY(:patterns)
            """
        ),
        {"cid": customer_id, "start": today_utc_start, "end": today_utc_end, "patterns": like_patterns},
    ).scalar()
    return int(row or 0)


def _company_row(conn: Connection, company_id: int | None) -> dict | None:
    if not company_id:
        return None
    row = conn.execute(text("SELECT * FROM companies WHERE id = :id"), {"id": company_id}).mappings().first()
    return dict(row) if row else None


def fetch_cooldown_pairs(conn: Connection, now_utc: datetime) -> set[tuple[int, str]]:
    cutoff = now_utc - timedelta(days=COOLDOWN_DAYS)
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


def fetch_weekly_cap_companies(conn: Connection, now_utc: datetime) -> set[str]:
    cutoff = now_utc - timedelta(days=WEEKLY_CAP_WINDOW_DAYS)
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


def _debit_one_credit(conn: Connection, customer_id: int) -> tuple[int, int]:
    """يخصم رصيدًا واحدًا (قفل صف FOR UPDATE + قيد CHECK بمستوى القاعدة —
    نفس نمط customers_api.py:wallet_credit) ويُدرج قيد ledger عبر
    INSERT...RETURNING id للحصول على ledger_id الفعلي مباشرة (بدل مطابقة
    نصية هشّة لاحقًا). يرجع (new_balance, ledger_id)."""
    row = conn.execute(
        text("SELECT balance FROM wallets WHERE customer_id = :id FOR UPDATE"), {"id": customer_id}
    ).first()
    current = row[0] if row else 0
    if row is None:
        conn.execute(text("INSERT INTO wallets (customer_id, balance) VALUES (:id, 0)"), {"id": customer_id})
    new_balance = current - 1
    if new_balance < 0:
        raise ValueError(f"رصيد غير كافِِِِِِِِِِِِِِِِِِ للعميل {customer_id}")
    conn.execute(
        text("UPDATE wallets SET balance = :b, updated_at = now() WHERE customer_id = :id"),
        {"b": new_balance, "id": customer_id},
    )
    ledger_row = conn.execute(
        text(
            """
            INSERT INTO ledger (customer_id, delta, reason, ref_id, created_at)
            VALUES (:cid, -1, 'application_sent', NULL, now())
            RETURNING id
            """
        ),
        {"cid": customer_id},
    ).first()
    return new_balance, ledger_row[0]


def _mark_skipped(conn: Connection, opportunity_id: int, reason: str) -> None:
    conn.execute(
        text(
            """
            UPDATE opportunities SET
                status = 'skipped',
                reasons = COALESCE(reasons, '{}'::jsonb) || jsonb_build_object('skip_reason', :reason)
            WHERE id = :id AND status = 'planned'
            """
        ),
        {"id": opportunity_id, "reason": reason},
    )


def build_queue_for_customer(conn: Connection, customer: dict, today: date, rng: random.Random) -> dict:
    """المنطق الكامل لعميل واحد — يرجع إحصائيات هذه الدورة (queued/skipped)."""
    customer_id = customer["id"]
    now_utc = pacing.utc_now()
    riyadh_now_naive = pacing.to_riyadh_naive(now_utc)

    day_start_riyadh = datetime.combine(today, dt_time(0, 0))
    day_end_riyadh = datetime.combine(today, dt_time(23, 59, 59))
    today_utc_start = pacing.riyadh_naive_to_utc(day_start_riyadh)
    today_utc_end = pacing.riyadh_naive_to_utc(day_end_riyadh) + timedelta(seconds=1)

    ramp = pacing.ramp_cap(customer.get("mail_created_at"), today)
    # B10: عميل billing_mode='wallet' يُحسَب سقف رصيده من wallet_balance_sar
    # (رصيد ريالي) لا من wallets.balance القديم (عدد تقديمات — يبقى صفرًا
    # دومًا لعميل لم يشترِ قط رصيد "credits" التقليدي، فلو استُخدم هنا لعميل
    # محفظة لكان سقفه صفرًا دائمًا). عميل subscription (الافتراضي، وكل
    # عميل حالي قبل B10) يسلك نفس المسار القديم حرفيًا — صفر تغيير سلوك.
    billing_mode = customer.get("billing_mode") or "subscription"
    if billing_mode == "wallet":
        wallet_rate = wallet.per_application_rate_conn(conn)
        wallet_cap = wallet.applications_affordable(customer.get("wallet_balance_sar"), wallet_rate)
    else:
        wallet_cap = customer.get("wallet_balance") or 0

    target = min(
        customer.get("target_daily") or pacing.DEFAULT_TARGET_DAILY,
        ramp,
        pacing.MAX_DAILY,
        wallet_cap,
    )

    already_committed = _count_committed_today(conn, customer_id, today_utc_start, today_utc_end)
    remaining = target - already_committed
    if remaining <= 0:
        return {"customer_id": customer_id, "queued": 0, "skipped": 0, "reason": "target_reached_or_no_credit"}

    generic_used = _count_generic_today(conn, customer_id, today_utc_start, today_utc_end)
    cooldown_pairs = fetch_cooldown_pairs(conn, now_utc)
    weekly_cap_companies = fetch_weekly_cap_companies(conn, now_utc)

    candidates = _fetch_candidate_opportunities(conn, customer_id, today)
    if not candidates:
        return {"customer_id": customer_id, "queued": 0, "skipped": 0, "reason": "no_candidates"}

    profile_skills = _to_list(customer.get("skills"))
    customer_cities = _to_list(customer.get("cities"))
    years_exp = customer.get("years_exp")

    window_start_riyadh = datetime.combine(today, pacing.WINDOW_START)
    window_end_riyadh = datetime.combine(today, pacing.WINDOW_END)

    last_row = conn.execute(
        text(
            """
            SELECT max(send_after) FROM send_queue
            WHERE customer_id = :cid AND status != 'cancelled'
              AND send_after >= :start AND send_after < :end
            """
        ),
        {"cid": customer_id, "start": today_utc_start, "end": today_utc_end},
    ).scalar()
    if last_row is not None:
        previous_slot = pacing.to_riyadh_naive(last_row)
    else:
        base = max(window_start_riyadh, riyadh_now_naive)
        previous_slot = base - timedelta(minutes=pacing.MIN_GAP_MINUTES)

    queued = 0
    skipped = 0

    for cand in candidates:
        if queued >= remaining:
            break

        company_name = cand.get("company_name")
        ck = normalize_company_key(company_name)
        if ck and (customer_id, ck) in cooldown_pairs:
            _mark_skipped(conn, cand["opportunity_id"], "company_cooldown")
            skipped += 1
            continue
        if ck and ck in weekly_cap_companies:
            _mark_skipped(conn, cand["opportunity_id"], "weekly_company_cap")
            skipped += 1
            continue

        company_row = _company_row(conn, cand.get("company_id"))
        to_email, email_class = apply_email.select_apply_email(
            description_snippet=cand.get("description_snippet"),
            raw_json_text=cand.get("raw_json"),
            company_row=company_row,
        )
        if not to_email:
            _mark_skipped(conn, cand["opportunity_id"], "no_apply_email")
            skipped += 1
            continue
        if email_class == "generic" and generic_used >= MAX_GENERIC_PER_DAY:
            _mark_skipped(conn, cand["opportunity_id"], "generic_cap_reached")
            skipped += 1
            continue

        slot = pacing.next_slot(previous_slot, window_start_riyadh, window_end_riyadh, rng)
        if slot is None:
            break
        previous_slot = slot
        send_after_utc = pacing.riyadh_naive_to_utc(slot)

        job_text = " ".join(filter(None, [cand.get("title"), cand.get("description_snippet")]))
        composed = composer.build_email(
            customer_id=customer_id,
            job_key=str(cand["job_id"]),
            customer_name=customer.get("name") or "",
            customer_phone=customer.get("phone"),
            customer_city=customer_cities[0] if customer_cities else None,
            years_exp=float(years_exp) if years_exp is not None else None,
            profile_skills=profile_skills,
            job_title=cand.get("title") or "",
            job_skills=[],
            job_text_for_language=job_text,
            email_class=email_class or "posted",
        )

        family = None
        family_row = conn.execute(text("SELECT family FROM jobs WHERE id = :id"), {"id": cand["job_id"]}).first()
        if family_row:
            family = family_row[0]
        family = family or "general"

        profile_row = {
            "cv_text": customer.get("cv_text"),
            "years_exp": years_exp,
            "seniority": customer.get("seniority"),
            "degree": customer.get("degree"),
            "certs": customer.get("certs"),
            "skills": customer.get("skills"),
            "titles": customer.get("titles"),
            "languages": customer.get("languages"),
        }
        customer_row = {"id": customer_id, "name": customer.get("name"), "phone": customer.get("phone"), "cities": customer.get("cities")}
        try:
            cv_variant = cv_builder.ensure_cv_variant(conn, customer_row, profile_row, family)
        except Exception:
            logger.exception("تعذّر بناء السيرة الذاتية للعميل %s عائلة %s", customer_id, family)
            _mark_skipped(conn, cand["opportunity_id"], "cv_build_failed")
            skipped += 1
            continue

        # B10: عميل billing_mode='wallet' لا يلمس wallets/ledger القديمين
        # إطلاقًا — سقف `target` أعلاه (floor(wallet_balance_sar/rate)) هو
        # الضابط الوحيد لعدد الصفوف المُدرَجة هنا؛ الخصم الفعلي بالريال
        # يقع لاحقًا عند تأكيد الإرسال الناجح فقط (sender._mark_success)،
        # لا هنا وقت البناء — لا حاجة لفحص/خصم "رصيد كافٍ" هنا لأن الحلقة
        # أصلًا لن تتجاوز `remaining` (المشتقّ من target المحدود بالرصيد).
        if billing_mode == "wallet":
            ledger_id = None
        else:
            try:
                new_balance, ledger_id = _debit_one_credit(conn, customer_id)
            except ValueError:
                _mark_skipped(conn, cand["opportunity_id"], "insufficient_credit")
                skipped += 1
                break

        attachments = [{"path": cv_variant["pdf_path"], "filename": f"CV_{customer.get('name') or customer_id}.pdf"}]
        insert_row = conn.execute(
            text(
                """
                INSERT INTO send_queue (
                    customer_id, opportunity_id, job_id, to_email, cc_email, subject,
                    body_text, body_html, attachments, send_after, attempts, status, synthetic, created_at
                ) VALUES (
                    :customer_id, :opportunity_id, :job_id, :to_email, :cc_email, :subject,
                    :body_text, NULL, :attachments, :send_after, 0, 'queued', false, now()
                ) RETURNING id
                """
            ),
            {
                "customer_id": customer_id,
                "opportunity_id": cand["opportunity_id"],
                "job_id": cand["job_id"],
                "to_email": to_email,
                "cc_email": customer.get("mail_address"),
                "subject": composed.subject,
                "body_text": composed.body_text,
                "attachments": json.dumps(attachments, ensure_ascii=False),
                "send_after": send_after_utc,
            },
        ).first()
        queue_id = insert_row[0]

        if ledger_id is not None:
            conn.execute(
                text("UPDATE ledger SET ref_id = :ref WHERE id = :id"),
                {"ref": f"send_queue:{queue_id}", "id": ledger_id},
            )
        # 'queued' لا 'sent' هنا عمدًا (تصحيح Critical/High 2 بمراجعة B4
        # الأوفلاين، docs/reports/B4-offline-review.md): الإدراج بـsend_queue
        # لا يعني إرسالًا فعليًا بعد — 'sent' الحقيقية تُضبط فقط داخل
        # sender._mark_success بعد نجاح SMTP فعليًا،و'skipped' عند فشل
        # نهائي (sender._mark_failure، MAX_ATTEMPTS) حتى لا يبقى opportunities.status
        # كاذبًا لو فشل الإرسال لاحقًا (migration 0006_b4_fixes يضيف 'queued'
        # لقيد CHECK).
        conn.execute(
            text("UPDATE opportunities SET status = 'queued' WHERE id = :id"),
            {"id": cand["opportunity_id"]},
        )

        if email_class == "generic":
            generic_used += 1
        queued += 1

    return {"customer_id": customer_id, "queued": queued, "skipped": skipped}


def build_queue_round(customer_ids: list[int] | None = None, engine: Engine | None = None) -> dict:
    """نقطة الدخول الرئيسية — عزل كامل بين العملاء (خطأ عميل واحد لا يوقف
    بقية الدورة، بنفس فلسفة discovery.run_round/planner.run_plan_round)."""
    engine = engine or get_engine()
    today = pacing.to_riyadh_naive(pacing.utc_now()).date()

    with engine.connect() as conn:
        customers = _fetch_active_customers_with_mail(conn, customer_ids)

    total_queued = 0
    total_skipped = 0
    customers_processed = 0
    errors = 0

    for customer in customers:
        customers_processed += 1
        seed_text = f"{customer['id']}:{today.isoformat()}"
        rng = pacing.deterministic_rng(seed_text)
        try:
            with engine.begin() as conn:
                result = build_queue_for_customer(conn, customer, today, rng)
            total_queued += result.get("queued", 0)
            total_skipped += result.get("skipped", 0)
        except Exception:
            logger.exception("فشل بناء طابور الإرسال للعميل %s", customer.get("id"))
            errors += 1
            continue

    result = {
        "ok": True,
        "customers_processed": customers_processed,
        "customers_errored": errors,
        "queued": total_queued,
        "skipped": total_skipped,
        "date": today.isoformat(),
    }
    logger.info("دورة بناء طابور الإرسال انتهت: %s", result)
    return result
