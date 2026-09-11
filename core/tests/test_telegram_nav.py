"""اختبارات core/app/telegram_nav.py (B9/B1) — بنية بيانات بسيطة بلا قاعدة
بيانات ولا شبكة، تعمل دومًا."""
from __future__ import annotations

from app.telegram_nav import nav_rows, pop_nav, push_nav


def test_nav_rows_with_back_and_home():
    rows = nav_rows("admin:extend", "admin:menu")
    assert rows == [
        [
            {"text": "◀️ رجوع", "callback_data": "admin:extend"},
            {"text": "🏠 القائمة الرئيسية", "callback_data": "admin:menu"},
        ]
    ]


def test_nav_rows_without_back():
    rows = nav_rows(None, "admin:menu")
    assert rows == [[{"text": "🏠 القائمة الرئيسية", "callback_data": "admin:menu"}]]


def test_nav_rows_home_target_customer_bot():
    rows = nav_rows(None, "menu:home")
    assert rows[0][0]["callback_data"] == "menu:home"


def test_push_nav_appends_step_and_extra():
    data: dict = {}
    push_nav(data, "cities", {"selected": ["الرياض"]})
    assert data["nav"] == [{"step": "cities", "extra": {"selected": ["الرياض"]}}]


def test_push_nav_defaults_extra_to_empty_dict():
    data: dict = {}
    push_nav(data, "families")
    assert data["nav"] == [{"step": "families", "extra": {}}]


def test_push_nav_caps_stack_at_max():
    data: dict = {}
    for i in range(15):
        push_nav(data, f"step{i}")
    assert len(data["nav"]) == 10
    assert data["nav"][0]["step"] == "step5"  # أول 5 خطوات (0-4) أُسقطت
    assert data["nav"][-1]["step"] == "step14"


def test_pop_nav_returns_last_step_and_removes_it():
    data: dict = {}
    push_nav(data, "cities")
    push_nav(data, "families", {"x": 1})
    result = pop_nav(data)
    assert result == ("families", {"x": 1})
    assert data["nav"] == [{"step": "cities", "extra": {}}]


def test_pop_nav_returns_none_when_stack_empty():
    assert pop_nav({}) is None
    assert pop_nav({"nav": []}) is None
