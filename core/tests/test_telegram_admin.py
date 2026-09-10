"""اختبارات core/app/telegram_admin.py (B8) — بوابة الوصول is_owner_chat
وتوجيه handle_update لمحادثات غير مصرَّح بها فقط (بلا قاعدة بيانات، تعمل
دومًا). فشل مغلق (fail-closed) عمدًا: نفس فلسفة app.auth.require_admin_token
— غياب MASAR_OWNER_CHAT_ID بالبيئة يُعطّل بوت الأدمن بالكامل بدل معاملة قيمة
فارغة كمطابقة بالخطأ.

أوامر الأدمن الفعلية (تسجيل عميل، تفعيل/إيقاف، تمديد، تصنيف...) تحتاج
قاعدة بيانات حقيقية — موجودة بملف منفصل test_telegram_admin_db.py عمدًا
(نفس سبب الفصل بـtest_telegram_onboarding.py/_db.py: pytest.skip على
مستوى الوحدة كان سيُسقط حتى هذه الاختبارات النقية معه)."""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app import telegram_admin as admin
from app.telegram_client import ChatEvent


def _run(coro):
    return asyncio.run(coro)


def test_is_owner_chat_true_when_matches(monkeypatch):
    monkeypatch.setenv("MASAR_OWNER_CHAT_ID", "12345")
    assert admin.is_owner_chat(12345) is True


def test_is_owner_chat_false_for_other_chat_id(monkeypatch):
    monkeypatch.setenv("MASAR_OWNER_CHAT_ID", "12345")
    assert admin.is_owner_chat(99999) is False


def test_is_owner_chat_fails_closed_when_env_unset(monkeypatch):
    monkeypatch.delenv("MASAR_OWNER_CHAT_ID", raising=False)
    assert admin.is_owner_chat(12345) is False
    assert admin.is_owner_chat(0) is False


def test_is_owner_chat_fails_closed_on_malformed_env(monkeypatch):
    monkeypatch.setenv("MASAR_OWNER_CHAT_ID", "not-a-number")
    assert admin.is_owner_chat(12345) is False


@pytest.mark.parametrize("chat_id", [12345, -1, 0])
def test_handle_update_ignores_non_owner_silently(monkeypatch, chat_id):
    monkeypatch.setenv("MASAR_OWNER_CHAT_ID", "999999999")
    calls: list[Any] = []

    class _Client:
        async def send_message(self, *a, **kw):
            calls.append((a, kw))

    event = ChatEvent(
        chat_id=chat_id, text="hi", is_callback=False, callback_data="", callback_query_id=None,
        message_id=1, document=None, photo=None, contact=None, from_user_id=chat_id,
    )
    _run(admin.handle_update({}, event, _Client()))
    assert calls == []  # لا أي ردّ لمحادثة غير مصرَّح بها
