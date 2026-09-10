"""
Masar Core — قارئ الوارد (B4، الدليل: "الارتدادات لا تُحتسب، القيمة
تُستردّ؛ اكتشاف الردود/المقابلات لإشعار العميل لاحقًا").

يُستدعى كل 15 دقيقة من core/app/scheduler_main.py وعبر `POST /inbox/run-now`
يدويًا. لكل صندوق بريد نشط (mail_links.status='ok')، يفتح اتصال IMAP
(للقراءة فقط — لا يحذف ولا يعدّل أي رسالة بصندوق العميل)، يبحث عن رسائل
جديدة، يصنّف كل رسالة (ارتداد/رد/مقابلة/أخرى)، يسجّلها بجدول inbox_events
(فريدة على customer_id+message_id — idempotent، إعادة تشغيل الدورة لا تُكرّر
تسجيل نفس الرسالة)، ولحالة الارتداد تحديدًا: يحدّث `applications.status=
'bounced'` لتطبيق الأصل (عبر Message-ID المطابق) ويرد رصيدًا واحدًا (ledger
reason='bounce_refund').

كل دوال التصنيف (classify_kind وما تحته) **نقية بالكامل** — لا IMAP ولا
قاعدة بيانات — قابلة للاختبار المباشر (core/tests/test_inbox_classifier.py).

B6 (تصليب الحمل لـ1,500 عميل، docs/reports/B6-executor.md، بند 5 —
"partition mail links across scheduler ticks... never scan all links every
tick; skip links in error state with backoff"):
    1. **متابعة UID تزايدية** (`mail_links.last_uid`، ترحيل 0011) بدل بحث
       "UNSEEN": القراءة كانت دومًا `readonly=True` (بالتصميم — لا نُعدّل
       صندوق العميل)، وهذا يعني عمليًا أن علم \\Seen **لا يتغيّر أبدًا** من
       جهتنا، فبحث "UNSEEN" كان سيُعيد كل الوارد التاريخي غير المقروء من
       جديد كل 15 دقيقة للأبد (idempotency جدول inbox_events كانت تمنع
       التكرار بقاعدة البيانات، لكن بتكلفة إعادة تنزيل/تحليل كل رسالة قديمة
       كل تكة — غير محتمل عند نمو الوارد بمرور الوقت). الآن: `UID SEARCH
       UID <last_uid+1>:*`، تزايدي حقيقي، بلا إعادة معالجة.
    2. **تقسيم عبر التكات** (`current_partition`): كل تكة (15 دقيقة) تعالج
       شريحة واحدة فقط من صناديق البريد (`id % INBOX_PARTITION_COUNT ==
       partition`, مُشتقّة حتميًا من الوقت الحالي) بدل كل الصناديق النشطة —
       يمنع 1,500 اتصال IMAP متزامن (حتى INBOX_ROUND_WORKERS في نفس اللحظة)
       كل 15 دقيقة على خادم 2 vCPU. الافتراضي 15 قسمًا → كل صندوق يُفحص مرة
       كل ~3.75 ساعة (أقل بكثير من نافذة "6 ساعات" لمعيار قبول B6، وأسرع
       بكثير من الحاجة الفعلية: الارتدادات/الردود ليست حساسة لثوانٍ).
    3. **تراجع الأخطاء** (`mail_links.error_count`/`next_check_at`): صندوق
       يفشل الاتصال يُؤجَّل بتراجع أُسّي (2^error_count دقيقة، سقف
       INBOX_BACKOFF_MAX_MINUTES) بدل إعادة محاولته كل تكة بلا جدوى.
"""
from __future__ import annotations

import email
import imaplib
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from email.header import decode_header
from email.message import Message

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app import mail_crypto
from app.discovery import get_engine

logger = logging.getLogger("masar.inbox")

IMAP_TIMEOUT_SECONDS = 15
MAX_MESSAGES_PER_MAILBOX = 50
INBOX_ROUND_WORKERS = 20

