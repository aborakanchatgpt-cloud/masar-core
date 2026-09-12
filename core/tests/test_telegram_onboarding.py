"""اختبارات core/app/telegram_onboarding.py (B8) — الدوال النقية فقط (بلا
قاعدة بيانات، تعمل دومًا بأي بيئة): بناء لوحات/رسائل المدن والمجالات،
تطبيع رقم الجوال، تصنيف نص حر لعائلة مهنية معروفة، واستخراج نص PDF حقيقي
صغير مبني بـpypdf نفسها (بلا أي اعتماد شبكي).

اختبارات مسار onboarding الكامل (تحتاج Postgres محلية) موجودة
بملف منفصل test_telegram_onboarding_db.py عمدًا — لو دُمجت بهذا الملف،
فإن pytest.skip(allow_module_level=True) الذي يُشترط بها (نفس نمط
test_catalog.py) كان سيُسقط حتى هذه الاختبارات النقية معه عند غياب
Postgres، رغم أنها لا تحتاجه إطلاقًا."""
from __future__ import annotations

import io

from app import telegram_onboarding as ob


def test_normalize_phone_canonicalizes_to_966_format():
    # B9/A1: كل صيغة شائعة لنفس الرقم يجب أن تنتج نفس السلسلة الموحّدة —
    # هذا هو ما يجعل تسجيل أحمد اليدوي (05...) وربط تيليجرام التلقائي
    # (966...) يتطابقان أخيرًا. راجع app/phone.py.
    assert ob.normalize_phone("0501234567") == "966501234567"
    assert ob.normalize_phone("+966 50 123 4567") == "966501234567"
    assert ob.normalize_phone("00966501234567") == "966501234567"
    assert ob.normalize_phone("966501234567") == "966501234567"
    assert ob.normalize_phone("501234567") == "966501234567"
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


def test_build_cities_confirm_buttons_no_cities_hides_delete_rows():
    buttons = ob.build_cities_confirm_buttons()
    flat = [b["callback_data"] for row in buttons for b in row]
    assert "city:done" in flat
    assert "city:more" in flat
    assert not any(cb.startswith("city:rm:") for cb in flat)


def test_build_cities_confirm_buttons_lists_delete_button_per_city():
    buttons = ob.build_cities_confirm_buttons(["الرياض", "جدة"])
    flat = [b["callback_data"] for row in buttons for b in row]
    assert "city:rm:0" in flat
    assert "city:rm:1" in flat
    texts = [b["text"] for row in buttons for b in row]
    assert any("الرياض" in t for t in texts)
    assert any("جدة" in t for t in texts)


def test_build_cities_confirm_buttons_hides_add_at_max():
    cities = [f"city_{i}" for i in range(ob.MAX_CITIES)]
    buttons = ob.build_cities_confirm_buttons(cities)
    flat = [b["callback_data"] for row in buttons for b in row]
    assert "city:more" not in flat
    assert "city:done" in flat


def test_build_families_message_empty_is_short_instruction():
    msg = ob.build_families_message([])
    assert str(ob.MAX_FAMILIES) in msg
    assert "بناءً على ما وصفته" not in msg


def test_build_families_message_lists_existing_families():
    msg = ob.build_families_message(["accounting"])
    assert "1. accounting" in msg


def test_build_family_choice_buttons_excludes_existing_and_has_other_escape():
    buttons = ob.build_family_choice_buttons(["chem_process"])
    flat = [b["callback_data"] for row in buttons for b in row]
    assert "family:chem_process" not in flat
    assert "family:quality" in flat
    assert flat[-1] == "family:other"


def test_build_family_choice_buttons_two_per_row():
    buttons = ob.build_family_choice_buttons([])
    # كل صف بلوحة الاختيار (عدا الزر الأخير "مجال آخر" المفرد، وربما آخر
    # صف زوجي لو كان عدد المجالات فرديًا) يحوي عنصرين بالضبط — 21 عائلة
    # بـFAMILY_BUTTON_ORDER (فردي) يعني آخر صف زوجي بعنصر واحد فقط.
    grid_rows = buttons[:-1]
    for row in grid_rows[:-1]:
        assert len(row) == 2
    assert len(grid_rows[-1]) in (1, 2)
    assert buttons[-1] == [{"text": "✏️ مجال آخر (اكتبه)", "callback_data": "family:other"}]


def test_build_my_list_edit_buttons_shows_add_when_below_max():
    buttons = ob.build_my_list_edit_buttons(
        ["الرياض"], ob.MAX_CITIES, "mymenu:city_add", "mymenu:city_rm:", "mymenu:done"
    )
    flat = [b["callback_data"] for row in buttons for b in row]
    assert "mymenu:city_add" in flat
    assert "mymenu:city_rm:0" in flat
    assert "mymenu:done" in flat


def test_build_my_list_edit_buttons_hides_add_at_max():
    items = [f"c{i}" for i in range(ob.MAX_CITIES)]
    buttons = ob.build_my_list_edit_buttons(items, ob.MAX_CITIES, "mymenu:city_add", "mymenu:city_rm:", "mymenu:done")
    flat = [b["callback_data"] for row in buttons for b in row]
    assert "mymenu:city_add" not in flat
    assert len([cb for cb in flat if cb.startswith("mymenu:city_rm:")]) == ob.MAX_CITIES


def test_family_button_order_excludes_out_of_scope_families():
    codes = [code for code, _ in ob.FAMILY_BUTTON_ORDER]
    assert "out_of_scope" not in codes
    assert "sales_excluded" not in codes
    assert len(codes) == len(set(codes))


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
    # (لا استثناء)، ما يعني "فشل تحليل" حسب MIN_CV_TEXT_CHARS حسب منطق الاستدعاء.
    pdf_bytes = _make_blank_pdf_bytes()
    result = ob.extract_pdf_text(pdf_bytes)
    assert result == ""


def test_extract_pdf_text_returns_empty_for_garbage_bytes():
    assert ob.extract_pdf_text(b"not a real pdf at all") == ""
