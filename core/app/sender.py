"""
Masar Core — مُرسِل البريد (B4، الدليل: "DRY_RUN افتراضي، sink للتطوير،
CC للعميل دومًا، نافذة إرسال صارمة").

يُستدعى كل دقيقة من core/app/scheduler_main.py وعبر `POST /admin/mail/send-now`
يدويًا. يطالب (claim) بدفعة مستحقة من send_queue عبر
`SELECT ... FOR UPDATE SKIP LOCKED` (يسمح بعدة عمليات إرسال متوازية — حتى
SEND_WORKERS — بلا تنافس على نفس الصفوف)، يحدّد وجهة النقل الفعلية (sink
محلي/dry-run/حقيقي) حسب أولوية صارمة موثّقة بـ`resolve_transport`/
`resolve_recipient`، يرسل عبر SMTP، ثم يُحدّث الحالة (نجاح: applications +
company_cooldowns؛ فشل: إعادة جدولة بتراجع أو فشل نهائي + استرداد الرصيد).

أولوية وجهة الإرسال (moved هنا صراحة، الدليل §"DRY_RUN/sink"):
    1. MAIL_SINK_SMTP معرّف بالبيئة → **كل** البريد يذهب لصندوق sink محلي
       (mailpit، شبكة داخلية فقط) — وضع تطوير، يتجاوز DRY_RUN_TO/MAIL_LIVE
       كليًا (الدليل: "حين يُضبط MAIL_SINK_SMTP، كل البريد يذهب هناك بصرف
       النظر عن DRY_RUN_TO").
    2. DRY_RUN_TO معرّف (وغير فارغ) وMAIL_SINK_SMTP غير معرّف → يُرسَل
       فعليًا عبر SMTP الحقيقي لصندوق العميل، لكن للمستلم DRY_RUN_TO فقط
       (لا للعنوان الحقيقي)، بعنوان يحمل بادئة `[DRY-RUN to: <original>]`.
    3. DRY_RUN_TO فارغ وMAIL_LIVE=true → إرسال حقيقي فعلي للمستلم الحقيقي.
    4. غير ذلك (لا sink، لا dry-run، لا MAIL_LIVE) → لا إرسال إطلاقًا (فشل
       آمن fail-closed — الدليل: "DRY_RUN افتراضي دومًا").
"""
from __future__ import annotations

import logging
import os
import smtplib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app import mail_crypto, pacing
from app.collectors.normalizer import company_key as normalize_company_key
from app.discovery import get_engine

logger = logging.getLogger("masar.sender")

CLAIM_BATCH_LIMIT_DEFAULT = 200
SEND_WORKERS = 20
LOCK_DURATION_MINUTES = 5
SMTP_TIMEOUT_SECONDS = 15
# تراجع بسيط بين المحاولات: محاولة 1 فشلت → أعد بعد 1 دقيقة؛ محاولة 2 فشلت
# → أعد بعد 5 دقائق؛ محاولة 3 فشلت → فشل نهائي (MAX_ATTEMPTS) + استرداد رصيد.
BACKOFF_MINUTES = {1: 1, 2: 5}
MAX_ATTEMPTS = 3


def resolve_transport(customer_id: int, mail_link: dict | None) -> dict | None:
    """يحدّد إعدادات النقل الفعلية (SMTP) لهذا العميل حسب أولوية sink>real:
    - MAIL_SINK_SMTP معرّف (مثال "mailpit:1025") → يُستخدم دومًا (تطوير)،
      بغض النظر عن وجود mail_link حقيقي أصلًا.
    - وإلا: يتطلّب mail_link حقيقي بحالة 'ok' وسرّ صالح — يُفكّ تشفيره هنا
      (لا يُسجّل أبدًا بأي مكان). يرجع None إن تعذّر (لا مصدر إرسال متاح
      إطلاقًا لهذا العميل الآن)."""
    sink = os.environ.get("MAIL_SINK_SMTP", "").strip()
    if sink:
        host, _, port_str = sink.partition(":")
        port = int(port_str) if port_str else 1025
        from_addr = (mail_link or {}).get("address") or f"customer-{customer_id}@masar.local"
        return {
            "mode": "sink",
            "host": host,
            "port": port,
            "use_tls": False,
            "username": None,
            "password": None,
            "from_addr": from_addr,
        }

    if not mail_link or mail_link.get("status") != "ok":
        return None

    secret_enc = mail_link.get("secret_enc")
    if not secret_enc:
        return None
    try:
        password = mail_crypto.decrypt_secret(secret_enc)
    except (mail_crypto.MailCryptoUnavailable, ValueError):
        logger.exception("تعذّر فك تشفير سرّ البريد للعميل %s", customer_id)
        return None

    return {
        "mode": "real",
        "host": mail_link.get("smtp_host") or "smtp.gmail.com",
        "port": mail_link.get("smtp_port") or 587,
        "use_tls": True,
        "username": mail_link.get("address"),
        "password": password,
        "from_addr": mail_link.get("address"),
    }


