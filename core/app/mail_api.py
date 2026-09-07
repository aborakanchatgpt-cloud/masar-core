"""
Masar Core — نقاط نهاية البريد (B4): ربط صندوق بريد العميل، إدارته، ونقاط
إدارية/تحقق للإرسال (إحصاءات، تشغيل فوري، رسائل sink، اختبار تحميل).

كل النقاط محمية بتوكن الإدارة (نفس نمط bقية core/app/*_api.py). **لا تُرجع
ولا تُسجّل أي سرّ إطلاقاً** — لا كلمة مرور تطبيق Gmail بأي استجابة JSON ولا
بأي سطر سجلّ (logger)، لا حتى بحالة فشل الاختبار (رسالة الخطأ من مكتبات
smtplib/imaplib تُقصّ من أي محتوى يشبه بيانات اعتماد قبل إرجاعها — الأبسط
here: لا تُمرّر رسالة الاستثناء الخام أصلاً، بل رسالة عامة + نوع الخطأ فقط).
"""
from __future__ import annotations

import imaplib
import logging
import os
import smtplib
import uuid
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import mail_crypto, send_builder, sender
from app.auth import require_admin_token
from app.discovery import get_engine
from sqlalchemy import text

logger = logging.getLogger("masar.mail_api")

# main.py يستورد `router` وحده (app.include_router(module.router)) — لذا
# نجمع هنا راوترين بادئتين مختلفتين (/mail-link و/admin/mail) داخل راوتر
واحد بلا بادئة إضافية خاصة به، عبر include_router بلا prefix زائد.
router = APIRouter(dependencies=[Depends(require_admin_token)])
mail_link_router = APIRouter(prefix="/mail-link", tags=["mail"])
admin_router = APIRouter(prefix="/admin/mail", tags=["mail-admin"])

SMTP_TEST_TIMEOUT = 10.0
IMAP_TEST_TIMEOUT = 10.0


def _test_smtp(host: str, port: int, address: str, password: str) -> str | None:
    """يحاول تسجيل دخول SMTP فعلياً (STARTTLS). يرجع None عند النجاح، أو
    رسالة خطأ عامة (بلا تفاصيل قد تحمل أثراً لكلمة المرور) عند الفشل."""
    try:
        with smtplib.SMTP(host, port, timeout=SMTP_TEST_TIMEOUT) as smtp:
            smtp.starttls()
            smtp.login(address, password)
        return None
    except smtplib.SMTPAuthenticationError:
        return "فشل تسجيل الدخول SMTP — تحقق من العنوان/كلمة مرور التطبيق"
    except Exception as exc:  # noqa: BLE001
        return f"تعذّر الاتصال بـSMTP ({type(exc).__name__})"


def _test_imap(host: str, port: int, address: str, password: str) -> str | None:
    try:
        with imaplib.IMAP4_SSL(host, port, timeout=IMAP_TEST_TIMEOUT) as imap:
            imap.login(address, password)
            imap.select("INBOX", readonly=True)
        return None
    except imaplib.IMAP4.error:
        return "فشل تسجيل الدخول IMAP — تحقق من العنوان/كلمة مرور التطبيق"
    except Exception as exc:  # noqa: BLE001
        return f"تعذّر الاتصال بـIMAP ({type(exc).__name__})"


class MailLinkCreateRequest(BaseModel):
    customer_id: int
    address: str
    app_password: str
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    imap_host: str = "imap.gmail.com"
    imap_port: int = 993


