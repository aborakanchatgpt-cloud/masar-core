"""اختبارات app/phone.py (B9/A1) — توحيد صيغة رقم الجوال السعودي."""
from __future__ import annotations

from app.phone import canonical_phone


def test_local_format_with_leading_zero():
    assert canonical_phone("0501234567") == "966501234567"


def test_international_with_plus_and_spaces():
    assert canonical_phone("+966 50 123 4567") == "966501234567"


def test_international_with_00_prefix():
    assert canonical_phone("00966501234567") == "966501234567"


def test_already_canonical():
    assert canonical_phone("966501234567") == "966501234567"


def test_bare_nine_digits_starting_with_5():
    assert canonical_phone("501234567") == "966501234567"


def test_empty_and_none_like_input():
    assert canonical_phone("") == ""
    assert canonical_phone(None) == ""  # type: ignore[arg-type]


def test_non_saudi_number_falls_back_to_digits_only():
    # رقم لا يطابق أي نمط سعودي معروف — يُعاد كأرقام مجردة بلا محاولة
    # تخمين، بدل رفضه بالكامل.
    assert canonical_phone("+1 202 555 0199") == "12025550199"
