"""اختبارات core/app/telegram_onboarding.py (B8) — الدوال النقية فقط (بلا
قاعدة بيانات، تعمل دومًا بأي بيئة): بناء لوحات/رسائل المدن والمجالات،
تطبيع رقم الجوال، تصنيف نص حرّ لعائلة مهنية معروفة، واستخراج نص PDF حقيقي
صغير مبني بـpypdf نفسها (بلا أي اعتماد شبكي).

اختبارات مسار onboarding الكامل (تحتاج Postgres محلية مهاجَرة) موجودة
بملف منفصل test_telegram_onboarding_db.py عمدًا — لو دُمجت بهذا الملف،
فإن pytest.skip(allow_module_level=True) الذي يُشترط بها (نفس نمط
test_catalog.py) كان سيُسقط حتى هذه الاختبارات النقية معه عند غياب
Postgres، رغم أنها لا تحتاجه إطلاقًا."""
from __future__ import annotations

import io

from app import telegram_onboarding as ob


def test_normalize_phone_strips_non_digits():
    assert ob.normalize_phone("+966 50 000 0000") == "966500000000"
    assert ob.normalize_phone("0501234567") == "0501234567"
    assert ob.normalize_phone("") == ""


def test_build_region_buttons_covers_all_regions_plus_flex_and_other():
    buttons = ob.build_region_buttons()
    flat = [b["callback_data"] for row in buttons for b in row]
    for region in ob.REGIONS:
        assert f"city:{region}" in flat
    assert "city:flex" in flat
    assert "city:other" in flat


def test_build_cities_message_lists_all_cities_numbered():
    msg = ob.build_cities_message(["الرياض", "جدة"])
    assert "1. الرياض" in msg
    assert "2. جدة" in msg


def test_build_families_buttons_hides_add_when_max_reached():
    families = [f"family_{i}" for i in range(ob.MAX_FAMILIES)]
    buttons = ob.build_families_buttons(families)
    flat = [b["callback_data"] for row in buttons for b in row]
    assert "families:add" not in flat
    assert any(cb.startswith("families:rm:") for cb in flat)


def test_build_families_buttons_shows_add_when_below_max():
    buttons = ob.build_families_buttons(["a"])
    flat = [b["callback_data"] for row in buttons for b in row]
    assert "families:add" in flat


def test_classify_family_input_adds_matched_family(monkeypatch):
    monkeypatch.setattr(ob, "classify_family", lambda text: "accounting" if "محاسب" in text else None)
    matched, families = ob.classify_family_input([], "محاسب أول")
    assert matched == "accounting"
    assert families == ["accounting"]


def test_classify_family_input_returns_none_for_unmatched_text(monkeypatch):
    monkeypatch.setattr(ob, "classify_family", lambda text: None)
    matched, families = ob.classify_family_input([], "كلام غير مفهوم")
    assert matched is None
    assert families == []


def test_classify_family_input_does_not_duplicate_existing_family(monkeypatch):
    monkeypatch.setattr(ob, "classify_family", lambda text: "accounting")
    matched, families = ob.classify_family_input(["accounting"], "محاسب")
    assert matched is None
    assert families == ["accounting"]


def test_classify_family_input_respects_max_families(monkeypatch):
    monkeypatch.setattr(ob, "classify_family", lambda text: "new_family")
    existing = [f"f{i}" for i in range(ob.MAX_FAMILIES)]
    matched, families = ob.classify_family_input(existing, "أي نص")
    assert matched is None
    assert families == existing


def test_as_list_handles_native_list_and_json_string_and_junk():
    assert ob._as_list(["a", "b"]) == ["a", "b"]
    assert ob._as_list('["a", "b"]') == ["a", "b"]
    assert ob._as_list(None) == []
    assert ob._as_list("not json") == []


def _make_blank_pdf_bytes() -> bytes:
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_extract_pdf_text_returns_empty_string_for_blank_page_pdf():
    # صفحة فارغة بلا أي نص — extract_text() يجب أن يُرجع سلسلة فارغة
    # (لا استثناء)، ما يعني "فشل تحليل" حسب MIN_CV_TEXT_CHARS بمنطق الاستدعاء.
    pdf_bytes = _make_blank_pdf_bytes()
    result = ob.extract_pdf_text(pdf_bytes)
    assert result == ""


def test_extract_pdf_text_returns_empty_for_garbage_bytes():
    assert ob.extract_pdf_text(b"not a real pdf at all") == ""