# B6 — تقسيم الصناديق عبر التكات (راجع تعليق الرأس، بند 2). قابل للضبط عبر
# البيئة (INBOX_PARTITION_COUNT<=1 يعني "بلا تقسيم" — كل صندوق كل تكة، نفس
# السلوك القديم، مفيد للاختبار المحلي/اختبار التحميل المُصغَّر).
INBOX_PARTITION_COUNT = int(os.environ.get("INBOX_PARTITION_COUNT", "15") or "15")
# يجب أن يطابق فاصل جدولة run_inbox_round_job بـscheduler_main.py (15 دقيقة)
# حتى تدور الأقسام تباعًا بلا تخطٍّ ولا تكرار — موثَّق هناك أيضًا.
INBOX_TICK_SECONDS = 15 * 60

# تراجع الأخطاء (راجع تعليق الرأس، بند 3): 2 دقيقة، 4، 8، ... حتى السقف.
INBOX_BACKOFF_BASE_MINUTES = 2
INBOX_BACKOFF_MAX_MINUTES = int(os.environ.get("INBOX_BACKOFF_MAX_MINUTES", "240") or "240")


def current_partition(count: int, *, now_ts: float | None = None) -> int:
    """القسم الحالي (0..count-1) المُشتقّ حتميًا من الوقت الحالي (لا حالة
    مخزَّنة — كل عملية/تكة تحسبه بنفس الطريقة بلا تنسيق مركزي). عدد
    الأقسام<=1 يعني "بلا تقسيم" (يرجع None يُترجَم لاحقًا لـ"كل الصناديق")."""
    if count <= 1:
        return 0
    ts = now_ts if now_ts is not None else time.time()
    return int(ts // INBOX_TICK_SECONDS) % count


def backoff_minutes(error_count: int) -> int:
    """تراجع أُسّي بسقف — error_count=1 → 2 دقيقة، 2 → 4، 3 → 8 ... حتى
    INBOX_BACKOFF_MAX_MINUTES."""
    if error_count <= 0:
        return 0
    minutes = INBOX_BACKOFF_BASE_MINUTES * (2 ** (error_count - 1))
    return min(minutes, INBOX_BACKOFF_MAX_MINUTES)

# عناوين/أسماء مرسِل شائعة لرسائل ارتداد (DSN — Delivery Status Notification).
_BOUNCE_FROM_LOCALS = ("mailer-daemon", "postmaster", "mail delivery subsystem", "mail-daemon")
_BOUNCE_SUBJECT_RE = re.compile(
    r"(delivery status notification|undelivered|failure|delivery failed|"
    r"رسالتك لم تصل|فشل التسليم|تعذر التسليم|فشل الإرسال)",
    re.IGNORECASE,
)

_INTERVIEW_KEYWORDS = (
    "interview", "schedule a call", "schedule an interview",
    "مقابلة", "موعد مقابلة", "دعوة لحضور مقابلة", "دعوتك لحضور مقابلة",
)

_MESSAGE_ID_RE = re.compile(r"Message-ID:\s*(<[^>\s]+>)", re.IGNORECASE)


def is_bounce(from_addr: str | None, subject: str | None) -> bool:
    """يكتشف رسائل الارتداد (DSN) من عنوان/اسم المرسِل الشائع (mailer-daemon
    إلخ) أو من عبارات موضوع نمطية — الفحصان مستقلان (أيّهما كافٍ)، وعنوان
    الارتداد يتغلّب حتى لو الموضوع محايدًا (والعكس)."""
    from_lower = (from_addr or "").lower()
    if any(local in from_lower for local in _BOUNCE_FROM_LOCALS):
        return True
    if subject and _BOUNCE_SUBJECT_RE.search(subject):
        return True
    return False


def is_interview_mention(subject: str | None, snippet: str | None) -> bool:
    combined = f"{subject or ''} {snippet or ''}".lower()
    return any(kw.lower() in combined for kw in _INTERVIEW_KEYWORDS)


def classify_kind(*, from_addr: str | None, subject: str | None, snippet: str | None, has_reply_reference: bool) -> str:
    """يصنّف رسالة واحدة: 'bounce' > 'interview' > 'reply' > 'other'
    (أولوية صارمة — ارتداد يتغلّب حتى لو تضمّن جسمه بالخطأ كلمة مقابلة،
    لأن جسم رسالة DSN يتضمّن أحيانًا نسخة من رسالتنا الأصلية كاملة)."""
    if is_bounce(from_addr, subject):
        return "bounce"
    if is_interview_mention(subject, snippet):
        return "interview"
    if has_reply_reference:
        return "reply"
    return "other"


def extract_original_message_id(raw_source: str | None) -> str | None:
    """يستخرج Message-ID الأصلي من مصدر رسالة خام — رسائل DSN تتضمّن غالبًا
    Message-ID الخاص برسالة DSN نفسها أولًا، ثم رسالتنا الأصلية الفاشلة
    كجزء متداخل (nested) لاحقًا؛ **آخر تطابق** هو أفضل تخمين حتمي لمطابقة
    التطبيق الأصلي المُرسَل من Masar (رسالتنا الأصلية غالبًا أعمق تداخلًا،
    تظهر لاحقًا بالنص الخام)."""
    if not raw_source:
        return None
    matches = _MESSAGE_ID_RE.findall(raw_source)
    return matches[-1] if matches else None


def _decode_mime_words(value: str | None) -> str:
    if not value:
        return ""
    try:
        parts = decode_header(value)
    except Exception:  # noqa: BLE001
        return value
    decoded = []
    for text_part, charset in parts:
        if isinstance(text_part, bytes):
            try:
                decoded.append(text_part.decode(charset or "utf-8", errors="ignore"))
            except (LookupError, TypeError):
                decoded.append(text_part.decode("utf-8", errors="ignore"))
        else:
            decoded.append(text_part)
    return "".join(decoded)


def _plain_text_snippet(msg: Message, max_chars: int = 500) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="ignore")[:max_chars]
        return ""
    if msg.get_content_type() == "text/plain":
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="ignore")[:max_chars]
    return ""


