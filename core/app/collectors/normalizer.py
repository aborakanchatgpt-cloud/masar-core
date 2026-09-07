"""
تطبيع أسماء الشركات/المسميات الوظيفية ومفتاح كشف التكرار — بنفس منطق
كشف التكرار المعتمد في المرحلة 0 (تطبيع الاسم، وليس مقارنة الروابط الخام).

مراجعة B2 (docs/reports/B2-review.md) R5: أُعيد تعريف `dedup_key` بالكامل.
الصيغة القديمة كانت تُفضّل معرّف المنصّة الخام (platform:external_id) حين
توفّر، وهو فريد بطبيعته لكنه **لا يكتشف تكرارًا حقيقياً** (نفس الوظيفة
منشورة مرتين بمعرّفين مختلفين على نفس المنصّة أو منصّتين). الصيغة الجديدة:

    sha1(company_key + '|' + normalize(title) + '|' + normalize(city) + '|'
         + مسار رابط التقديم بلا سلسلة الاستعلام (query string))

تضمين مسار الرابط (بلا `?utm_source=...` إلخ) يُفرّق صحيحًا بين وظيفتين
حقيقيتين مختلفتين بنفس (شركة+مسمى+مدينة) — حالة شائعة بمصادر عالمية كبرى
تفتح نفس المسمى بأكثر من مكتب — مع استمرار تجميع أي تكرار حرفي فعلي (نفس
الرابط، أو بلا رابط إطلاقًا مع تطابق تام بباقي الحقول) عند الإدراج مباشرةً
عبر `ON CONFLICT (dedup_key) DO NOTHING`، بدل الاكتفاء برصده لاحقًا بمقياس
`dup_ratio_24h` التقريبي فقط.
"""
from __future__ import annotations

import hashlib
import re
from urllib.parse import urlsplit

_ARABIC_DIACRITICS = re.compile(r"[ً-ْ]")
_NON_ALNUM = re.compile(r"[^a-z0-9؀-ۿ]+")
_WHITESPACE = re.compile(r"\s+")

# لواحق شركات شائعة تُحذف عند بناء company_key حتى لا يُعّد "Zeeco Inc" و"Zeeco"
شركتين مختلفتين (الدليل §3.4). مكتوبة هنا بصيغتها بعد normalize_text (أي
بعد توحيد ة→ه، ى→ي) لأن company_key يقارن التوكنات بعد التطبيع لا قبله.
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


def _url_path_no_query(url: str | None) -> str:
    """مسار الرابط فقط (بلا مخطّط/مضيف/سلسلة استعلام/جزء) — حتى لا تُعد
    نفس الوظيفة (نفس المسار) مكررة زورًا إن اختلفت معاملات تتبّع
    (`?utm_source=...`) بين جلبتين، ولا تُعد وظيفتان مختلفتان (مسارين
    مختلفين فعليًا) نفس الوظيفة."""
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
) -> str:
    """مفتاح كشف التكرار (مراجعة B2 R5):

        sha1(company_key(company) + '|' + normalize(title) + '|' +
             normalize(city) + '|' + مسار apply_url بلا سلسلة الاستعلام)

    خرج ثابت الطول دائمًا (40 حرف hex) — يبقى ضمن حد 600 حرف لعمود
    jobs.dedup_key بمسافة واسعة."""
    base = "|".join(
        [
            company_key(company_name),
            normalize_text(job_title),
            normalize_text(city),
            _url_path_no_query(apply_url),
        ]
    )
    return hashlib.sha1(base.encode("utf-8")).hexdigest()
