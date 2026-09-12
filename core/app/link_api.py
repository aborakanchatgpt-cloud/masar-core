"""
Masar Core — صفحة ربط البريد الذاتي (B5a البند 5، الدليل §7 البند 2: "صندوق
briد الخدمة" — لكن كصفحة ويب مباشرة بدل خطوات تيليجرام مصوّرة، بلا أي سرّ
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

**لا كلمة مرور تُسجّل أبدًا** — لا بسجلّ (logger) ولا بأي استجابة HTML (نفس
قاعدة mail_api.py). الرمز نفسه لا يُخزّن خامًا، فقط sha256(token) — فقدان
قاعدة البيانات لا يكشف أي رمز فعّال.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text

from app import mail_api, sender
from app.auth import require_admin_token
from app.discovery import get_engine
from app.reports import fetch_customer_chat_id
from app.telegram_notify_admin import notify_customer

logger = logging.getLogger("masar.link_api")

router = APIRouter()
admin_router = APIRouter(prefix="/admin/customers", tags=["link"], dependencies=[Depends(require_admin_token)])
public_router = APIRouter(prefix="/link", tags=["link-public"])

TOKEN_TTL_HOURS = 48
MAX_ATTEMPTS = 5

# B9/A5: صفحة شروط الخدمة العامة (بنية فقط بهذه الدفعة — بلا أي ربط
# بتدفّق onboarding الحالي، راجع migrations/versions/0016_terms_accepted.py).
# نفس نمط REPO_DIR بـapp/mcp_bridge.py: جذر المستودع مركّب للقراءة فقط
# داخل حاوية core على /repo (راجع docker-compose.yml)، فdocs/TERMS_AR.md
# مقروء دومًا بلا حاجة لنسخه بـDockerfile أو إعادة بناء الصورة عند تعديله.
TERMS_MD_PATH = Path(os.environ.get("REPO_DIR", "/repo")) / "docs" / "TERMS_AR.md"

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
# B9/A5 — GET /terms: صفحة شروط الخدمة العامة
# ---------------------------------------------------------------------------


def _inline_md(text_: str) -> str:
    """تشديد **نص** + تفريغ أحرف HTML الخاصة (لا يوجد سواه بـTERMS_AR.md)."""
    escaped = text_.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)


def _render_terms_html(markdown_text: str) -> str:
    """محوّل Markdown→HTML يدوي مبسّط (بلا تبعية جديدة لصفحة واحدة
    نادرة التغيّر — core/requirements.txt لا يحوي مكتبة markdown أصلًا). يدعم فقط
    ما يظهر فعليًا بـdocs/TERMS_AR.md: عناوين # و##، فقرات نصية، قوائم
    نقطية (-) ومرقّمة (1.)، وتشديد **نص** داخل أي سطر."""
    html_parts: list[str] = []
    para_buffer: list[str] = []
    list_buffer: list[str] = []
    list_tag: str | None = None

    def flush_para() -> None:
        if para_buffer:
            html_parts.append(f"<p>{' '.join(_inline_md(p) for p in para_buffer)}</p>")
            para_buffer.clear()

    def flush_list() -> None:
        nonlocal list_tag
        if list_buffer:
            tag = list_tag or "ul"
            items = "".join(f"<li>{_inline_md(item)}</li>" for item in list_buffer)
            html_parts.append(f"<{tag}>{items}</{tag}>")
            list_buffer.clear()
            list_tag = None

    for raw_line in markdown_text.splitlines():
        line = raw_line.strip()
        if not line:
            flush_para()
            flush_list()
            continue
        if line.startswith("## "):
            flush_para()
            flush_list()
            html_parts.append(f"<h2>{_inline_md(line[3:])}</h2>")
        elif line.startswith("# "):
            flush_para()
            flush_list()
            html_parts.append(f"<h1>{_inline_md(line[2:])}</h1>")
        elif line.startswith("- "):
            flush_para()
            if list_tag == "ol":
                flush_list()
            list_tag = "ul"
            list_buffer.append(line[2:])
        elif re.match(r"^\d+\.\s", line):
            flush_para()
            if list_tag == "ul":
                flush_list()
            list_tag = "ol"
            list_buffer.append(re.sub(r"^\d+\.\s", "", line))
        else:
            flush_list()
            para_buffer.append(line)

    flush_para()
    flush_list()
    return "".join(html_parts)


def _load_terms_html() -> str:
    try:
        raw = TERMS_MD_PATH.read_text(encoding="utf-8")
    except OSError:
        logger.error("تعذّرت قراءة ملف الشروط %s", TERMS_MD_PATH)
        return "<p>تعذّر تحميل نص الشروط حاليًا. حاول لاحقًا أو تواصل معنا.</p>"
    return _render_terms_html(raw)


@router.get("/terms")
async def show_terms() -> HTMLResponse:
    """GET /terms — عامة، بلا مصادقة، RTL (نفس نمط _page() بـ/link/{token}
    تمامًا). بنية فقط بهذه الدفعة: لا ترابط بأي خطوة onboarding حاليًا."""
    return HTMLResponse(_page("شروط خدمة مسار", _load_terms_html()))


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
بخطوتين، ثم أنشئ "كلمة مرور تطبيق" من إعدادات Google والصقها هنا. لن
نطّلع على بريدك الشخصي أبدًا — فقط هذا الصندوق الجديد.</p>
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
        # attempts قبل أن يزيدها أيّهما (كلاهما يمر فحص الحدّ خطأًا).
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
        # B4/v2 §تحديثات تلقائية: إشعار العميل بفشل الربط عبر بوت العميل
        # (best-effort — لا يمنع/يؤخّر عرض صفحة الويب أعلاه بأي حال).
        _notify_customer_mail_link_result(customer_id, ok=False)
        return _message_page(
            "تعذّر الربط",
            "لم نتمكن من التحقق من بريدك. تأكد من صحة العنوان وكلمة مرور التطبيق (لا كلمة مرور حسابك العادي) وحاول مرة أخرى.",
            error=True,
        )

    with engine.begin() as conn:
        conn.execute(text("UPDATE link_tokens SET used_at = now() WHERE id = :id"), {"id": row["id"]})

    _notify_customer_mail_link_result(customer_id, ok=True)

    return _message_page(
        "تم الربط بنجاح",
        "أحسنت! تم ربط بريدك بخدمة مسار بنجاح، وسنبدأ العمل على البحث عن الفرص المناسبة لك. وفّقك الله 🌿",
    )


def _notify_customer_mail_link_result(customer_id: int, *, ok: bool) -> None:
    """B4/v2: إشعار فوري للعميل عبر بوت العميل بنتيجة ربط بريده — بديل
    انتظاره لتقرير الغد. best-effort بحت: عميل بلا chat_id (لم يربط
    تيليجرام) يُتجاهَل بصمت (نفس منطق reply_set_status بـ
telegram_admin_commands.py)."""
    customer_chat_id = fetch_customer_chat_id(get_engine(), customer_id)
    if customer_chat_id is None:
        return
    if ok:
        notify_customer(customer_chat_id, "تم ربط بريدك بنجاح ✅ سنبدأ العمل على البحث عن الفرص المناسبة لك.")
    else:
        notify_customer(
            customer_chat_id,
            "تعذّر ربط بريدك — تأكد من كلمة مرور التطبيق (App Password) لا كلمة مرور حسابك العادي، وحاول مرة أخرى.",
        )


router.include_router(admin_router)
router.include_router(public_router)