def _imap_search_since(mail_link: dict) -> tuple[list[bytes], int]:
    """يفتح اتصال IMAP فعليًا ويرجع (قائمة raw_bytes، أعلى UID رآه) —
    قراءة فقط، بلا حذف/تعديل أي علم IMAP.

    B6: `UID SEARCH UID <start>:*` تزايدي حقيقي بدل "UNSEEN" (راجع تعليق
    رأس الملف، بند 1) — `start = mail_links.last_uid + 1` (أو 1 لصندوق لم
    يُعالَج قط). ملاحظة IMAP معروفة: "UID:*" قد يُرجع آخر رسالة بالصندوق
    حتى لو UID لتلك الرسالة أقل من `start` (بعض خوادم IMAP، منها Gmail
    أحيانًا) — يُصفَّى صراحة بـ`uid >= start` أدناه احترازًا."""
    password = mail_crypto.decrypt_secret(mail_link["secret_enc"])
    host = mail_link.get("imap_host") or "imap.gmail.com"
    port = mail_link.get("imap_port") or 993
    start_uid = (mail_link.get("last_uid") or 0) + 1

    results: list[bytes] = []
    max_uid_seen = mail_link.get("last_uid") or 0
    with imaplib.IMAP4_SSL(host, port, timeout=IMAP_TIMEOUT_SECONDS) as imap:
        imap.login(mail_link["address"], password)
        imap.select("INBOX", readonly=True)
        status, data = imap.uid("search", None, f"UID {start_uid}:*")
        if status != "OK" or not data or not data[0]:
            return results, max_uid_seen
        uids = [int(u) for u in data[0].split() if u.isdigit()]
        uids = sorted(u for u in uids if u >= start_uid)[-MAX_MESSAGES_PER_MAILBOX:]
        for uid in uids:
            status, msg_data = imap.uid("fetch", str(uid).encode(), "(RFC822)")
            if status != "OK" or not msg_data or not msg_data[0]:
                continue
            raw = msg_data[0][1]
            results.append(raw)
            max_uid_seen = max(max_uid_seen, uid)
    return results, max_uid_seen


