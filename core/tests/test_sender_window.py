"""اختبار وحدة (بلا قاعدة بيانات) لفحص نافذة الإرسال بـcore/app/sender.py —
تصحيح F2 بمراجعة B3B4-live-review.md (استنتاج متوسط 2): `send_tick` كان
يتجاوز نافذة الإرسال الأحد-الخميس 08:00-16:30 بتوقيت الرياض كليًا طالما
MAIL_SINK_SMTP معرّف (الافتراضي الدائم بـdocker-compose.yml)، ما يعني عمليًا
أن الإرسال الفعلي التلقائي لم يكن محكومًا بالنافذة إطلاقًا. هذا الملف يتحقق
من ثلاثة أمور:

    1. خارج النافذة → لا إرسال إطلاقًا، حتى مع MAIL_SINK_SMTP معرّفًا.
    2. داخل النافذة → send_tick يتابع (يتجاوز فحص النافذة، يصل لمرحلة
       المطالبة بالدفعة `_claim_due_batch`).
    3. `MAIL_IGNORE_SEND_WINDOW=true` يعمل فقط حين DRY_RUN فعّال (MAIL_LIVE
       ليست true) — لا يعمل إطلاقًا بوضع MAIL_LIVE=true.

لا حاجة لقاعدة بيانات حقيقية هنا: عندما يُرفَض الإرسال بسبب النافذة تعود
send_tick فورًا قبل أي استعلام؛ وعندما يُتوقَّع تجاوز الفحص، نُموّه
`sender._claim_due_batch` لإرجاع دفعة فارغة بدل الاتصال بقاعدة بيانات حقيقية
(الهدف هنا فحص منطق البوابة (gate) نفسه، لا منطق المطالبة/الإرسال الذي
يغطّيه test_sender_idempotency.py ضد Postgres حقيقي)."""
from __future__ import annotations

from datetime import datetime

import pytest

from app import pacing, sender


class _EngineSentinel:
    """كائن بديل لا يمكن استخدامه إطلاقًا — أي استدعاء لأي طريقة عليه (مثل
    `.begin()`/`.connect()`) يعني أن send_tick تجاوز فحص النافذة بالخطأ
    ومضى لمرحلة الاتصال بقاعدة بيانات حقيقية بلا تمويه _claim_due_batch."""

    def __getattr__(self, name):  # pragma: no cover - يُستدعى فقط عند خطأ
        raise AssertionError(
            f"send_tick حاول استخدام engine.{name} رغم أن الاختبار خارج نافذة "
            "الإرسال ولا تجاوز صريح — البوابة لم تُطبّق بشكل صحيح"
        )


# نقطة زمنية معروفة خارج النافذة: الجمعة (ليس يوم إرسال إطلاقًا) — آمنة بصرف
# النظر عن التوقيت الفعلي عند تشغيل الاختبار.
OUTSIDE_WINDOW_RIYADH = datetime(2026, 9, 11, 12, 0)  # 2026-09-11 جمعة
# نقطة زمنية معروفة داخل النافذة: الأحد 10:00 صباحًا بتوقيت الرياض.
INSIDE_WINDOW_RIYADH = datetime(2026, 9, 6, 10, 0)  # 2026-09-06 أحد


def _patch_now(monkeypatch, riyadh_naive: datetime) -> None:
    monkeypatch.setattr(pacing, "to_riyadh_naive", lambda utc_dt: riyadh_naive)


def _clear_send_env(monkeypatch) -> None:
    for var in ("MAIL_SINK_SMTP", "MAIL_LIVE", "MAIL_IGNORE_SEND_WINDOW"):
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------------------
# 1. خارج النافذة → لا إرسال، حتى مع sink معرّفًا (هذا بالضبط الخلل المُصحّح).
# ---------------------------------------------------------------------------


def test_send_tick_outside_window_skips_even_with_sink_configured(monkeypatch):
    _clear_send_env(monkeypatch)
    monkeypatch.setenv("MAIL_SINK_SMTP", "mailpit:1025")
    _patch_now(monkeypatch, OUTSIDE_WINDOW_RIYADH)

    def _boom(*args, **kwargs):  # pragma: no cover
        raise AssertionError("_claim_due_batch لا يجب استدعاؤها خارج النافذة")

    monkeypatch.setattr(sender, "_claim_due_batch", _boom)

    result = sender.send_tick(engine=_EngineSentinel())

    assert result == {"ok": True, "sent": 0, "failed": 0, "note": "خارج نافذة الإرسال"}


def test_send_tick_outside_window_skips_without_sink_too(monkeypatch):
    """نفس السلوك السابق فعليًا قبل هذا التصحيح (تأكيد عدم كسره)."""
    _clear_send_env(monkeypatch)
    _patch_now(monkeypatch, OUTSIDE_WINDOW_RIYADH)

    monkeypatch.setattr(
        sender,
        "_claim_due_batch",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("لا يجب استدعاؤها")),
    )

    result = sender.send_tick(engine=_EngineSentinel())
    assert result["note"] == "خارج نافذة الإرسال"


