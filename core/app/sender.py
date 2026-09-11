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
import threading
import time
from collections import defaultdict
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


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


# B6 (تصليب الحمل، docs/reports/B6-executor.md): كل ثوابت التزامن/الوتيرة
# التالية أصبحت قابلة للضبط عبر متغيّرات بيئة (القيم الافتراضية = نفس القيم
# الثابتة القديمة، بلا تغيير سلوك بلا env صريح) — تُضبَط فعليًا فقط لاختبار
# التحميل المحلي (scripts/load_test_send.py) وربما لاحقًا على الخادم إن
# احتاج ضبطًا دقيقًا لحجم أكبر من 1,500 عميل.
SEND_WORKERS = _int_env("SEND_WORKERS", 20)
LOCK_DURATION_MINUTES = 5
SMTP_TIMEOUT_SECONDS = 15
# تراجع بسيط بين المحاولات: محاولة 1 فشلت → أعد بعد 1 دقيقة؛ محاولة 2 فشلت
# → أعد بعد 5 دقائق؛ محاولة 3 فشلت → فشل نهائي (MAX_ATTEMPTS، "dead-letter":
# الصفّ يبقى بحالة 'failed' نهائيًا، يظهر بعدّاد "failures by class" على
# /admin/send/stats، ولا يُعاد التقاطه أبدًا من _claim_due_batch لاحقًا).
BACKOFF_MINUTES = {1: 1, 2: 5}
MAX_ATTEMPTS = _int_env("SEND_MAX_ATTEMPTS", 3)

# حدّ إرسال Gmail: ≤ 1 رسالة/6 ثوانِِ لكل صندوق (الدليل: تسخين 6/12/16/22 +
# نافذة إرسال بطيئة أصلًا 8-15 دقيقة/رسالة لكل عميل بوضع الإنتاج الطبيعي —
# لكن *استرداد* Backlog متراكم (عميل عاد بعد انقطاع، أو اختبار تحميل) قد
# يُطالب بعدة صفوف لنفس العميل بنفس الدفعة؛ هذا الحارس يمنع إرسالين لنفس
# صندوق البريد أقرب من MAILBOX_MIN_INTERVAL_SECONDS مهما كانت حالة الطابور).
MAILBOX_MIN_INTERVAL_SECONDS = _float_env("SEND_MAILBOX_MIN_INTERVAL_SECONDS", 6.0)

# سقف عام (كل الصناديق معًا) رسائل/ثانية — يحمي Postgres/SMTP sink (mailpit
# أو محلي) من انفجار تزامن ThreadPoolExecutor (حتى SEND_WORKERS خيطًا) عند
# استرداد طابور متراكم ضخم (اختبار التحميل: 25,500 صف). القيمة الافتراضية
# سخيّة عمدًا (أعلى بكثير من معدّل الإنتاج الطبيعي ~0.03 رسالة/ثانية لكل
# عميل×1500) — الهدف حماية الموارد المشتركة (2 vCPU) لا تقييد وتيرة العميل
# الفعلية (تلك محكومة أصلًا بـpacing.py/send_builder.py وقت البناء).
GLOBAL_RATE_PER_SECOND = _float_env("SEND_GLOBAL_RATE_PER_SECOND", 20.0)
GLOBAL_RATE_BURST = _float_env("SEND_GLOBAL_RATE_BURST", GLOBAL_RATE_PER_SECOND * 2)


# ---------------------------------------------------------------------------
# B6 — مقاييس زمن التشغيل (in-process، تُصفّر عند إعادة تشغيل العملية) +
# محدّدا الوتيرة (per-mailbox + عام). كل هذا داخل عملية واحدة فقط (لا يُشارك
# بين core وcore-scheduler ولا بين خيوط عمليات مختلفة) — كافِ لأن الإرسال
# الفعلي كله يحدث داخل core-scheduler وحدها (send_tick يُستدعى من هناك، أو
# يدويًا من /admin/mail/send-now على core نفسها للاختبار فقط).
# ---------------------------------------------------------------------------

_metrics_lock = threading.Lock()
_metrics: dict = {
    "sent_total": 0,
    "failed_total": 0,
    "mailbox_rate_limit_hits": 0,
    "failures_by_class": defaultdict(int),
}

_mailbox_lock = threading.Lock()
_mailbox_last_sent_monotonic: dict[int, float] = {}

