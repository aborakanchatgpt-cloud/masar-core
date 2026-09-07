"""
تطبيع أسماء الشركات/المسميات الوظيفية ومفتاح كشف التكرار — بنفس منطق
كشف التكرار المعتمد في المرحلة 0 (تطبيع الاسم، وليس مقارنة الروابط الخام).

مراجعة B2 (docs/reports/B2-review.md) R5: أُعيد تعريف `dedup_key` بالكامل.
الصيغة القديمة كانت تُفضِّل معرّف المنصّة الخام (platform:external_id) حين
توفّر، وهو فريد بطبيعته لكنه **لا يكتشف تكرارًا حقيقيًا** (نفس الوظيفة
منشورة مرتين بمعرّفين مختلفين على نفس المنصّة أو منصّتين).

مراجعة B2 R10 (docs/reports/B2-review-2.md): صيغة R5 (company+title+city+
apply_url_path) كانت لا تزال تُنتج تكرارًا حقيقيًا بنسبة ~55% — لأن بعض
مصادر Workable (وكالات توظيف مثل Eram Talent/Hudson Manpower) تُرجع نفس
الوظيفة (نفس apply_url) مرارًا ضمن استجابة واحدة، مرة لكل مدينة "مرشَّحة"،
وبما أن city كانت جزءًا من المفتاح، كل مدينة أنتجت dedup_key مختلفًا لنفس
الإعلان الحقيقي. **الصيغة الجديدة (R10): هوية الإعلان = apply_url وحده حين
متوفر** — نفس رابط التقديم = نفس الطلب الفعلي بصرف النظر عمّا يُذكر بجانبه
من مدن. تعدّد المواقع لنفس apply_url يُعبَّر عنه بحقل `jobs.locations`
(مصفوفة JSON تُجمَّع بواسطة discovery.py)، لا بصفوف منفصلة.

    apply_url متوفر:  sha1(source_id + '|' + normalize_apply_url(apply_url))
    بلا apply_url:    sha1(company_key + '|' + normalize(title) + '|' + normalize(location))

تضمين source_id (لا company) مع apply_url يمنع تصادمًا نظريًا بين مصدرين
مختلفين لو تطابق رابطاهما المطبَّعان صدفة، بلا حاجة لمقارنة اسم الشركة (الذي
قد يُكتب بصيغ مختلفة قليلاً عبر مصادر مختلفة لنفس apply_url أصلاً غير وارد
عمليًا). الرجوع لـ(شركة+مسمى+موقع) يبقى فقط للصفوف بلا apply_url إطلاقًا
(أقلية — SmartRecruiters غالبًا).
"""
from __future__ import annotations

import hashlib
import re
from urllib.parse import urlsplit

_ARABIC_DIACRITICS = re.compile(r"[ً-ْ]")
_NON_ALNUM = re.compile(r"[^a-z0-9؀-ۿ]+")
_WHITESPACE = re.compile(r"\s+")

# لواحق شركات شائعة تُحذف عند بناء company_key حتى لا يُعدّ "Zeeco Inc" و"Zeeco"
# شركتين مختلفتين (الدليل §3.4). مكتوبة هنا بصيغتها بعد normalize_text (أي
# بعد توحيد ة→ه، ى→ي) لأن company_key يقارن التوكنات بعد التطبيع لا قبله.
_COMPANY_SUFFIXES = [
    "company", "co", "co.", "ltd", "ltd.", "llc", "l.l.c", "inc", "inc.",
    "international", "holding", "holdings", "group", "corporation", "corp",
    "الشركه", "شركه", "المحدوده", "القابضه", "مجموعه",
]


def normalize_text(value: str | None) -> str:
    """يحوّل النص لصيغة موحّدة: أحرف صغيرة، بدون تشكيل عربي، بدون علامات ترقيم،
    وتوحيد الحروف العربية الشائعة الاختلاف (أ/إ/آ→ا، ة→ه، ى→ي) حسب الدليل §3.4."""
    if not value:
        return ""
    value = value.strip().lower()
    value = _ARABIC_DIACRITICS.sub("", value)
    value = value.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    value = value.replace("ة", "ه").replace("ى", "ي")
    value = _NON_ALNUM.sub(" ", value)
    return _WHITESPACE.sub(" ", value).strip()


def company_key(company_name: str | None) -> str:
    """اسم الشركة بعد التطبيع وحذف اللواحق الشائعة — يُستخدم لمطابقة نفس
    الشركة عبر مصادر مختلفة قد تكتب اسمها بصيغ مختلفة قليلاً."""
    normalized = normalize_text(company_name)
    if not normalized:
        return ""
    tokens = [t for t in normalized.split(" ") if t not in _COMPANY_SUFFIXES]
    return " ".join(tokens) if tokens else normalized


def normalize_apply_url(url: str | None) -> str:
    """مراجعة B2 R10: يطبّع رابط التقديم لهوية الإعلان الحقيقية —
    مخطط+مضيف+مسار فقط (بلا سلسلة استعلام `?...` ولا جزء `#...`)، مع تصغير
    حروف المخطط والمضيف (غير حسّاسة لحالة الأحرف بمعيار URL) مع إبقاء
    المسار كما هو (معرّفات الوظائف حسّاسة لحالة الأحرف غالبًا). يُرجع سلسلة
    فارغة إن لم يوجد رابط صالح (لا مضيف)، ليعتمد `dedup_key` عندها على
    الملاذ الأخير (شركة+مسمى+موقع)."""
    if not url or not url.strip():
        return ""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return ""
    netloc = parts.netloc.lower()
    if not netloc:
        return ""
    scheme = (parts.scheme or "https").lower()
    path = parts.path or ""
    return f"{scheme}://{netloc}{path}"


def _url_path_no_query(url: str | None) -> str:
    """محفوظة لتوافق الاختبارات القديمة/أي استدعاء خارجي — مسار الرابط فقط
    بلا مخطّط/مضيف/سلسلة استعلام/جزء. لم تعد تُستخدَم داخل `dedup_key` نفسه
    بعد R10 (اُستبدلت بـ`normalize_apply_url`)."""
    if not url:
        return ""
    try:
        return urlsplit(url.strip()).path or ""
    except ValueError:
        return url.strip()


def dedup_key(
    company_name: str | None,
    job_title: str | None,
    city: str | None = None,
    apply_url: str | None = None,
    source_id: int | str | None = None,
) -> str:
    """مفتاح كشف التكرار — هوية الإعلان الحقيقية (مراجعة B2 R10):

        apply_url متوفر:  sha1(source_id + '|' + normalize_apply_url(apply_url))
        بلا apply_url:    sha1(company_key + '|' + normalize(title) + '|' + normalize(city))

    `city` هنا يقبل أي نص موقع خام (location_text) — الاسم محفوظ للتوافق مع
    استدعاءات قائمة. خرج ثابت الطول دائمًا (40 حرف hex)."""
    norm_url = normalize_apply_url(apply_url)
    if norm_url:
        prefix = "" if source_id is None else str(source_id)
        base = f"{prefix}|{norm_url}"
    else:
        base = "|".join(
            [
                company_key(company_name),
                normalize_text(job_title),
                normalize_text(city),
            ]
        )
    return hashlib.sha1(base.encode("utf-8")).hexdigest()
