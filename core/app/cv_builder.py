"""
Masar Core — بناء السيرة الذاتية كمرفق PDF لكل رسالة (B4، الدليل: "مرفق
سيرة ذاتية مناسبة لكل تقديم" + "قوالب متعددة لتفادي نمط واحد ملحوظ").

يختار كل عميل قالبًا ثابتًا (template_id_for_customer، حتمي حسب customer_id
% 4) من 4 قوالب HTML مستقلة (core/app/templates/cv/1.html..4.html)، يملأه
Jinja2 ببيانات ملفه الشخصي (بلا صور أبدًا — القوالب نصية بحتة، لا مخاطر
حجب صور من فلاتر البريد ولا حاجة لتحميل خطوط خارجية)، ثم يحوّله لـPDF عبر
Gotenberg (خدمة تحويل HTML→PDF بالبنية التحتية الحالية — Docker/Hetzner).

نسخة واحدة لكل (عميل، عائلة مهنية) — لا نعيد التوليد لكل رسالة إرسال؛
`ensure_cv_variant()` تُنشئها أول مرة فقط وتُعيد استخدامها (cv_variants،
مفتاح فريد customer_id+family) طالما الملف الفعلي (html_path/pdf_path) لا
يزال موجودًا على القرص (`cv_data` — محرك Docker volume مشترك بين core
وcore-scheduler).

sanitize_cv_excerpt(): تحمي خصوصية أطراف أخرى قد تُذكر بنص السيرة الخام
(مدير سابق، مرجع) — تقصّ القسم بعد أي عنوان "المراجع/References" صراحة،
وتُبقي أول ظهور فقط لبريد/هاتف (بيانات العميل نفسه برأس سيرته الأصلية) وتحذف
أي ظهور لاحق (احتمال بيانات شخص آخر مذكور بلا عنوان قسم صريح، دفاع إضافي).
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import httpx
from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATE_COUNT = 4

_TEMPLATES_DIR = Path(__file__).parent / "templates" / "cv"
_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=select_autoescape(["html"]),
)

_REFERENCES_HEADING_RE = re.compile(
    r"^\s*(المراجع|References)\s*:?\s*$", re.IGNORECASE | re.MULTILINE
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(?:\+?\d[\d\s\-]{7,}\d)")


def _to_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        import json

        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except (ValueError, TypeError):
            return []
    return []


def template_id_for_customer(customer_id: int) -> int:
    """قالب ثابت حتمي لكل عميل — (customer_id % 4) + 1، يضمن توزيعًا
    متساويًا تقريبيًا عبر القوالب الأربعة ونفس القالب دومًا لنفس العميل."""
    return (customer_id % TEMPLATE_COUNT) + 1


def sanitize_cv_excerpt(cv_text: str | None, *, max_chars: int = 4000) -> str:
    """يقصّ نص السيرة عند أول عنوان "المراجع/References" صراحة (والقسم الذي
    يليه)، ثم يُبقي أول ظهور فقط لأي بريد/هاتف بالنص المتبقي (بيانات العميل
    نفسه غالبًا برأس سيرته) ويحذف أي ظهور لاحق (دفاع إضافي ضد بيانات طرف
    آخر مذكورة بلا عنوان قسم صريح)، ثم يقصّ الطول الإجمالي."""
    if not cv_text:
        return ""

    match = _REFERENCES_HEADING_RE.search(cv_text)
    text = cv_text[: match.start()].rstrip() if match else cv_text

    email_seen = False

    def _email_sub(m: re.Match) -> str:
        nonlocal email_seen
        if not email_seen:
            email_seen = True
            return m.group(0)
        return ""

    phone_seen = False

    def _phone_sub(m: re.Match) -> str:
        nonlocal phone_seen
        if not phone_seen:
            phone_seen = True
            return m.group(0)
        return ""

    text = _EMAIL_RE.sub(_email_sub, text)
    text = _PHONE_RE.sub(_phone_sub, text)

    return text[:max_chars]


def render_cv_html(*, template_id: int, language: str, context: dict) -> str:
    """يملأ قالب HTML رقم template_id (1..4) ببيانات context، بلغة `language`
    ('ar' أو 'en') تحدّد اتجاه النص (rtl/ltr) والنصوص الثابتة داخل القالب."""
    template = _env.get_template(f"{template_id}.html")
    direction = "rtl" if language == "ar" else "ltr"
    render_context = dict(context)
    render_context["dir"] = direction
    render_context["language"] = language
    render_context.setdefault("certs", [])
    render_context.setdefault("skills", [])
    render_context.setdefault("titles", [])
    render_context.setdefault("languages", [])
    return template.render(**render_context)


def convert_html_to_pdf(html: str, *, timeout: float = 30.0) -> bytes:
    """يحوّل HTML إلى PDF عبر Gotenberg (`/forms/chromium/convert/html`،
    multipart/form-data مع ملف index.html). GOTENBERG_URL من متغيّرات البيئة
    (نفس الخدمة المستخدمة أصلًا ببقية النظام — docker-compose.yml)."""
    base_url = os.environ.get("GOTENBERG_URL", "http://gotenberg:3000")
    url = f"{base_url.rstrip('/')}/forms/chromium/convert/html"
    files = {"index.html": ("index.html", html.encode("utf-8"), "text/html")}
    with httpx.Client(timeout=timeout) as client:
        response = client.post(url, files=files)
        response.raise_for_status()
        return response.content


def build_profile_context(customer_row: dict, profile_row: dict, family: str) -> dict:
    """يبني context جاهزًا لـrender_cv_html من صفوف customers+profiles SQL خام."""
    cv_excerpt = sanitize_cv_excerpt(profile_row.get("cv_text"))
    years_exp = profile_row.get("years_exp")
    titles = [
        t.get("title") if isinstance(t, dict) else str(t)
        for t in _to_list(profile_row.get("titles"))
    ]
    return {
        "name": customer_row.get("name") or "",
        "phone": customer_row.get("phone") or "",
        "city": (_to_list(customer_row.get("cities")) or [""])[0],
        "years_exp": float(years_exp) if years_exp is not None else None,
        "seniority": profile_row.get("seniority") or "",
        "degree": profile_row.get("degree") or "",
        "certs": _to_list(profile_row.get("certs")),
        "skills": _to_list(profile_row.get("skills")),
        "titles": [t for t in titles if t],
        "languages": _to_list(profile_row.get("languages")),
        "family": family,
        "cv_excerpt": cv_excerpt,
    }


def ensure_cv_variant(conn, customer_row: dict, profile_row: dict, family: str) -> dict:
    """يرجع صفّ cv_variants الحالي لهذا (العميل، العائلة) — يبنيه أول مرة
    فقط (أو يعيد بناءه إن اختفى الملف الفعلي من القرص رغم وجود صفّ بالقاعدة،
    مثال: volume أُعيد تهيئته يدويًا) عبر render_cv_html + convert_html_to_pdf،
    ثم upsert بجدول cv_variants (ON CONFLICT على customer_id+family)."""
    from sqlalchemy import text as sql_text

    customer_id = customer_row["id"]
    existing = conn.execute(
        sql_text(
            "SELECT id, template, html_path, pdf_path FROM cv_variants WHERE customer_id = :cid AND family = :family"
        ),
        {"cid": customer_id, "family": family},
    ).mappings().first()

    if existing and existing["pdf_path"] and Path(existing["pdf_path"]).is_file():
        return dict(existing)

    template_id = existing["template"] if existing else template_id_for_customer(customer_id)
    context = build_profile_context(customer_row, profile_row, family)
    language = "ar" if any(ord(c) in range(0x0600, 0x0700) for c in (context.get("name") or "")) else "en"
    html = render_cv_html(template_id=template_id, language=language, context=context)

    cv_data_dir = Path(os.environ.get("CV_DATA_DIR", "/data/cv")) / str(customer_id)
    cv_data_dir.mkdir(parents=True, exist_ok=True)
    html_path = cv_data_dir / f"{family}.html"
    pdf_path = cv_data_dir / f"{family}.pdf"
    html_path.write_text(html, encoding="utf-8")

    pdf_bytes = convert_html_to_pdf(html)
    pdf_path.write_bytes(pdf_bytes)

    conn.execute(
        sql_text(
            """
            INSERT INTO cv_variants (customer_id, family, template, html_path, pdf_path, updated_at)
            VALUES (:cid, :family, :template, :html_path, :pdf_path, now())
            ON CONFLICT (customer_id, family) DO UPDATE SET
                template = EXCLUDED.template,
                html_path = EXCLUDED.html_path,
                pdf_path = EXCLUDED.pdf_path,
                updated_at = now()
            """
        ),
        {
            "cid": customer_id,
            "family": family,
            "template": template_id,
            "html_path": str(html_path),
            "pdf_path": str(pdf_path),
        },
    )

    return {
        "template": template_id,
        "html_path": str(html_path),
        "pdf_path": str(pdf_path),
    }