_global_rate_lock = threading.Lock()
_global_rate_tokens = GLOBAL_RATE_BURST
_global_rate_last_check = time.monotonic()


def _classify_error(error_text: str) -> str:
    """تصنيف تقريبي بسيط لرسالة خطأ SMTP لعدّاد "failures by class" —
    مطابقة كلمات مفتاحية شائعة على اسم الاستثناء/الرسالة، لا تحليل عميق."""
    low = (error_text or "").lower()
    if "authenticationerror" in low or "auth" in low or "535" in low:
        return "auth"
    if "timeout" in low or "timed out" in low:
        return "timeout"
    if "recipientsrefused" in low or "recipient" in low:
        return "recipient_refused"
    if "connection" in low or "refused" in low or "reset" in low:
        return "connection"
    if "لا يوجد صندوق بريد صالح" in error_text or "mail_link" in low:
        return "no_mailbox"
    return "other"


def get_runtime_metrics() -> dict:
    """لقطة عدّادات هذه العملية منذ آخر إقلاع — تُستهلك من
    core/app/send_stats_api.py (`GET /admin/send/stats`)."""
    with _metrics_lock:
        return {
            "sent_total": _metrics["sent_total"],
            "failed_total": _metrics["failed_total"],
            "mailbox_rate_limit_hits": _metrics["mailbox_rate_limit_hits"],
            "failures_by_class": dict(_metrics["failures_by_class"]),
        }


def _record_sent() -> None:
    with _metrics_lock:
        _metrics["sent_total"] += 1


def _record_failed(error_text: str) -> None:
    with _metrics_lock:
        _metrics["failed_total"] += 1
        _metrics["failures_by_class"][_classify_error(error_text)] += 1


def _throttle_mailbox(customer_id: int) -> bool:
    """يمنع إرسالين لنفس صندوق البريد (customer_id) أقرب من
    MAILBOX_MIN_INTERVAL_SECONDS — يحجز الفتحة الزمنية تفاؤليًا تحت القفل
    (لا سباق بين خيطين يطالبان بصفّين لنفس العميل بنفس الدفعة)، ثم ينام
    خارج القفل. يرجع True إن حدث انتظار فعلي (لعدّاد mailbox_rate_limit_hits)."""
    if MAILBOX_MIN_INTERVAL_SECONDS <= 0:
        return False
    with _mailbox_lock:
        now = time.monotonic()
        last_reserved = _mailbox_last_sent_monotonic.get(customer_id)
        # الفتحة المحجوزة لهذا الإرسال = آخر فتحة محجوزة لنفس العميل (إن
        # كانت بالمستقبل) وإلا الآن — يحجزها فورًا تحت القفل (لا سباق بين خيطين).
        reserved_slot = last_reserved if (last_reserved is not None and last_reserved > now) else now
        wait = max(0.0, reserved_slot - now)
        _mailbox_last_sent_monotonic[customer_id] = reserved_slot + MAILBOX_MIN_INTERVAL_SECONDS
    if wait > 0:
        time.sleep(wait)
        with _metrics_lock:
            _metrics["mailbox_rate_limit_hits"] += 1
        return True
    return False


def _throttle_global() -> None:
    """محدّد وتيرة عام (token bucket) عبر كل الصناديق معًا — GLOBAL_RATE_PER_SECOND
    رمز/ثانية بسقف احتياطي (burst) GLOBAL_RATE_BURST."""
    if GLOBAL_RATE_PER_SECOND <= 0:
        return
    global _global_rate_tokens, _global_rate_last_check
    while True:
        with _global_rate_lock:
            now = time.monotonic()
            elapsed = now - _global_rate_last_check
            _global_rate_last_check = now
            _global_rate_tokens = min(
                GLOBAL_RATE_BURST, _global_rate_tokens + elapsed * GLOBAL_RATE_PER_SECOND
            )
            if _global_rate_tokens >= 1.0:
                _global_rate_tokens -= 1.0
                return
            sleep_for = (1.0 - _global_rate_tokens) / GLOBAL_RATE_PER_SECOND
        time.sleep(min(sleep_for, 0.5))


