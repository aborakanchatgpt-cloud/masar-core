"""اختبارات core/app/telegram_notify_admin.py — best-effort دومًا، بلا أي
استدعاء شبكة/قاعدة بيانات حقيقي (send_message وget_engine مموّهتان بالكامل
هنا؛ FakeEngine بسيط يكفي لتغطية `_active_delegate_chat_ids` بلا Postgres
حقيقي — نفس فلسفة الفصل النقي/المُتّصل بقاعدة بيانات المُتّبعة بكل
core/tests، لكن هنا التمويه الكامل يُغني عن ملف _db منفصل أصلًا).

B9/B2: notify_admin أصبحت ترسل للمالك **ولكل مفوّض نشط مربوط فعليًا**."""
from __future__ import annotations

from app import telegram_notify_admin as notify_admin_mod
from app.telegram_notify import TelegramNotConfigured


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, *a, **kw):
        return _FakeResult(self._rows)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeEngine:
    def __init__(self, rows):
        self._rows = rows

    def connect(self):
        return _FakeConn(self._rows)


def _patch_delegates(monkeypatch, rows):
    monkeypatch.setattr(notify_admin_mod, "get_engine", lambda: _FakeEngine(rows))


def test_notify_admin_sends_to_owner_only_when_no_delegates(monkeypatch):
    _patch_delegates(monkeypatch, [])
    sent: list[tuple] = []

    def fake_send_message(chat_id, text, *, token_env_var):
        sent.append((chat_id, text, token_env_var))

    monkeypatch.setattr(notify_admin_mod, "send_message", fake_send_message)

    result = notify_admin_mod.notify_admin("تنبيه تجريبي")

    assert result is True
    assert len(sent) == 1
    assert sent[0][0] == notify_admin_mod.DEFAULT_ADMIN_CHAT_ID


def test_notify_admin_sends_to_owner_and_active_delegates(monkeypatch):
    _patch_delegates(monkeypatch, [(111,), (222,)])
    sent: list[tuple] = []

    def fake_send_message(chat_id, text, *, token_env_var):
        sent.append((chat_id, text))

    monkeypatch.setattr(notify_admin_mod, "send_message", fake_send_message)

    result = notify_admin_mod.notify_admin("تنبيه للجميع")

    assert result is True
    sent_chat_ids = {c for c, _ in sent}
    assert sent_chat_ids == {notify_admin_mod.DEFAULT_ADMIN_CHAT_ID, 111, 222}
    assert all(t == "تنبيه للجميع" for _, t in sent)


def test_notify_admin_delegate_failure_does_not_affect_owner_result(monkeypatch):
    _patch_delegates(monkeypatch, [(111,)])

    def fake_send_message(chat_id, text, *, token_env_var):
        if chat_id == 111:
            raise RuntimeError("فشل شبكة للمفوّض")

    monkeypatch.setattr(notify_admin_mod, "send_message", fake_send_message)

    result = notify_admin_mod.notify_admin("تنبيه")
    assert result is True  # فشل مفوّض واحد لا يُسقط النتيجة الكلية


def test_notify_admin_skips_delegates_when_token_missing(monkeypatch):
    calls: list[int] = []
    _patch_delegates(monkeypatch, [(111,)])

    def fake_send_message(chat_id, text, *, token_env_var):
        calls.append(chat_id)
        raise TelegramNotConfigured("TELEGRAM_ADMIN_BOT_TOKEN غير معرّف")

    monkeypatch.setattr(notify_admin_mod, "send_message", fake_send_message)

    result = notify_admin_mod.notify_admin("تنبيه")

    assert result is False
    # لا فائدة من محاولة إرسال للمفوّضين بنفس التوكن غير المعرّف — استدعاء
    # واحد فقط (للمالك)، لا محاولة ثانية للمفوّض 111.
    assert calls == [notify_admin_mod.DEFAULT_ADMIN_CHAT_ID]


def test_active_delegate_chat_ids_returns_empty_list_on_db_error(monkeypatch):
    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(notify_admin_mod, "get_engine", _boom)
    assert notify_admin_mod._active_delegate_chat_ids() == []