# ---------------------------------------------------------------------------
# 2. داخل النافذة → send_tick يتجاوز فحص النافذة فعليًا (يصل لـ_claim_due_batch).
# ---------------------------------------------------------------------------


def test_send_tick_inside_window_proceeds_to_claim(monkeypatch):
    _clear_send_env(monkeypatch)
    monkeypatch.setenv("MAIL_SINK_SMTP", "mailpit:1025")
    _patch_now(monkeypatch, INSIDE_WINDOW_RIYADH)

    claimed_calls = []

    def _fake_claim(engine, limit):
        claimed_calls.append((engine, limit))
        return []

    monkeypatch.setattr(sender, "_claim_due_batch", _fake_claim)

    result = sender.send_tick(engine=object())

    assert len(claimed_calls) == 1
    assert result == {"ok": True, "sent": 0, "failed": 0, "claimed": 0}
    assert "note" not in result


# ---------------------------------------------------------------------------
# 3. MAIL_IGNORE_SEND_WINDOW=true يعمل فقط حين DRY_RUN فعّال (MAIL_LIVE ليست
#    true). بوضع MAIL_LIVE=true لا يعمل التجاوز إطلاقًا.
# ---------------------------------------------------------------------------


def test_ignore_send_window_override_bypasses_gate_in_dry_run(monkeypatch):
    _clear_send_env(monkeypatch)
    monkeypatch.setenv("MAIL_IGNORE_SEND_WINDOW", "true")
    monkeypatch.setenv("MAIL_LIVE", "false")  # DRY_RUN فعّال
    _patch_now(monkeypatch, OUTSIDE_WINDOW_RIYADH)

    claimed_calls = []
    monkeypatch.setattr(
        sender, "_claim_due_batch", lambda engine, limit: claimed_calls.append(1) or []
    )

    result = sender.send_tick(engine=object())

    assert len(claimed_calls) == 1
    assert "note" not in result


def test_ignore_send_window_override_inactive_when_mail_live_true(monkeypatch):
    _clear_send_env(monkeypatch)
    monkeypatch.setenv("MAIL_IGNORE_SEND_WINDOW", "true")
    monkeypatch.setenv("MAIL_LIVE", "true")  # الإنتاج الحي — DRY_RUN معطّل
    _patch_now(monkeypatch, OUTSIDE_WINDOW_RIYADH)

    monkeypatch.setattr(
        sender,
        "_claim_due_batch",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("لا يجب تجاوز النافذة إطلاقًا بوضع MAIL_LIVE=true")
        ),
    )

    result = sender.send_tick(engine=_EngineSentinel())
    assert result["note"] == "خارج نافذة الإرسال"


def test_ignore_send_window_override_inactive_when_env_false(monkeypatch):
    _clear_send_env(monkeypatch)
    monkeypatch.setenv("MAIL_LIVE", "false")
    _patch_now(monkeypatch, OUTSIDE_WINDOW_RIYADH)

    result = sender.send_tick(engine=_EngineSentinel())
    assert result["note"] == "خارج نافذة الإرسال"


def test_explicit_ignore_window_param_still_bypasses_regardless(monkeypatch):
    """`ignore_window=True` الصريح (الاستدعاء الإداري اليدوي عبر
    /admin/mail/send-now) يبقى يعمل بصرف النظر عن الوقت/الوضع — لم يتغيّر
    بهذا التصحيح."""
    _clear_send_env(monkeypatch)
    monkeypatch.setenv("MAIL_LIVE", "true")
    _patch_now(monkeypatch, OUTSIDE_WINDOW_RIYADH)

    claimed_calls = []
    monkeypatch.setattr(
        sender, "_claim_due_batch", lambda engine, limit: claimed_calls.append(1) or []
    )

    result = sender.send_tick(engine=object(), ignore_window=True)
    assert len(claimed_calls) == 1
    assert "note" not in result


# ---------------------------------------------------------------------------
# 4. اختبارات وحدة مباشرة على الدوال المساعدة الجديدة.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mail_live,expected",
    [("true", False), ("True", False), ("false", True), ("", True), (None, True)],
)
def test_is_dry_run(monkeypatch, mail_live, expected):
    monkeypatch.delenv("MAIL_LIVE", raising=False)
    if mail_live is not None:
        monkeypatch.setenv("MAIL_LIVE", mail_live)
    assert sender.is_dry_run() is expected


@pytest.mark.parametrize(
    "override,mail_live,expected",
    [
        ("true", "false", True),
        ("true", "true", False),
        ("false", "false", False),
        (None, "false", False),
    ],
)
def test_send_window_override_active(monkeypatch, override, mail_live, expected):
    monkeypatch.delenv("MAIL_IGNORE_SEND_WINDOW", raising=False)
    if override is not None:
        monkeypatch.setenv("MAIL_IGNORE_SEND_WINDOW", override)
    monkeypatch.setenv("MAIL_LIVE", mail_live)
    assert sender._send_window_override_active() is expected
