"""
Masar Core — صفحة ربط البريد الذاتي (B5a البند 5، الدليل §7 البند 2: "صندوق
بريد الخدمة" — لكن كصفحة ويب مباشرة بدل خطوات تيليجرام مصوّرة، بلا أي سرّ
يمر عبر محادثة إدارية).

    POST /admin/customers/{id}/link-token   → رمز مرّة واحدة (48 ساعة)، محمي
                                               بتوكن الإدارة (يستدعيه الأدمن
                                               ليرسل الرابط للعميل عبر تيليجرام).
    GET  /link/{token}                      → صفحة HTML عربية RTL (لا توكن،
                                               عامة — العميل نفسه يفتحها).
    POST /link/{token}                      → يستدعي app.mail_api.create_mail_link
                                               فعليًا (نفس تحقّق SMTP/IMAP
                                               الحقيقي)، محدود بـ5 محاولات
                                               لكل رمز.

**لا كلمة مرور تُسجَّل أبدًا** — لا بسجلّ (logger) ولا بأي استجابة HTML (نفس
قاعدة mail_api.py). الرمز نفسه لا يُخزَّن خامًا، فقط sha256(token) — فقدان
قاعدة البيانات لا يكشف أي رمز فعّال.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text

from app import mail_api, sender
from app.auth import require_admin_token
from app.discovery import get_engine

logger = logging.getLogger("masar.link_api")

router = APIRouter()
admin_router = APIRouter(prefix="/admin/customers", tags=["link"], dependencies=[Depends(require_admin_token)])
public_router = APIRouter(prefix="/link", tags=["link-public"])

TOKEN_TTL_HOURS = 48
MAX_ATTEMPTS = 5

_PAGE_STYLE = """
body{font-family:'Tahoma','Segoe UI',sans-serif;background:#f6f5f2;color:#2b2b28;
  margin:0;padding:0;direction:rtl;}