def _process_mail_link(engine: Engine, mail_link: dict) -> dict:
    customer_id = mail_link["customer_id"]
    try:
        raw_messages, max_uid_seen = _imap_search_since(mail_link)
    except Exception as exc:  # noqa: BLE001 — عزل خطأ صندوق واحد عن بقية الدورة
        # B6 — تراجع الأخطاء (راجع تعليق الرأس، بند 3): عدّاد متتالٍ +
        # next_check_at مؤجَّل — لا يُعاد الاتصال بهذا الصندوق كل تكة بلا جدوى.
        error_count = (mail_link.get("error_count") or 0) + 1
        delay_minutes = backoff_minutes(error_count)
        logger.warning(
            "فشل قراءة صندوق العميل %s (محاولة %s متتالية) — تأجيل %s دقيقة: %s",
            customer_id, error_count, delay_minutes, exc,
        )
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE mail_links SET last_error = :err, last_check_at = now(),
                        error_count = :error_count,
                        next_check_at = now() + (:delay_minutes || ' minutes')::interval
                    WHERE id = :id
                    """
                ),
                {
                    "err": str(exc)[:2000],
                    "error_count": error_count,
                    "delay_minutes": delay_minutes,
                    "id": mail_link["id"],
                },
            )
        return {"customer_id": customer_id, "processed": 0, "error": str(exc)}

    processed = 0
    bounces = 0
    for raw_bytes in raw_messages:
        try:
            msg = email.message_from_bytes(raw_bytes)
        except Exception:  # noqa: BLE001
            continue

        raw_text = raw_bytes.decode("utf-8", errors="ignore")
        message_id = msg.get("Message-ID") or extract_original_message_id(raw_text) or ""
        if not message_id:
            continue
        from_addr = _decode_mime_words(msg.get("From"))
        subject = _decode_mime_words(msg.get("Subject"))
        snippet = _plain_text_snippet(msg)
        in_reply_to = msg.get("In-Reply-To")
        references = msg.get("References")

        original_message_id = extract_original_message_id(raw_text)
        has_reply_reference = bool(in_reply_to or references)
        kind = classify_kind(
            from_addr=from_addr, subject=subject, snippet=snippet, has_reply_reference=has_reply_reference
        )

        with engine.begin() as conn:
            inserted = conn.execute(
                text(
                    """
                    INSERT INTO inbox_events (
                        customer_id, message_id, in_reply_to, kind, from_addr, subject, snippet, raw_ref, created_at
                    ) VALUES (:cid, :mid, :in_reply_to, :kind, :from_addr, :subject, :snippet, :raw_ref, now())
                    ON CONFLICT (customer_id, message_id) DO NOTHING
                    RETURNING id
                    """
                ),
                {
                    "cid": customer_id,
                    "mid": message_id[:255],
                    "in_reply_to": (in_reply_to or "")[:255] or None,
                    "kind": kind,
                    "from_addr": (from_addr or "")[:255] or None,
                    "subject": subject or None,
                    "snippet": snippet or None,
                    "raw_ref": original_message_id,
                },
            ).first()
            if inserted is None:
                continue  # مُسجّلة أصلًا بدورة سابقة (idempotent)
            processed += 1

            if kind == "bounce" and original_message_id:
                app_row = conn.execute(
                    text(
                        """
                        SELECT id, status FROM applications
                        WHERE customer_id = :cid AND message_id = :mid
                        """
                    ),
                    {"cid": customer_id, "mid": original_message_id},
                ).mappings().first()
                if app_row and app_row["status"] not in ("bounced",):
                    conn.execute(
                        text("UPDATE applications SET status = 'bounced' WHERE id = :id"),
                        {"id": app_row["id"]},
                    )
                    current = conn.execute(
                        text("SELECT balance FROM wallets WHERE customer_id = :id FOR UPDATE"),
                        {"id": customer_id},
                    ).first()
                    new_balance = (current[0] if current else 0) + 1
                    conn.execute(
                        text("UPDATE wallets SET balance = :b, updated_at = now() WHERE customer_id = :id"),
                        {"b": new_balance, "id": customer_id},
                    )
                    conn.execute(
                        text(
                            """
                            INSERT INTO ledger (customer_id, delta, reason, ref_id, created_at)
                            VALUES (:cid, 1, 'bounce_refund', :ref, now())
                            """
                        ),
                        {"cid": customer_id, "ref": f"application:{app_row['id']}"},
                    )
                    bounces += 1

    # B6: تصفير عدّاد الأخطاء + next_check_at عند نجاح فعلي — صندوق كان
    # بتراجع (backoff) يعود فورًا للجدول الطبيعي بمجرد نجاح تكة واحدة.
    # last_uid يتقدّم فقط (max_uid_seen يبدأ من القيمة القديمة نفسها إن لم
    # تصل رسائل جديدة — لا رجوع للخلف أبدًا).
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE mail_links SET last_error = NULL, last_check_at = now(),
                    error_count = 0, next_check_at = NULL, last_uid = :last_uid
                WHERE id = :id
                """
            ),
            {"last_uid": max_uid_seen, "id": mail_link["id"]},
        )

    return {"customer_id": customer_id, "processed": processed, "bounces": bounces}


