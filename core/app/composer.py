"""
Masar Core — تركيب رسائل التقديم (B4، الدليل: "نبرة بشرية، عربي أو إنجليزي
حسب لغة الإعلان، لا كشف أتمتة أبدًا").

يبني رسالة تقديم كاملة (موضوع + نص) لكل زوج (عميل، وظيفة) من بنك عبارات
(data/phrases_ar.yaml، data/phrases_en.yaml) — **حتمي** (deterministic):
نفس (customer_id, job_key) يُنتج دومًا نفس اختيار العبارات (بذرة sha256 لا
عشوائية حقيقية، لا `random.seed()` من الوقت ولا PYTHONHASHSEED) حتى:
    1. تبقى الرسالة قابلة لإعادة الإنتاج بدقّة لأغراض المراجعة/الاختبار.
    2. لا تتكرر نفس التركيبة بالضبط بين عملاء/وظائف مختلفة (بذرة مختلفة
       لكل زوج) بما يمنع نمطًا واضحًا يلفت انتباه فلاتر البريد.

لا PII لأي شخص آخر أبدًا في الجسم — فقط بيانات العميل نفسه (الاسم/الهاتف/
المدينة الموقّعة صراحة بأسفل الرسالة) وعنوان الوظيفة/مهارات مذكورة بالإعلان
نفسه (علنية أصلًا).
"""
from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

ARABIC_RATIO_THRESHOLD = 0.30

_ARABIC_RANGE = range(0x0600, 0x0700)

_FALLBACK_SKILLS = {
    "ar": ("مجال التخصص", "التنفيذ العملي"),
    "en": ("the role's core requirements", "hands-on delivery"),
}


def _data_dir() -> Path:
    import os

    return Path(os.environ.get("DATA_DIR", "/app/data"))


def detect_language(*texts: str | None) -> str:
    """يكتشف لغة الإعلان: 'ar' إن كانت نسبة الأحرف العربية بين كل النصوص
    المُمَرّرة ≥ARABIC_RATIO_THRESHOLD، وإلا 'en' (الافتراضي الآمن لنص غير
    عربي غالبًا، أو فارغ تمامًا)."""
    combined = "".join(t for t in texts if t)
    if not combined:
        return "en"
    letters = [c for c in combined if c.isalpha()]
    if not letters:
        return "en"
    arabic_count = sum(1 for c in letters if ord(c) in _ARABIC_RANGE)
    ratio = arabic_count / len(letters)
    return "ar" if ratio >= ARABIC_RATIO_THRESHOLD else "en"


@lru_cache(maxsize=4)
def _load_phrases(language: str) -> dict:
    filename = f"phrases_{language}.yaml"
    path = _data_dir() / filename
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data