@mail_link_router.post("")
async def create_mail_link(body: MailLinkCreateRequest) -> dict:
    """يختبر SMTP+IMAP فعلياً بكلمة المرور المُرسَلة قبل التخزين — لا يُخزّن
    صندوق فشل الاختبار كـ'ok' أبداً (يُخزّن كـ'failed' مع last_error عامًا،
    ما يسمح للمستخدم بإعادة المحاولة عبر نفس النقطة بلا تكرار صفوف)."""
    engine = get_engine()
    with engine.connect() as conn:
        customer = conn.execute(
            text("SELECT id FROM customers WHERE id = :id"), {"id": body.customer_id}
        ).first()
    if not customer:
        raise HTTPException(status_code=404, detail="عميل غير موجود")

    smtp_error = _test_smtp(body.smtp_host, body.smtp_port, body.address, body.app_password)
    imap_error = _test_imap(body.imap_host, body.imap_port, body.address, body.app_password) if not smtp_error else None
    error = smtp_error or imap_error
    status = "ok" if not error else "failed"

    secret_enc = mail_crypto.encrypt_secret(body.app_password)

    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                INSERT INTO mail_links (
                    customer_id, address, smtp_host, smtp_port, imap_host, imap_port,
                    secret_enc, status, last_check_at, last_error, created_at
                ) VALUES (
                    :customer_id, :address, :smtp_host, :smtp_port, :imap_host, :imap_port,
                    :secret_enc, :status, now(), :error, now()
                )
                ON CONFLICT (customer_id) DO UPDATE SET
                    address = EXCLUDED.address,
                    smtp_host = EXCLUDED.smtp_host,
                    smtp_port = EXCLUDED.smtp_port,
                    imap_host = EXCLUDED.imap_host,
                    imap_port = EXCLUDED.imap_port,
                    secret_enc = EXCLUDED.secret_enc,
                    status = EXCLUDED.status,
                    last_check_at = now(),
                    last_error = EXCLUDED.last_error
                RETURNING id
                """
            ),
            {
                "customer_id": body.customer_id,
                "address": body.address,
                "smtp_host": body.smtp_host,
                "smtp_port": body.smtp_port,
                "imap_host": body.imap_host,
                "imap_port": body.imap_port,
                "secret_enc": secret_enc,
                "status": status,
                "error": error,
            },
        ).first()

    return {"ok": error is None, "mail_link_id": row[0], "status": status, "error": error}


@mail_link_router.delete("/{customer_id}")
async def delete_mail_link(customer_id: int) -> dict:
    engine = get_engine()
    with engine.begin() as conn:
        result = conn.execute(text("DELETE FROM mail_links WHERE customer_id = :id"), {"id": customer_id})
    return {"ok": True, "deleted": result.rowcount}


@mail_link_router.get("/{customer_id}")
async def get_mail_link(customer_id: int) -> dict:
    """يُرجع حالة الربط فقط — لا secret_enc إطلاقاً بأي استجابة."""
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT id, customer_id, address, smtp_host, smtp_port, imap_host, imap_port,
                       status, last_check_at, last_error, warmup_day, created_at
                FROM mail_links WHERE customer_id = :id
                """
            ),
            {"id": customer_id},
        ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="لا يوجد ربط بريد لهذا العميل")
    return dict(row)


# ---------------------------------------------------------------------------
# admin/mail/* — إحصاءات وتشغيل يدوي/تحقق (deliverable 7)
# ---------------------------------------------------------------------------


@admin_router.get("/stats")
async def mail_stats() -> dict:
    engine = get_engine()
    with engine.connect() as conn:
        by_status = conn.execute(
            text("SELECT status, count(*) FROM send_queue WHERE synthetic = false GROUP BY status")
        ).all()
        mail_links_by_status = conn.execute(
            text("SELECT status, count(*) FROM mail_links GROUP BY status")
        ).all()
        inbox_by_kind = conn.execute(
            text("SELECT kind, count(*) FROM inbox_events GROUP BY kind")
        ).all()
        queued_next = conn.execute(
            text(
                "SELECT min(send_after) FROM send_queue WHERE status = 'queued' AND synthetic = false"
            )
        ).scalar()
    return {
        "send_queue_by_status": {r[0]: r[1] for r in by_status},
        "mail_links_by_status": {r[0]: r[1] for r in mail_links_by_status},
        "inbox_events_by_kind": {r[0]: r[1] for r in inbox_by_kind},
        "next_queued_send_after": queued_next.isoformat() if queued_next else None,
    }


@admin_router.post("/queue-now")
async def queue_now(customer_id: int | None = None) -> dict:
    customer_ids = [customer_id] if customer_id is not None else None
    result = send_builder.build_queue_round(customer_ids=customer_ids)
    return result


@admin_router.post("/send-now")
async def send_now(limit: int = 200) -> dict:
    """يرسل فورًا متجاوزًا فحص نافذة الإرسال — للتحقق اليدوي/اختبار sink
    فقط (راجع توثيق sender.send_tick لضمانات ignore_window)."""
    result = sender.send_tick(limit=limit, ignore_window=True)
    return result