def resolve_recipient(to_email: str, subject: str, *, sink_active: bool) -> tuple[str, str]:
    """يحدّد المستلم الفعلي والعنوان الفعلي حسب أولوية sink>dry_run_to>mail_live>None.
    يرجع (actual_recipient, actual_subject). يرفع ValueError إن تعذّر تحديد
    أي مستلم فعلي إطلاقًا (fail-closed — لا إرسال بلا قرار وضع صريح)."""
    if sink_active:
        # في وضع sink، المستلم الحقيقي يبقى كما هو (mailpit يستقبل أي عنوان
        # داخل الشبكة الداخلية، بلا حاجة لإعادة توجيه المستلم نفسه) — لكن
        # نُبقي العنوان كما هو أيضًا (بلا بادئة DRY-RUN، فالسياق كله تطوير).
        return to_email, subject

    dry_run_to = os.environ.get("DRY_RUN_TO", "").strip()
    if dry_run_to:
        return dry_run_to, f"[DRY-RUN to: {to_email}] {subject}"

    mail_live = os.environ.get("MAIL_LIVE", "false").strip().lower() == "true"
    if mail_live:
        return to_email, subject

    raise ValueError("لا وضع إرسال فعّال (لا sink، لا DRY_RUN_TO، ولا MAIL_LIVE=true) — الإرسال يُمنع افتراضيًا")


def backoff_send_after(attempts: int) -> datetime:
    minutes = BACKOFF_MINUTES.get(attempts, 5)
    return datetime.now(timezone.utc) + timedelta(minutes=minutes)


def build_mime_message(
    *,
    from_addr: str,
    to_addr: str,
    cc_addr: str | None,
    subject: str,
    body_text: str,
    attachments: list[dict],
) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = to_addr
    if cc_addr:
        msg["Cc"] = cc_addr
    msg["Subject"] = subject
    msg.set_content(body_text)

    for att in attachments or []:
        path = att.get("path")
        if not path:
            continue
        p = Path(path)
        if not p.is_file():
            logger.warning("مرفق مفقود، يُتخطّى: %s", path)
            continue
        data = p.read_bytes()
        filename = att.get("filename") or p.name
        msg.add_attachment(data, maintype="application", subtype="pdf", filename=filename)

    return msg


def _smtp_send(transport: dict, msg: EmailMessage, recipients: list[str]) -> None:
    host = transport["host"]
    port = transport["port"]
    with smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT_SECONDS) as smtp:
        if transport.get("use_tls"):
            smtp.starttls()
        if transport.get("username") and transport.get("password"):
            smtp.login(transport["username"], transport["password"])
        smtp.send_message(msg, from_addr=transport["from_addr"], to_addrs=recipients)


