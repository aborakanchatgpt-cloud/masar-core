"""
Masar Core — تحليل الفجوة المعرفية الأسبوعي (نصيحة الجمعة) — B8: إزالة n8n
بالكامل، بديل مباشر لركفلو
n8n/workflows/job-bot-weekly-skill-gap-analysis-friday__VcTFiyUdB7FQDsmw.json
(كل جمعة 2 ظهرًا الرياض: يحلّل سيرة كل عميل نشط عبر Claude، ويرسل له أهم
شهادتين ينقصانه، وأهم 5 مهارات يحتاج يطورها، ونصيحة تطوير مسيرة مهنية).

منقول عن مصدر بيانات n8n القديم (dataTable حقول `cvText`/`fieldOfWork`/
`jobTitles`) إلى الجداول الحقيقية بـPostgres (migrations/versions/0004_b3_customers.py):
    - cvText      → profiles.cv_text
    - fieldOfWork → customers.families (قائمة نصوص JSONB)
    - jobTitles   → profiles.titles (قائمة {"title": str, "weight": float}
                    JSONB — نفس تنسيق core/app/matching.py:_score_title، راجع
                    _extract_title_strings أدناه لنفس منطق الاستخراج)

يستدعي Anthropic Messages API مباشرة عبر httpx (لا SDK إضافي — httpx موجود
أصلًا بـcore/requirements.txt، ولا داعٍ لتبعية جديدة لطلب HTTP واحد بسيط)
بدل عقدة `@n8n/n8n-nodes-langchain.anthropic` السابقة؛ نفس البرومبت
العربي والنموذج المطلوب تقريبًا (راجع ANTHROPIC_MODEL أدناه، قابل للتغيير
عبر متغير بيئة بلا تعديل كود).

**بوابة أمان**: بلا ANTHROPIC_API_KEY بالبيئة، الجولة كاملة تُتخطّى بصمت
(تسجيل معلوماتي فقط) — لا فشل صاخب يوقف بقية scheduler_main.py، ولا محاولة
تخمين مفتاح؛ هذا تكامل خارجي اختياري صراحة (نفس فلسفة `except ImportError`
بـcore/app/main.py: غياب إعداد اختياري لا يُسقط الخدمة)."""
from __future__ import annotations

import json
import logging
import os
import re

import httpx
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.discovery import get_engine
from app.telegram_notify import send_message, split_message

logger = logging.getLogger("masar.skill_gap")

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_API_VERSION = "2023-06-01"
# قابل للتغيير عبر البيئة بلا تعديل كود (النموذج المحدَّد بركفلو n8n السابق
# "claude-sonnet-4-6" كان اسمًا داخليًا بمنصة n8n وقتها، لا مُعرِّف رسمي
# ثابت يصلح كافتراض دائم هنا — القيمة أدناه أحدث نموذج Sonnet معروف وقت
# كتابة هذا الملف، وSKILL_GAP_MODEL بالبيئة يتجاوزها فورًا لو تغيّر لاحقًا).
ANTHROPIC_MODEL = os.environ.get("SKILL_GAP_MODEL", "claude-sonnet-4-5-20250929")
ANTHROPIC_MAX_TOKENS = 2000
ANTHROPIC_TEMPERATURE = 0.6
ANTHROPIC_TIMEOUT_SECONDS = 60.0

_JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}")