@admin_router.get("/sink/messages")
async def sink_messages(limit: int = 50) -> dict:
    """يعرض رسائل صندوق mailpit (sink التطوير) عبر واجهة mailpit HTTP API —
    يتحقق بصريًا من وصول الرسائل (موضوع/CC/مرفق PDF) بلا أي إرسال حقيقي."""
    sink_api_url = os.environ.get("MAIL_SINK_API_URL", "").strip()
    if not sink_api_url:
        sink_smtp = os.environ.get("MAIL_SINK_SMTP", "").strip()
        if not sink_smtp:
            raise HTTPException(status_code=503, detail="MAIL_SINK_SMTP/MAIL_SINK_API_URL غير معرّفين — sink غير مفعّل")
        host = sink_smtp.split(":", 1)[0]
        sink_api_url = f"http://{host}:8025"

    url = f"{sink_api_url.rstrip('/')}/api/v1/messages"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, params={"limit": limit})
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"تعذّر الاتصال بواجهة mailpit: {exc}") from exc


class LoadTestRequest(BaseModel):
    customers: int = 1500
    per_customer: int = 17


@admin_router.post("/load-test")
async def load_test(body: LoadTestRequest) -> dict:
    """يبني صفوف send_queue اصطناعية (synthetic=true) لقياس معدّل التصريف
    (drain rate) دون المرور بأي منطق تخطيط/مطابقة حقيقي ودون أي أثر جانبي
    حقيقي (applications/company_cooldowns/ledger مُستبعدة صراحةً لصفوف
    synthetic بمنطق sender._mark_success). عملاء الاختبار حقيقيون بقاعدة
    البيانات (قيد FK يتطلب ذلك) لكن بحالة 'paused' (مُستبعدون تلقائيًا من
    أي معالجة حقيقية بـplanner.py/send_builder.py التي تفلتر status='active')."""
    batch_tag = uuid.uuid4().hex[:8]
    engine = get_engine()

    with engine.begin() as conn:
        customer_ids: list[int] = []
        for i in range(body.customers):
            row = conn.execute(
                text(
                    """
                    INSERT INTO customers (name, email_service, status, cities, families, target_daily)
                    VALUES (:name, :email, 'paused', '[]', '[]', :target)
                    ON CONFLICT (email_service) DO UPDATE SET name = EXCLUDED.name
                    RETURNING id
                    """
                ),
                {
                    "name": f"Load Test {batch_tag} #{i}",
                    "email": f"loadtest-{batch_tag}-{i}@masar.invalid",
                    "target": body.per_customer,
                },
            ).first()
            customer_ids.append(row[0])

        # سيرة اصطناعية واحدة يُعاد استخدامها لكل صف (بلا Gotenberg —
        # ملف PDF placeholder بسيط، الهدف قياس معدّل تصريف الطابور لا بناء
        # سير فعلية لكل صف اختبار).
        placeholder_dir = os.environ.get("CV_DATA_DIR", "/data/cv") + "/_loadtest"
        os.makedirs(placeholder_dir, exist_ok=True)
        placeholder_pdf = os.path.join(placeholder_dir, "placeholder.pdf")
        if not os.path.isfile(placeholder_pdf):
            with open(placeholder_pdf, "wb") as f:
                f.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>")

        now = datetime.now(timezone.utc)
        rows_to_insert = []
        for cid in customer_ids:
            for n in range(body.per_customer):
                rows_to_insert.append(
                    {
                        "customer_id": cid,
                        "to_email": "loadtest-recipient@masar.invalid",
                        "cc_email": None,
                        "subject": f"[load-test {batch_tag}] Application #{n}",
                        "body_text": "Load test synthetic message.",
                        "attachments": '[{"path": "%s", "filename": "CV.pdf"}]' % placeholder_pdf,
                        "send_after": now,
                    }
                )

        chunk_size = 2000
        inserted_total = 0
        for start in range(0, len(rows_to_insert), chunk_size):
            chunk = rows_to_insert[start : start + chunk_size]
            conn.execute(
                text(
                    """
                    INSERT INTO send_queue (
                        customer_id, opportunity_id, job_id, to_email, cc_email, subject,
                        body_text, body_html, attachments, send_after, attempts, status, synthetic, created_at
                    ) VALUES (
                        :customer_id, NULL, NULL, :to_email, :cc_email, :subject,
                        :body_text, NULL, CAST(:attachments AS jsonb), :send_after, 0, 'queued', true, now()
                    )
                    """
                ),
                chunk,
            )
            inserted_total += len(chunk)

    return {
        "ok": True,
        "batch_tag": batch_tag,
        "customers_created": len(customer_ids),
        "rows_queued": inserted_total,
    }


router.include_router(mail_link_router)
router.include_router(admin_router)