def _claim_due_batch(engine: Engine, limit: int) -> list[dict]:
    """يطالب بدفعة مستحقة عبر CTE بـFOR UPDATE SKIP LOCKED — يسمح بتشغيل
    عدة استدعاءات send_tick متوازية (SEND_WORKERS) بلا تصادم على نفس الصفوف،
    ويحدّد locked_until (LOCK_DURATION_MINUTES) كشبكة أمان ضد عملية تعطّلت
    منتصف الإرسال (صف مقفل لن يُطالَب به مجددًا حتى تنتهي مهلة القفل)."""
    now = datetime.now(timezone.utc)
    lock_until = now + timedelta(minutes=LOCK_DURATION_MINUTES)
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                WITH due AS (
                    SELECT id FROM send_queue
                    WHERE status = 'queued' AND send_after <= :now
                      AND (locked_until IS NULL OR locked_until < :now)
                    ORDER BY send_after
                    LIMIT :limit
                    FOR UPDATE SKIP LOCKED
                )
                UPDATE send_queue sq SET status = 'sending', locked_until = :lock_until
                FROM due WHERE sq.id = due.id
                RETURNING sq.id, sq.customer_id, sq.opportunity_id, sq.job_id, sq.to_email,
                          sq.cc_email, sq.subject, sq.body_text, sq.attachments, sq.attempts, sq.synthetic
                """
            ),
            {"now": now, "limit": limit, "lock_until": lock_until},
        ).mappings().all()
    return [dict(r) for r in rows]


def _fetch_mail_link(conn, customer_id: int) -> dict | None:
    row = conn.execute(
        text("SELECT * FROM mail_links WHERE customer_id = :id"), {"id": customer_id}
    ).mappings().first()
    return dict(row) if row else None


def _mark_success(conn, row: dict, message_id: str) -> None:
    conn.execute(
        text(
            """
            UPDATE send_queue SET status = 'sent', message_id = :mid, locked_until = NULL, error = NULL
            WHERE id = :id
            """
        ),
        {"mid": message_id, "id": row["id"]},
    )
    if row.get("synthetic"):
        return

    job_row = conn.execute(text("SELECT company_name FROM jobs WHERE id = :id"), {"id": row["job_id"]}).first()
    company_name = job_row[0] if job_row else None
    ck = normalize_company_key(company_name)

    conn.execute(
        text(
            """
            INSERT INTO applications (
                customer_id, job_id, opportunity_id, sent_at, message_id, status, company_key, created_at
            ) VALUES (:cid, :jid, :oid, now(), :mid, 'sent', :ck, now())
            """
        ),
        {
            "cid": row["customer_id"],
            "jid": row["job_id"],
            "oid": row["opportunity_id"],
            "mid": message_id,
            "ck": ck,
        },
    )
    if ck:
        conn.execute(
            text(
                """
                INSERT INTO company_cooldowns (company_key, customer_id, last_sent_at)
                VALUES (:ck, :cid, now())
                ON CONFLICT (company_key, customer_id) DO UPDATE SET last_sent_at = now()
                """
            ),
            {"ck": ck, "cid": row["customer_id"]},
        )


def _mark_failure(conn, row: dict, error_text: str) -> None:
    attempts = (row.get("attempts") or 0) + 1
    if attempts >= MAX_ATTEMPTS:
        conn.execute(
            text(
                """
                UPDATE send_queue SET status = 'failed', attempts = :attempts, error = :err, locked_until = NULL
                WHERE id = :id
                """
            ),
            {"attempts": attempts, "err": error_text[:2000], "id": row["id"]},
        )
        if not row.get("synthetic"):
            # استرداد الرصيد المخصوم مسبقًا (send_builder._debit_one_credit) —
            # فشل نهائي بعد MAX_ATTEMPTS محاولات يعني عدم إرسال الرسالة إطلاقًا.
            current = conn.execute(
                text("SELECT balance FROM wallets WHERE customer_id = :id FOR UPDATE"),
                {"id": row["customer_id"]},
            ).first()
            new_balance = (current[0] if current else 0) + 1
            conn.execute(
                text("UPDATE wallets SET balance = :b, updated_at = now() WHERE customer_id = :id"),
                {"b": new_balance, "id": row["customer_id"]},
            )
            conn.execute(
                text(
                    """
                    INSERT INTO ledger (customer_id, delta, reason, ref_id, created_at)
                    VALUES (:cid, 1, 'adjustment', :ref, now())
                    """
                ),
                {"cid": row["customer_id"], "ref": f"send_queue_failed:{row['id']}"},
            )
        return

    conn.execute(
        text(
            """
            UPDATE send_queue SET status = 'queued', attempts = :attempts, error = :err,
                locked_until = NULL, send_after = :send_after
            WHERE id = :id
            """
        ),
        {
            "attempts": attempts,
            "err": error_text[:2000],
            "send_after": backoff_send_after(attempts),
            "id": row["id"],
        },
    )


def _process_row(engine: Engine, row: dict) -> bool:
    with engine.connect() as conn:
        mail_link = _fetch_mail_link(conn, row["customer_id"])

    transport = resolve_transport(row["customer_id"], mail_link)
    if transport is None:
        with engine.begin() as conn:
            _mark_failure(conn, row, "لا يوجد صندوق بريد صالح لهذا العميل (mail_link غير موجود/غير موثّق)")
        return False

    try:
        actual_recipient, actual_subject = resolve_recipient(
            row["to_email"], row["subject"], sink_active=(transport["mode"] == "sink")
        )
    except ValueError as exc:
        with engine.begin() as conn:
            _mark_failure(conn, row, str(exc))
        return False

    # CC العميل دومًا (الدليل: "CC للعميل على كل تقديم") — بصرف النظر عن
    # وضع النقل (sink/dry-run/حقيقي)، طالما cc_email موجود بالصفّ أصلًا.
    cc_email = row.get("cc_email")
    recipients = [actual_recipient] + ([cc_email] if cc_email else [])

    msg = build_mime_message(
        from_addr=transport["from_addr"],
        to_addr=actual_recipient,
        cc_addr=cc_email,
        subject=actual_subject,
        body_text=row["body_text"],
        attachments=row.get("attachments") or [],
    )
    message_id = msg.get("Message-ID") or f"<masar-{row['id']}@masar.local>"
    if "Message-ID" not in msg:
        msg["Message-ID"] = message_id

    try:
        _smtp_send(transport, msg, recipients)
    except Exception as exc:  # noqa: BLE001 — أي خطأ SMTP يُعامَل كفشل قابل لإعادة المحاولة
        logger.warning("فشل إرسال send_queue #%s: %s", row["id"], exc)
        with engine.begin() as conn:
            _mark_failure(conn, row, str(exc))
        return False

    with engine.begin() as conn:
        _mark_success(conn, row, message_id)
    return True


def send_tick(*, limit: int = CLAIM_BATCH_LIMIT_DEFAULT, ignore_window: bool = False, engine: Engine | None = None) -> dict:
    """نقطة الدخول الرئيسية — تُستدعى كل دقيقة. `ignore_window=True` يُستخدم
    فقط من `/admin/mail/send-now` و`/admin/mail/load-test` للاختبار اليدوي
    الفوري بلا انتظار نافذة الإرسال الحقيقية (الإرسال الفعلي بالمقابل يبقى
    محكومًا بأن send_after لكل صفّ كان قد حُسِب أصلًا ضمن النافذة من
    send_builder.py — تجاوز الفحص هنا لا يُعيد جدولة صفوف قديمة خارج
    النافذة، فقط يسمح بمعالجتها الآن بدل الانتظار لدورة داخل
    النافذة)."""
    engine = engine or get_engine()

    if not ignore_window:
        riyadh_now = pacing.to_riyadh_naive(pacing.utc_now())
        sink_active = bool(os.environ.get("MAIL_SINK_SMTP", "").strip())
        if not sink_active and not pacing.is_in_window(riyadh_now):
            return {"ok": True, "sent": 0, "failed": 0, "note": "خارج نافذة الإرسال"}

    batch = _claim_due_batch(engine, limit)
    if not batch:
        return {"ok": True, "sent": 0, "failed": 0, "claimed": 0}

    # معالجة متوازية (حتى SEND_WORKERS مسارات) — كل صفّ اتصال SMTP مستقل
    # (اتصال جديد لكل رسالة أصلًا بـ_smtp_send) ومعاملة DB منفصلة به عبر
    # engine.begin() الخاصة بكل _process_row، فلا تشارك اتصالًا واحدًا بين
    # الخيوط؛ هذا ما يحقق معدّل الاستنزاف (drain rate) المطلوب باختبار التحميل.
    sent = 0
    failed = 0
    workers = min(SEND_WORKERS, len(batch))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_process_row, engine, row): row for row in batch}
        for future in as_completed(futures):
            row = futures[future]
            try:
                ok = future.result()
                if ok:
                    sent += 1
                else:
                    failed += 1
            except Exception:
                logger.exception("خطأ غير متوقع أثناء معالجة send_queue #%s", row.get("id"))
                failed += 1

    result = {"ok": True, "claimed": len(batch), "sent": sent, "failed": failed}
    logger.info("دورة إرسال: %s", result)
    return result
