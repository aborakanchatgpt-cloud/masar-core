"""اختبارات core/app/telegram_admin.py (B8) — بوابة الوصول is_owner_chat
(بلا قاعدة بيانات، تعمل دومًا). فشل مغلق (fail-closed) عمدًا: نفس فلسفة
app.auth.require_admin_token — غياب MASAR_OWNER_CHAT_ID بالبيئة يُعطّل بوت
الأدمن بالكامل بدل معاملة قيمة فارغة كمطابقة بالخطأ.

B9/B2: `is_admin_chat` (المالك أو مفوّض نشط مربوط) استُحدثت فوق
is_owner_chat — مسارها لمحادثة **غير** المالك يحتاج استعلام admin_delegates
(قاعدة بيانات)، فانتقل اختبار `handle_update` لمحادثة غير مصرّح بها (الذي
كان هنا سابقًا، بلا قاعدة بيانات) إلى test_telegram_admin_db.py الذي يغطيه
الآن فعليًا (owner/delegate/غريب/ربط) بقاعدة بيانات حقيقية. يبقى هنا فقط
اختبار المسار السريع بلا قاعدة بيانات: is_admin_chat للمالك لا يستعلم
admin_delegates إطلاقًا (تحسين أداء متعمّد — راجع docstring الدالة).

أوامر الأدمن الفعلية (تسجيل عميل، تفعيل/إيقاف، تمديد، تصنيف، بحث، المفوّضون...)
تحتاج قاعدة بيانات حقيقية — موجودة بملف منفصل test_telegram_admin_db.py عمدًا (نفس سبب الفصل بـtest_telegram_onboarding.py/_db.py: pytest.skip على
مستوى الوحدة كان سيُسقط حتى هذه الاختبارات النقية معه)."""
from __future__ import annotations

import asyncio
from typing import Any

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


# ---------------------------------------------------------------------------
# B9/B2: is_admin_chat — مسار المالك السريع (بلا أي استعلام قاعدة بيانات)
# ---------------------------------------------------------------------------


def test_is_admin_chat_true_for_owner_without_touching_db(monkeypatch):
    """المالك دومًا `is_admin_chat` — والأهم هنا: بلا أي استدعاء get_engine
    إطلاقًا (المسار الشائع لكل رسالة من أحمد نفسه). get_engine مموّهة
    لترفع استثناءً فورًا لو استُدعيت، فنجاح الاختبار دليل أنها لم تُستدعَ."""
    monkeypatch.setenv("MASAR_OWNER_CHAT_ID", "12345")

    def _must_not_be_called():
        raise AssertionError("is_admin_chat لمحادثة المالك يجب ألا يستدعي get_engine إطلاقًا")

    monkeypatch.setattr(admin, "get_engine", _must_not_be_called)
    assert admin.is_admin_chat(12345) is True


# ---------------------------------------------------------------------------
# B9/A6: خطوة extend_days — إدخال غير رقمي يجب أن يُعيد الطلب (بلا لمس
# قاعدة البيانات إطلاقًا بهذا المسار: لا _clear_session ولا
# _reply_extend_subscription تُستدعيان قبل التحقق من صحة الرقم) بدل الاستمرار
# بصمت ضمني كما كان سابقًا.
# ---------------------------------------------------------------------------


def test_extend_days_step_reprompts_on_non_numeric_input():
    calls: list[Any] = []

    class _Client:
        async def send_message(self, *a, **kw):
            calls.append((a, kw))

    event = ChatEvent(
        chat_id=1, text="ثلاثين يوم", is_callback=False, callback_data="", callback_query_id=None,
        message_id=1, document=None, photo=None, contact=None, from_user_id=1,
    )
    _run(admin._handle_step_text(event, _Client(), "extend_days", {"customer_id": 7}))
    assert len(calls) == 1
    sent_text = calls[0][0][1]
    assert str(admin.EXTEND_DEFAULT_DAYS) in sent_text
    assert "لم أفهم" in sent_text