_PROMPT_TEMPLATE = (
    "أنت خبير تطوير مهني وتوظيف في السوق السعودي. عميلنا اسمه {name}، "
    "مجاله/تخصصه: {field}، والمسميات الوظيفية التي يستهدفها: {titles}.\n\n"
    "سيرته الذاتية الحالية:\n{cv_text}\n\n"
    "المطلوب منك تحليل الفجوة بين ما يملكه فعليًا وما يحتاجه ليتفوق في مجاله "
    "ويكون مطلوبًا أكثر في سوق العمل السعودي حاليًا. أرجع النتيجة بصيغة JSON "
    "فقط بدون أي نص إضافي قبله أو بعده وبدون Markdown، بالشكل التالي بالضبط:\n"
    "{{\n"
    '  "certifications": [ {{"name": "اسم الشهادة الاحترافية", "reason": "سبب موجز يوضح '
    "لماذا هذي الشهادة تحديدًا تفيده وترفع طلبه في السوق، ولماذا هي من أهم شهادتين "
    'ينقصانه"}} ... بالضبط عنصرين اثنين فقط، أعلى شهادتين احترافيتين قيمة وطلبًا في '
    "السوق وغير موجودتين حاليًا في سيرته الذاتية ],\n"
    '  "skills": [ {{"name": "اسم المهارة", "reason": "سبب موجز يوضح كيف هذي المهارة '
    'تخدم تخصصه والوظائف اللي يستهدفها ولماذا مطلوبة بكثرة في السوق حاليًا"}} ... '
    "بالضبط 5 عناصر، مهارات غير موجودة حاليًا في سيرته الذاتية ],\n"
    '  "advice": "نصيحة عملية واحدة مركّزة (3-4 جمل) لتطوير سيرته الذاتية ومسيرته '
    'المهنية بشكل عام، مبنية على وضعه الحالي"\n'
    "}}\n"
    "كل المحتوى بالعربية، ومبني فعليًا على تفاصيل سيرته الذاتية ومجاله وليس عامًا جدًا. "
    "لا تقترح شهادة أو مهارة مذكورة أصلًا في سيرته الذاتية."
)

# نفس هامش الأمان تحت حد تيليجرام المستخدَم ببقية الوحدات الجديدة.
MAX_MESSAGE_CHARS = 3900


def _extract_title_strings(titles_json: list) -> list[str]:
    """profiles.titles مخزَّن كـ[{"title": str, "weight": float}, ...] —
    نفس تنسيق core/app/matching.py (راجع `for entry in profile.titles` هناك).
    يرجع فقط النصوص غير الفارغة، بنفس ترتيب التخزين."""
    out = []
    for entry in titles_json or []:
        if isinstance(entry, dict):
            t = entry.get("title")
            if t:
                out.append(t)
    return out


def _fetch_active_customers_with_profile(conn) -> list[dict]:
    rows = conn.execute(
        text(
            """
            SELECT c.id, c.name, c.telegram_chat_id, c.families, p.cv_text, p.titles
            FROM customers c JOIN profiles p ON p.customer_id = c.id
            WHERE c.status = 'active'
            """
        )
    ).mappings().all()
    return [dict(r) for r in rows]


def _call_anthropic(prompt: str, api_key: str) -> str:
    """يرجع أول جزء نصّي (`type: 'text'`) من رد Anthropic Messages API —
    يرفع httpx.HTTPStatusError عند فشل الطلب (مسؤولية المُستدعي عزل خطأ
    عميل واحد، نفس بقية core/app/*.py)."""
    headers = {
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_API_VERSION,
        "content-type": "application/json",
    }
    body = {
        "model": ANTHROPIC_MODEL,
        "max_tokens": ANTHROPIC_MAX_TOKENS,
        "temperature": ANTHROPIC_TEMPERATURE,
        "messages": [{"role": "user", "content": prompt}],
    }
    response = httpx.post(ANTHROPIC_API_URL, headers=headers, json=body, timeout=ANTHROPIC_TIMEOUT_SECONDS)
    response.raise_for_status()
    data = response.json()
    for part in data.get("content") or []:
        if isinstance(part, dict) and part.get("type") == "text":
            return part.get("text") or ""
    return ""


def _parse_skill_gap_response(raw_text: str) -> dict:
    """نفس منطق عقدة "Parse Skill Gap Response" بركفلو n8n السابق حرفيًا:
    استخراج أول كتلة `{...}` من النص وتحليلها JSON — رد فارغ/غير صالح يرجع
    قوائم فارغة (لا استثناء، الرسالة النهائية تتعامل مع قوائم فارغة بأمان)."""
    match = _JSON_BLOCK_RE.search(raw_text or "")
    try:
        parsed = json.loads(match.group(0) if match else raw_text)
    except (json.JSONDecodeError, TypeError):
        parsed = {}
    if not isinstance(parsed, dict):
        parsed = {}
    return {
        "certifications": parsed.get("certifications") if isinstance(parsed.get("certifications"), list) else [],
        "skills": parsed.get("skills") if isinstance(parsed.get("skills"), list) else [],
        "advice": parsed.get("advice") or "",
    }


