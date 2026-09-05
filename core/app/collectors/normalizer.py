"""
تطبيع أسماء الشركات/المسميات الوظيفية ومفتاح كشف التكرار — بنفس منطق
كشف التكرار المعتمد في المرحلة 0 (تطبيع الاسم، وليس مقارنة الروابط الخام).
"""
from __future__ import annotations

import re

_ARABIC_DIACRITICS = re.compile(r"[ً-ْ]")
_NON_ALNUM = re.compile(r"[^a-z0-9؀-ۿ]+")
_WHITESPACE = re.compile(r"\s+")


def normalize_text(value: str | None) -> str:
    """يحوّل النص لصيغة موحّدة: أحرف صغيرة، بدون تشكيل عربي، بدون علامات ترقيم."""
    if not value:
        return ""
    value = value.strip().lower()
    value = _ARABIC_DIACRITICS.sub("", value)
    value = _NON_ALNUM.sub(" ", value)
    return _WHITESPACE.sub(" ", value).strip()


def dedup_key(company_name: str | None, job_title: str | None) -> str:
    """مفتاح كشف التكرار: تطبيع (اسم الشركة + المسمى الوظيفي) معًا."""
    return f"{normalize_text(company_name)}::{normalize_text(job_title)}"