def run_inbox_round(
    engine: Engine | None = None,
    customer_ids: list[int] | None = None,
    *,
    partition_count: int | None = None,
    partition_index: int | None = None,
) -> dict:
    """نقطة الدخول الرئيسية — يعالج بالتوازي (ThreadPoolExecutor، IMAP عملية
    I/O-bound تستفيد من الخيوط رغم GIL) حتى INBOX_ROUND_WORKERS اتصال متزامن،
    فقط لشريحة (قسم) واحدة من صناديق البريد النشطة بهذه التكة (B6، راجع
    تعليق رأس الملف) — يتخطّى أيضًا صناديق بتراجع فعّال (next_check_at
    بالمستقبل بعد أخطاء متتالية).

    `customer_ids` صريح (اختبار يدوي/`POST /inbox/run-now?customer_id=`)
    **يتجاوز التقسيم كليًا** (يعالج تلك الصناديق بعينها بصرف النظر عن
    القسم الحالي) — استخدام واضح النية لا يجب أن يتخطّاه التقسيم التلقائي.
    `partition_count`/`partition_index` صريحان (اختبار/تشغيل يدوي مُتحكَّم
    به) يتجاوزان الحساب التلقائي المبني على الوقت."""
    engine = engine or get_engine()
    count = partition_count if partition_count is not None else INBOX_PARTITION_COUNT
    index = partition_index if partition_index is not None else current_partition(count)

    with engine.connect() as conn:
        sql = "SELECT * FROM mail_links WHERE status = 'ok' AND (next_check_at IS NULL OR next_check_at <= now())"
        params: dict = {}
        if customer_ids:
            sql += " AND customer_id = ANY(:ids)"
            params["ids"] = customer_ids
        elif count > 1:
            sql += " AND id % :count = :index"
            params["count"] = count
            params["index"] = index
        rows = conn.execute(text(sql), params).mappings().all()
        mail_links = [dict(r) for r in rows]

    if not mail_links:
        return {"ok": True, "mailboxes": 0, "processed": 0, "bounces": 0, "partition": index, "partition_count": count}

    total_processed = 0
    total_bounces = 0
    errors = 0

    with ThreadPoolExecutor(max_workers=min(INBOX_ROUND_WORKERS, len(mail_links))) as pool:
        futures = {pool.submit(_process_mail_link, engine, ml): ml for ml in mail_links}
        for future in as_completed(futures):
            try:
                result = future.result()
                total_processed += result.get("processed", 0)
                total_bounces += result.get("bounces", 0)
                if result.get("error"):
                    errors += 1
            except Exception:
                logger.exception("خطأ غير متوقع أثناء معالجة صندوق بريد")
                errors += 1

    result = {
        "ok": True,
        "mailboxes": len(mail_links),
        "processed": total_processed,
        "bounces": total_bounces,
        "errors": errors,
        "partition": index,
        "partition_count": count,
    }
    logger.info("جولة وارد انتهت: %s", result)
    return result