def _build_skill_gap_message(parsed: dict) -> str:
    """نفس نص عقدة "Build Skill Gap Message" بركفلو n8n السابق حرفيًا."""
    lines = [
        "🌟 نصيحة الجمعة لتطوير مسارك المهني",
        "",
        "قعدنا نراجع سيرتك الذاتية ووضعك في السوق، وهذي أبرز الأشياء اللي نشوف إنها ترفع فرصك:",
    ]

    certifications = parsed.get("certifications") or []
    if certifications:
        lines.append("")
        lines.append("🎓 أهم شهادتين نرشحهما لك:")
        for i, cert in enumerate(certifications, start=1):
            lines.append(f"{i}. {cert.get('name', '')}")
            if cert.get("reason"):
                lines.append(f"💡 {cert['reason']}")

    skills = parsed.get("skills") or []
    if skills:
        lines.append("")
        lines.append("🛠️ أهم 5 مهارات نرشحها لك تطويرها:")
        for i, skill in enumerate(skills, start=1):
            lines.append(f"{i}. {skill.get('name', '')}")
            if skill.get("reason"):
                lines.append(f"💡 {skill['reason']}")

    if parsed.get("advice"):
        lines.append("")
        lines.append("📌 نصيحتنا لك:")
        lines.append(parsed["advice"])

    lines.append("")
    lines.append("استمر بالتطور، والله يوفقك ويرزقك 🤍")
    return "\n".join(lines)


def run_skill_gap_round(engine: Engine | None = None) -> dict:
    """نقطة الدخول الرئيسية — تُستدعى أسبوعيًا كل جمعة من scheduler_main.py
    (2 ظهرًا الرياض، نفس توقيت n8n السابق حرفيًا). idempotent بالمعنى
    الضعيف فقط (لا جدول تتبّع "أُرسلت هذا الأسبوع" — نفس نمط n8n السابق
    الذي كان يعتمد فقط على الجدولة الأسبوعية بلا حارس تكرار صريح؛ خارج
    نطاق B8 إضافة تتبّع idempotent صارم هنا، التشغيل اليدوي المتكرر بنفس
    اليوم نادر وغير خطير — رسالة نصيحة إضافية لا تُفسد شيئًا)."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.info("ANTHROPIC_API_KEY غير معرّف بالبيئة — تخطّي جولة تحليل الفجوة المعرفية الأسبوعية بالكامل")
        return {"ok": True, "skipped": "no_api_key"}

    engine = engine or get_engine()
    with engine.connect() as conn:
        customers = _fetch_active_customers_with_profile(conn)

    sent = 0
    skipped_no_chat = 0
    skipped_no_cv = 0
    errors = 0

    for customer in customers:
        chat_id = customer.get("telegram_chat_id")
        if not chat_id:
            skipped_no_chat += 1
            continue

        cv_text = customer.get("cv_text")
        if not cv_text:
            skipped_no_cv += 1
            continue

        titles = _extract_title_strings(customer.get("titles") or [])
        families = customer.get("families") or []
        prompt = _PROMPT_TEMPLATE.format(
            name=customer.get("name") or "العميل",
            field=", ".join(families) if families else "غير محدد",
            titles=", ".join(titles) if titles else "غير محددة",
            cv_text=cv_text,
        )

        try:
            raw_text = _call_anthropic(prompt, api_key)
            parsed = _parse_skill_gap_response(raw_text)
            message = _build_skill_gap_message(parsed)
            for chunk in split_message(message, MAX_MESSAGE_CHARS):
                send_message(chat_id, chunk)
            sent += 1
        except Exception:  # noqa: BLE001 — عزل خطأ عميل واحد عن بقية الجولة
            logger.exception("فشل تحليل/إرسال الفجوة المعرفية للعميل %s", customer.get("id"))
            errors += 1

    result = {
        "ok": True,
        "customers_active_with_profile": len(customers),
        "sent": sent,
        "skipped_no_chat": skipped_no_chat,
        "skipped_no_cv": skipped_no_cv,
        "errors": errors,
    }
    logger.info("جولة تحليل الفجوة المعرفية الأسبوعية انتهت: %s", result)
    return result