def _seed_for(customer_id: int, job_key: str) -> int:
    """بذرة حتمية (sha256، لا hash() المدمجة — راجع توثيق pacing.py لنفس
    السبب: PYTHONHASHSEED عشوائي بين تشغيلات العملية)."""
    digest = hashlib.sha256(f"{customer_id}:{job_key}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def _rng_for(customer_id: int, job_key: str) -> random.Random:
    return random.Random(_seed_for(customer_id, job_key))


def _pick_two_skills(
    profile_skills: list[str], job_skills: list[str], rng: random.Random, language: str
) -> tuple[str, str]:
    """يختار مهارتين لعبارة "لماذا أنا": يفضّل تقاطع مهارات العميل مع
    مهارات الإعلان، ثم مهارات العميل وحدها، ثم مهارات الإعلان وحدها، وإلا
    عبارتان عامتان محايدتان (باللغة الصحيحة — لا نص إنجليزي مقحَم برسالة
    عربية أو العكس)."""
    overlap = [s for s in profile_skills if s in set(job_skills)]
    pool = overlap or list(profile_skills) or list(job_skills)
    pool = [s for s in pool if s]
    if len(pool) >= 2:
        chosen = rng.sample(pool, 2)
        return chosen[0], chosen[1]
    if len(pool) == 1:
        fallback = _FALLBACK_SKILLS.get(language, _FALLBACK_SKILLS["en"])
        return pool[0], fallback[1]
    fallback = _FALLBACK_SKILLS.get(language, _FALLBACK_SKILLS["en"])
    return fallback[0], fallback[1]


def _years_phrase(years_exp: float | None, language: str) -> str:
    """يُنسّق سنوات الخبرة كنص يُدرج بعبارة "لماذا أنا" — رقم صحيح إن أمكن،
    وإلا رقم عشري بمنزلة واحدة؛ وعبارة "عدة/several" المحايدة إن كانت
    سنوات الخبرة غير معروفة أصلًا (None)."""
    if years_exp is None:
        return "عدة" if language == "ar" else "several"
    if float(years_exp).is_integer():
        return str(int(years_exp))
    return f"{years_exp:.1f}"


@dataclass
class ComposedEmail:
    language: str
    subject: str
    body_text: str


def build_email(
    *,
    customer_id: int,
    job_key: str,
    customer_name: str,
    customer_phone: str | None,
    customer_city: str | None,
    years_exp: float | None,
    profile_skills: list[str],
    job_title: str,
    job_skills: list[str],
    job_text_for_language: str,
    email_class: str = "posted",
) -> ComposedEmail:
    """نقطة الدخول الرئيسية — يبني رسالة كاملة (موضوع + نص) بلغة الإعلان
    (يُكتشف من job_text_for_language عبر detect_language)، حتمية لكل زوج
    (customer_id, job_key).

    B12.4: `email_class="speculative"` (التقديم المبادر — عمل جديد يُقدّم
    لشركة بمجال العميل بلا وظيفة معلنة فعليًا) يختار بنك عبارات فرعي مختلف
    (`openings_speculative`/`why_me_speculative`/`subject_templates_speculative`
    بـ phrases_*.yaml) لا يذكر مطلقًا "الوظيفة المعلنة" أو مسمّى وظيفي
    مُختلَق — بدلًا من ذلك يذكر مجال الشركة (`job_title` هنا يُمرّر كاسم
    العائلة المهنية بالعربية/الإنجليزية لا كمسمى وظيفي، راجع
    core/app/planner.py:_speculative_job_title). يتراجع بصمت لعبارات
    `email_class="posted"` العادية إن كانت مفاتيح `*_speculative` غير
    موجودة بملف اللغة (توافق خلفي — لا كسر لو نُشر composer.py قبل تحديث
    ملفات phrases_*.yaml بنفس الدفعة)."""
    language = detect_language(job_text_for_language, job_title)
    phrases = _load_phrases(language)
    rng = _rng_for(customer_id, job_key)

    is_speculative = email_class == "speculative"
    openings_pool = phrases.get("openings_speculative") if is_speculative else None
    opening = rng.choice(openings_pool or phrases["openings"])

    skill_a, skill_b = _pick_two_skills(profile_skills, job_skills, rng, language)
    years_text = _years_phrase(years_exp, language)

    why_me_pool = phrases.get("why_me_speculative") if is_speculative else None
    why_me_template = rng.choice(why_me_pool or phrases["why_me"])
    why_me = why_me_template.format(title=job_title, years=years_text, skill_a=skill_a, skill_b=skill_b)

    attachment_line = rng.choice(phrases["attachment_lines"])
    closing = rng.choice(phrases["closings"])
    signature = phrases["signature_template"].format(
        name=customer_name, phone=customer_phone or "", city=customer_city or ""
    )

    if email_class == "generic":
        subject = phrases.get("subject_generic_prefix", "") or f"{job_title}"
    elif is_speculative:
        subject_pool = phrases.get("subject_templates_speculative") or phrases["subject_templates"]
        subject = rng.choice(subject_pool).format(title=job_title)
    else:
        subject_template = rng.choice(phrases["subject_templates"])
        subject = subject_template.format(title=job_title)

    body_parts = [opening, "", why_me, "", attachment_line, "", closing, "", signature]
    body_text = "\n".join(body_parts)

    return ComposedEmail(language=language, subject=subject, body_text=body_text)