.wrap{max-width:480px;margin:0 auto;padding:32px 20px;}
.card{background:#ffffff;border-radius:14px;padding:28px 24px;box-shadow:0 2px 10px rgba(0,0,0,0.06);}
h1{font-size:20px;margin:0 0 8px;color:#1f5c4b;}
p.sub{color:#6b6a63;font-size:14px;margin:0 0 22px;line-height:1.6;}
label{display:block;font-size:14px;margin:16px 0 6px;color:#2b2b28;}
input[type=text],input[type=password]{width:100%;box-sizing:border-box;padding:11px 12px;
  border:1px solid #d8d5cc;border-radius:8px;font-size:15px;background:#faf9f6;}
.checkbox-row{display:flex;align-items:flex-start;gap:8px;margin:18px 0;font-size:13px;
  color:#4a4a45;line-height:1.6;}
.checkbox-row input{margin-top:3px;}
button{width:100%;padding:12px;margin-top:20px;border:none;border-radius:8px;
  background:#1f5c4b;color:#fff;font-size:16px;cursor:pointer;}
button:hover{background:#184a3c;}
.msg{padding:16px;border-radius:10px;background:#f0f7f3;color:#1f5c4b;font-size:15px;
  line-height:1.7;text-align:center;}
.msg.err{background:#fbf0ee;color:#8a3a2c;}
.foot{margin-top:22px;font-size:12px;color:#9a988f;text-align:center;}
"""


def _page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="ar" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>{title}</title>
<style>{_PAGE_STYLE}</style>
</head><body><div class="wrap"><div class="card">{body}</div>
<div class="foot">مسار — نرافقك في رحلتك</div></div></body></html>"""


def _message_page(title: str, message: str, *, error: bool = False) -> HTMLResponse:
    cls = "msg err" if error else "msg"
    body = f"<h1>{title}</h1><div class='{cls}'>{message}</div>"
    return HTMLResponse(_page(title, body))


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# admin — إنشاء رمز
# ---------------------------------------------------------------------------


@admin_router.post("/{customer_id}/link-token")
async def create_link_token(customer_id: int) -> dict:
    engine = get_engine()
    with engine.begin() as conn:
        customer = conn.execute(text("SELECT id FROM customers WHERE id = :id"), {"id": customer_id}).first()
        if not customer:
            raise HTTPException(status_code=404, detail="عميل غير موجود")

        raw_token = secrets.token_urlsafe(32)
        token_hash = _hash_token(raw_token)
        expires_at = datetime.now(timezone.utc) + timedelta(hours=TOKEN_TTL_HOURS)

        row = conn.execute(
            text(
                """
                INSERT INTO link_tokens (customer_id, token_hash, expires_at, attempts, created_at)
                VALUES (:cid, :hash, :expires, 0, now())
                RETURNING id
                """
            ),
            {"cid": customer_id, "hash": token_hash, "expires": expires_at},
        ).first()

    return {
        "ok": True,
        "link_token_id": row[0],
        "path": f"/link/{raw_token}",
        "expires_at": expires_at.isoformat(),
    }


# ---------------------------------------------------------------------------
# عامة — صفحة الإدخال + الإرسال
# ---------------------------------------------------------------------------


def _fetch_token_row(conn, token: str, *, lock: bool = False) -> dict | None:
    sql = "SELECT * FROM link_tokens WHERE token_hash = :h"
    if lock:
        sql += " FOR UPDATE"
    row = conn.execute(text(sql), {"h": _hash_token(token)}).mappings().first()
    return dict(row) if row else None


_FORM_BODY = """
<h1>ربط بريدك الخاص بخدمة مسار</h1>
<p class="sub">أنشئ بريد Gmail جديدًا باسمك خصّيصًا لهذه الخدمة، فعّل التحقق
بخطوتين، ثم أنشئ "كلمة مرور تطبيق" من إعدادات Google والصقها هنا. لن نطّلع
على بريدك الشخصي أبدًا — فقط هذا الصندوق الجديد.</p>
<form method="post" action="">
  <label for="gmail_address">عنوان البريد الإلكتروني</label>
  <input type="text" id="gmail_address" name="gmail_address" placeholder="name.career@gmail.com" required>
  <label for="app_password">كلمة مرور التطبيق (16 حرفًا)</label>
  <input type="text" id="app_password" name="app_password" placeholder="xxxx xxxx xxxx xxxx" required>
  <div class="checkbox-row">
    <input type="checkbox" id="consent" name="consent">
    <label for="consent" style="margin:0;display:inline;">أوافق أن يُستخدم هذا البريد للتقديم على الفرص نيابةً عني وقراءة الردود الواردة إليه.</label>
  </div>
  <button type="submit">ربط البريد</button>
</form>
"""


@public_router.get("/{token}")
async def show_link_form(token: str) -> HTMLResponse:
    engine = get_engine()
    with engine.connect() as conn:
        row = _fetch_token_row(conn, token)

    if row is None:
        return _message_page("رابط غير صالح", "هذا الرابط غير صحيح. تواصل معنا للحصول على رابط جديد.", error=True)
    if row["used_at"] is not None:
        return _message_page("تم الاستخدام", "تم استخدام هذا الرابط من قبل. تواصل معنا إن احتجت رابطًا جديدًا.", error=True)
    if row["expires_at"] < datetime.now(timezone.utc):
        return _message_page("انتهت الصلاحية", "انتهت صلاحية هذا الرابط. تواصل معنا للحصول على رابط جديد.", error=True)
    if row["attempts"] >= MAX_ATTEMPTS:
        return _message_page("توقّفنا مؤقتًا", "تجاوزنا عدد المحاولات المسموح على هذا الرابط. تواصل معنا للمتابعة.", error=True)

    return HTMLResponse(_page("ربط بريدك بخدمة مسار", _FORM_BODY))


@public_router.post("/{token}")
async def submit_link_form(token: str, request: Request, skip_verify: int = Query(default=0)) -> HTMLResponse:
    engine = get_engine()

    with engine.begin() as conn:
        # FOR UPDATE: يمنع سباقًا بين طلبين متزامنين لنفس الرمز يقرآن نفس
        # attempts قبل أن يزيدها أيّهما (كلاهما يمرّ فحص الحدّ خطأًا).
        row = _fetch_token_row(conn, token, lock=True)
        if row is None:
            return _message_page("رابط غير صالح", "هذا الرابط غير صحيح. تواصل معنا للحصول على رابط جديد.", error=True)
        if row["used_at"] is not None:
            return _message_page("تم الاستخدام", "تم استخدام هذا الرابط من قبل. تواصل معنا إن احتجت رابطًا جديدًا.", error=True)
        if row["expires_at"] < datetime.now(timezone.utc):
            return _message_page("انتهت الصلاحية", "انتهت صلاحية هذا الرابط. تواصل معنا للحصول على رابط جديد.", error=True)
        if row["attempts"] >= MAX_ATTEMPTS:
            return _message_page("توقّفنا مؤقتًا", "تجاوزنا عدد المحاولات المسموح على هذا الرابط. تواصل معنا للمتابعة.", error=True)

        # الحدّ من المحاولات (Rate-limit by token) — يُزاد بصرف النظر عن
        # نتيجة هذه المحاولة (نجاح/فشل تحقّق/بيانات ناقصة).
        conn.execute(text("UPDATE link_tokens SET attempts = attempts + 1 WHERE id = :id"), {"id": row["id"]})
        customer_id = row["customer_id"]

    form = await request.form()
    address = str(form.get("gmail_address") or "").strip()
    app_password_raw = str(form.get("app_password") or "")
    consent = form.get("consent") in ("on", "true", "1")
    # لا نُسجّل app_password_raw بأي سجلّ/رسالة أبدًا من هنا فصاعدًا.

    if not consent:
        return _message_page("الموافقة مطلوبة", "يرجى الموافقة على استخدام البريد للمتابعة.", error=True)

    password_clean = app_password_raw.replace(" ", "").replace("‏", "")
    if len(password_clean) != 16 or not password_clean.isalnum():
        return _message_page(
            "تحقق من كلمة المرور", "كلمة مرور التطبيق يجب أن تكون 16 حرفًا (المسافات بينها لا بأس بها).", error=True
        )
    if "@" not in address or "." not in address.split("@")[-1]:
        return _message_page("تحقق من البريد", "يرجى إدخال عنوان بريد إلكتروني صحيح.", error=True)

    effective_skip_verify = bool(skip_verify) and sender.is_dry_run()

    try:
        result = await mail_api.create_mail_link(
            mail_api.MailLinkCreateRequest(
                customer_id=customer_id,
                address=address,
                app_password=password_clean,
                skip_verify=effective_skip_verify,
            )
        )
    except HTTPException as exc:
        return _message_page("تعذّر الربط", str(exc.detail), error=True)
    except Exception:  # noqa: BLE001 — لا نُسرّب أي تفصيل داخلي للصفحة العامة
        logger.exception("خطأ غير متوقع أثناء ربط بريد العميل %s عبر صفحة الرابط", customer_id)
        return _message_page("تعذّر الربط", "حدث خطأ غير متوقع، حاول مرة أخرى بعد قليل.", error=True)

    if not result.get("ok"):
        return _message_page(
            "تعذّر الربط",
            "لم نتمكن من التحقق من بريدك. تأكد من صحة العنوان وكلمة مرور التطبيق (لا كلمة مرور حسابك العادية) وحاول مرة أخرى.",
            error=True,
        )

    with engine.begin() as conn:
        conn.execute(text("UPDATE link_tokens SET used_at = now() WHERE id = :id"), {"id": row["id"]})

    return _message_page(
        "تم الربط بنجاح",
        "أحسنت! تم ربط بريدك بخدمة مسار بنجاح، وسنبدأ العمل على البحث عن الفرص المناسبة لك. وفّقك الله 🌿",
    )


router.include_router(admin_router)
router.include_router(public_router)
