"""
تطبيع أسماء الشركات/المسميات الوظيفية ومفتاح كشف التكرار — بنفس منطق
كشف التكرار المعتمد في المرحلة 0 (تطبيع الاسم، وليس مقارنة الروابط الخام).

B2: وسّعنا `dedup_key` لتطبيق منطق الدليل §3.4 بدقة أكبر:
    1. إن توفّر معرّف منصّة معروف (platform:id من ATS) → يُستخدم مباشرة
       (فريد بطبيعته، لا حاجة لتطبيع).
    2. وإلا → sha1(company_key + '|' + normalize(title) + '|' + normalize(city)).
كلا الشكلين يبقيان ضمن حد 600 حرف لعمود jobs.dedup_key.
"""
from __future__ import annotations

import hashlib
import re

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


def dedup_key(
    company_name: str | None,
    job_title: str | None,
    city: str | None = None,
    external_key: str | None = None,
) -> str:
    """مفتاح كشف التكرار (الدليل §3.4):

    - external_key (مثال: "greenhouse:12345") يُستخدم كما هو إن توفّر — فريد
      بطبيعته من المصدر نفسه ولا يحتاج تطبيعًا.
    - وإلا: sha1(company_key + '|' + normalize(title) + '|' + normalize(city)).

    التوقيع القديم (company_name, job_title) فقط لا يزال يعمل (توافق خلفي
    لأي كود سابق كان يستدعيه بوسيطين فقط).
    """
    if external_key:
        cleaned = external_key.strip()
        if cleaned:
            return cleaned[:600]

    base = f"{company_key(company_name)}|{normalize_text(job_title)}|{normalize_text(city)}"
    return hashlib.sha1(base.encode("utf-8")).hexdigest()