def is_dry_run() -> bool:
    """DRY_RUN هو الوضع الافتراضي دومًا (الدليل: "DRY_RUN افتراضي دومًا") —
    يُعتبر معطّلاً (إرسال حي فعلي مسموح به) فقط حين MAIL_LIVE=true صراحة
    بالبيئة. يُستخدَم هنا (F2 — تجاوز نافذة الإرسال التطويري) وبـmail_api.py
    (F1 — تجاوز التحقق الحي لـSMTP/IMAP عبر skip_verify) — مصدر حقيقة واحد
    لمعنى "DRY_RUN فعّال" بكل الكود (مراجعة حيّة docs/reports/B3B4-live-review.md)."""
    return os.environ.get("MAIL_LIVE", "false").strip().lower() != "true"


def _send_window_override_active() -> bool:
    """تجاوز نافذة الإرسال المخصّص للتطوير فقط (F2 بمراجعة B3B4-live-review.md،
    استنتاج متوسط 2): يُشترط كلا الأمرين معًا — MAIL_IGNORE_SEND_WINDOW=true
    صراحة بالبيئة **و** DRY_RUN فعّال (is_dry_run()) — لا يعمل هذا التجاوز
    إطلاقًا بوضع الإنتاج الحي (MAIL_LIVE=true) بصرف النظر عن قيمة المتغيّر،
    منعًا لتسرّبه للإنتاج بالخطأ (نسيان إزالته من .env لا يكفي وحده لتفعيله)."""
    override = os.environ.get("MAIL_IGNORE_SEND_WINDOW", "false").strip().lower() == "true"
    return override and is_dry_run()


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


def find_missing_attachments(attachments: list[dict]) -> list[str]:
    """B9/A2: يتحقّق من وجود كل ملفات المرفقات (السيرة الذاتية غالبًا) فعليًا
    على القرص *قبل* بناء الرسالة والاتصال بـSMTP.

    المشكلة التي يُصلحها هذا الفحص (اكتُشفت بمراجعة الجاهزية قبل التجربة
    الحيّة، 2026-09-11): build_mime_message أعلاه كان يتجاوز أي مرفق مفقود
    بصمت (سطر تحذير بالسجلّ فقط عبر logger.warning) ثم يُكمل بناء الرسالة
    وإرسالها فعليًا — أي أن رسالة تقديم كانت تصل فعليًا للشركة **بلا سيرة
    ذاتية مرفقة إطلاقًا** إن حُذف/فُقد الملف بين بناء send_queue وتنفيذ
    الإرسال، دون أي إشعار أو إعادة محاولة، رغم أن إرفاق السيرة الذاتية هو
    جوهر الخدمة تقريبًا.

    القرار (بموافقة أحمد الصريحة): الصفّ يجب أن **يفشل ويُعاد لاحقًا** (نفس
    منطق _mark_failure/backoff الموجود أصلًا لأي فشل SMTP)، لا أن يُرسَل
    ناقصًا بصمت — راجع _process_row أدناه لموضع الاستدعاء (قبل أي اتصال
    SMTP فعلي، حتى لا يُستهلك throttle/رصيد على محاولة كانت ستُرسَل ناقصة).
    """
    missing = []
    for att in attachments or []:
        path = att.get("path")
        if not path or not Path(path).is_file():
            missing.append(att.get("filename") or path or "(بلا مسار)")
    return missing


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
    منتصف الإرسال (صف مقفول لن يُطالَب به مجددًا حتى تنتهي مهلة القفل).

    تصحيح Critical/High 1 (F1) بمراجعة B4 الأوفلاين (docs/reports/B4-offline-review.md):
    الشرط `status = 'queued'` وحده كان يمنع فعليًا استرداد صفّ عالق بحالة
    'sending' (عملية أُنهيت — SIGTERM/تعطّل — بين نجاح SMTP فعليًا وتنفيذ
    _mark_success) رغم أن التعليق أعلاه يصف "شبكة أمان" تعتمد صراحة على
    انتهاء locked_until؛ الصفّ كان يبقى عالقًا للأبد بلا استرداد إطلاقًا (لا
    ازدواج، لكن فقدان فعلي للصفّ). الآن `status IN ('queued','sending')` مع
    نفس فحص `locked_until` يُطبّق فعليًا هذه الشبكة كما تصفها الوثائق —
    والحماية من الازدواج الفعلي (رسالة SMTP خرجت فعلًا قبل الانهيار) تقع
    بعدها في `_process_row` (فحص idempotency ضد `applications` قبل أي اتصال
    SMTP جديد لصفّ استُعيد بهذا المسار)."""
    now = datetime.now(timezone.utc)
    lock_until = now + timedelta(minutes=LOCK_DURATION_MINUTES)
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                WITH due AS (
                    SELECT id FROM send_queue
                    WHERE status IN ('queued', 'sending') AND send_after <= :now
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


def _existing_application(conn, row: dict) -> dict | None:
    """تحقّق idempotency (تصحيح High/F1 بمراجعة B4 الأوفلاين): هل توجد أصلًا
    صفّ `applications` ناجح لنفس (customer_id, job_id, opportunity_id)؟ إن
    وُجد فهذا يعني أن الرسالة **خرجت فعليًا عبر SMTP بدورة سابقة** (applications
    يُدرج فقط من `_mark_success` بعد نجاح `_smtp_send`) وأن الانهيار الذي
    ترك صفّ send_queue عالقًا بحالة 'sending' حدث *بعد* الإرسال الفعلي، لا
    قبله — تجنّب اتصال SMTP جديد كليًا في هذه الحالة (لا يستهلك رصيدًا إضافيًا
    أصلًا، فقط يمنع رسالة مزدوجة فعلية لنفس الشركة). صفوف synthetic
    (job_id/opportunity_id فارغان دومًا) لا تُنشئ applications أبدًا فلا
    تتطابق هنا بأمان.

    يرجع القاموس (وليس message_id مباشرة) عمدًا: العمود قابل للـNULL
    بالمخطّط، وفحص `is not None` على القيمة نفسها كان سيُخطئ في التعامل مع
    صفّ موجود فعليًا لكن message_id به NULL كأنه "لا يوجد صفّ إطلاقًا"."""
    if row.get("job_id") is None or row.get("opportunity_id") is None:
        return None
    existing = conn.execute(
        text(
            """
            SELECT message_id FROM applications
            WHERE customer_id = :cid AND job_id = :jid AND opportunity_id = :oid
            ORDER BY id LIMIT 1
            """
        ),
        {"cid": row["customer_id"], "jid": row["job_id"], "oid": row["opportunity_id"]},
    ).mappings().first()
    return dict(existing) if existing is not None else None


def _mark_already_sent(conn, row: dict, message_id: str) -> None:
    """صفّ send_queue استُعيد (reclaimed) وتبيّن أن تطبيقًا ناجحًا مسجّل أصلًا
    لنفس الفرصة (`_existing_application_message_id`) — يُعلّم كـ'sent'
    مباشرة بلا اتصال SMTP جديد ولا إدراج applications/company_cooldowns
    مكرّر (السجلّ الأصلي من المحاولة الناجحة الأولى كافِ ودقيق)."""
    conn.execute(
        text(
            """
            UPDATE send_queue SET status = 'sent', message_id = :mid, locked_until = NULL, error = NULL,
                completed_at = now()
            WHERE id = :id
            """
        ),
        {"mid": message_id or f"<masar-{row['id']}@masar.local>", "id": row["id"]},
    )
    if row.get("opportunity_id") is not None:
        conn.execute(
            text("UPDATE opportunities SET status = 'sent' WHERE id = :id"),
            {"id": row["opportunity_id"]},
        )


def _mark_success(conn, row: dict, message_id: str) -> None:
    # B6: completed_at (ترحيل 0011) يُغذّي مقاييس "أُرسل/دقيقة" و"عمر أقدم
    # صفّ" على /admin/send/stats — يشمل صفوف synthetic (اختبار التحميل) التي
    # لا تصل أبدًا لجدول applications (راجع `if row.get("synthetic"): return` أدناه).
    conn.execute(
        text(
            """
            UPDATE send_queue SET status = 'sent', message_id = :mid, locked_until = NULL, error = NULL,
                completed_at = now()
            WHERE id = :id
            """
        ),
        {"mid": message_id, "id": row["id"]},
    )
    # المصدر الحقيقي الوحيد لـ"أُرسل فعليًا" هو هذه النقطة (بعد نجاح SMTP
    # فعليًا) — تصحيح Critical/High 2 بمراجعة B4 الأوفلاين: send_builder.py
    # لم يعد يضبط opportunities.status='sent' وقت البناء، بل 'queued' فقط.
    if row.get("opportunity_id") is not None:
        conn.execute(
            text("UPDATE opportunities SET status = 'sent' WHERE id = :id"),
            {"id": row["opportunity_id"]},
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
                UPDATE send_queue SET status = 'failed', attempts = :attempts, error = :err, locked_until = NULL,
                    completed_at = now()
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
        # تصحيح Critical/High 2 (نفس منطق _mark_success أعلاه بالاتجاه
        # المعاكس): فشل نهائي يعني أن التقديم لم يُرسَل فعليًا إطلاقًا —
        # opportunities.status='skipped' لا تبقى 'queued' للأبد (تُفسد أي
        # تقرير/إحصاء يعتمدها كمصدر حقيقة).
        if row.get("opportunity_id") is not None:
            conn.execute(
                text("UPDATE opportunities SET status = 'skipped' WHERE id = :id"),
                {"id": row["opportunity_id"]},
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
        existing_application = _existing_application(conn, row)

    if existing_application is not None:
        # فحص idempotency (تصحيح High/F1): صفّ استُعيد (locked_until انتهى،
        # status كان 'sending' — راجع _claim_due_batch) وتطبيق ناجح مسجّل
        # أصلًا لنفس الفرصة — أُرسلت الرسالة فعليًا بمحاولة سابقة قبل أن
        # يُقتَل العملية بين نجاح SMTP وتنفيذ _mark_success؛ لا نتصل بـSMTP
        # مجددًا إطلاقًا (يمنع رسالة مزدوجة فعلية لنفس الشركة).
        logger.info(
            "send_queue #%s: تطبيق ناجح موجود أصلًا لنفس الفرصة (idempotency) — تعليم كـsent بلا إرسال جديد",
            row["id"],
        )
        with engine.begin() as conn:
            _mark_already_sent(conn, row, existing_application.get("message_id"))
        return True

    transport = resolve_transport(row["customer_id"], mail_link)
    if transport is None:
        error_text = "لا يوجد صندوق بريد صالح لهذا العميل (mail_link غير موجود/غير موثّق)"
        with engine.begin() as conn:
            _mark_failure(conn, row, error_text)
        _record_failed(error_text)
        return False

    # B9/A2: فحص وجود ملفات المرفقات فعليًا *قبل* أي throttle/اتصال SMTP —
    # مرفق مفقود يُفشل الصفّ (يُعاد لاحقًا بنفس منطق backoff) بدل إرسال
    # الرسالة للشركة ناقصة بصمت. راجع find_missing_attachments أعلاه.
    missing_attachments = find_missing_attachments(row.get("attachments") or [])
    if missing_attachments:
        error_text = "مرفق(ات) مفقودة عند الإرسال، يُعاد لاحقًا لإتاحة الوقت لتوفّرها: " + ", ".join(
            missing_attachments
        )
        with engine.begin() as conn:
            _mark_failure(conn, row, error_text)
        _record_failed(error_text)
        return False

    try:
        actual_recipient, actual_subject = resolve_recipient(
            row["to_email"], row["subject"], sink_active=(transport["mode"] == "sink")
        )
    except ValueError as exc:
        with engine.begin() as conn:
            _mark_failure(conn, row, str(exc))
        _record_failed(str(exc))
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

    # B6 — بند 4 (تصليب الحمل): سقف عام (كل الصناديق) ثم سقف لكل صندوق
    # (≤1 رسالة/MAILBOX_MIN_INTERVAL_SECONDS ثوانِِ) — بهذا الترتيب تحديدًا،
    # قبل اتصال SMTP الفعلي مباشرة (لا قبل بناء الرسالة، حتى لا يُهدر وقت
    # انتظار على صفّ سيفشل أصلًا بفحوصات transport/recipient أعلاه).
    _throttle_global()
    _throttle_mailbox(row["customer_id"])

    try:
        _smtp_send(transport, msg, recipients)
    except Exception as exc:  # noqa: BLE001 — أي خطأ SMTP يُعامَل كفشل قابل لإعادة المحاولة
        logger.warning("فشل إرسال send_queue #%s: %s", row["id"], exc)
        with engine.begin() as conn:
            _mark_failure(conn, row, str(exc))
        _record_failed(str(exc))
        return False

    with engine.begin() as conn:
        _mark_success(conn, row, message_id)
    _record_sent()
    return True


def get_queue_stats(engine: Engine | None = None) -> dict:
    """B6 — إحصاءات طابور الإرسال من قاعدة البيانات (لا تعتمد على عدّادات
    in-process فتبقى صحيحة عبر إعادة تشغيل العملية أو عدة عمليات): عمق
    الطابور بكل حالة، عمر أقدم صفّ مستحق لم يُعالَج بعد، وعدد المُرسَل
    بآخر 60/300 ثانية (باستخدام send_queue.completed_at، ترحيل 0011 —
    يشمل صفوف synthetic اختبار التحميل)."""
    engine = engine or get_engine()
    with engine.connect() as conn:
        by_status = conn.execute(
            text("SELECT status, count(*) FROM send_queue GROUP BY status")
        ).all()
        oldest_due = conn.execute(
            text(
                "SELECT min(send_after) FROM send_queue WHERE status = 'queued' AND send_after <= now()"
            )
        ).scalar()
        sent_last_60s = conn.execute(
            text(
                "SELECT count(*) FROM send_queue WHERE status = 'sent' AND completed_at >= now() - interval '60 seconds'"
            )
        ).scalar()
        sent_last_5min = conn.execute(
            text(
                "SELECT count(*) FROM send_queue WHERE status = 'sent' AND completed_at >= now() - interval '5 minutes'"
            )
        ).scalar()

    now = datetime.now(timezone.utc)
    oldest_due_age_seconds = None
    if oldest_due is not None:
        oldest_due_age_seconds = round((now - oldest_due).total_seconds(), 1)

    return {
        "by_status": {r[0]: r[1] for r in by_status},
        "oldest_due_age_seconds": oldest_due_age_seconds,
        "sent_last_60s": int(sent_last_60s or 0),
        "sent_per_min_last_5min": round((sent_last_5min or 0) / 5.0, 2),
    }


def send_tick(*, limit: int = CLAIM_BATCH_LIMIT_DEFAULT, ignore_window: bool = False, engine: Engine | None = None) -> dict:
    """نقطة الدخول الرئيسية — تُستدعى كل دقيقة. `ignore_window=True` يُستخدم
    فقط من `/admin/mail/send-now` و`/admin/mail/load-test` للاختبار اليدوي
    الفوري بلا انتظار نافذة الإرسال الحقيقية (الإرسال الفعلي بالمقابل يبقى
    محكومًا بأن send_after لكل صفّ كان قد حُسِب أصلًا ضمن النافذة من
    send_builder.py — تجاوز الفحص هنا لا يُعيد جدولة صفوف قديمة خارج
    النافذة، فقط يسمح بمعالجتها الآن بدل الانتظار لدورة داخل
    النافذة).

    تصحيح F2 (مراجعة حيّة docs/reports/B3B4-live-review.md، استنتاج متوسط 2):
    فحص النافذة أصبح **غير مشروط** بوجود MAIL_SINK_SMTP (كان يتجاوز النافذة
    كليًا طالما sink مفعّل — وهو الافتراضي الدائم بـdocker-compose.yml، ما
    يعني أن الإرسال الفعلي لم يكن محكومًا بالنافذة إطلاقًا بأي بيئة تستخدم
    sink) — الآن يستخدم نفس `pacing.is_in_window` غير المشروط الذي تستخدمه
    `scheduler_main.run_queue_builder_job` تمامًا، بصرف النظر عن sink/dry-run/
    حقيقي. المسار الوحيدان لتجاوز النافذة الآن: `ignore_window=True` الصريح
    (الاستدعاء الإداري اليدوي)، أو `MAIL_IGNORE_SEND_WINDOW=true` **مع**
    DRY_RUN فعّال معًا (`_send_window_override_active`، تطوير محلي فقط)."""
    engine = engine or get_engine()

    if not ignore_window and not _send_window_override_active():
        riyadh_now = pacing.to_riyadh_naive(pacing.utc_now())
        if not pacing.is_in_window(riyadh_now):
            return {"ok": True, "sent": 0, "failed": 0, "note": "خارج نافذة الإرسال"}

    batch = _claim_due_batch(engine, limit)
    if not batch:
        return {"ok": True, "sent": 0, "failed": 0, "claimed": 0}

    # معالجة متوازية (حتى SEND_WORKERS مسارات) — كل صفّ اتصال SMTP مستقل
    # (اتصال جديد لكل رسالة أصلًا بـ_smtp_send) ومعاملة DB منفصلة به عبر
    # engine.begin() الخاصة بكل _process_row، فلا تشارك اتصالًا واحدًا بين
    # الخيوط؛ هذا ما يحقّق معدّل الاستنزاف (drain rate) المطلوب باختبار التحميل.
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
