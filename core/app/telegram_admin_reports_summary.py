"""B6/v3: 📊 تقرير شامل ببوت الأدمن — توصيل الأزرار/الجلسة فوق منطق التجميع
البحت بـ`app.reports_summary` (نفس فصل واجهة/منطق المستخدَم بكل ميزات الأدمن
الأخرى — راجع docstring `telegram_admin_settings.py`).

**الفحص الفعلي:** متاح للمالك وللمفوّض النشط على السواء (`is_admin_chat`
بملف `telegram_admin.py`، لا فحص إضافي هنا) — بخلاف ⚙️ الإعدادات/👥
المفوّضين (مالك حصرًا)، هذا التقرير معلومات تشغيلية عادية مثل "📊 نظرة عامة"
و"📨 تقرير عميل" اللذين لا يحصران بالمالك أيضًا.

**تدفّق الفترة:** زر `admin:summary` يفتح قائمة بثلاث أزرار سريعة
(`summary:period:day|week|month`) تُنفّذ فورًا (بلا خطوة نصّية)، بالإضافة
لزر "📅 مدى مخصَّص" (`summary:custom`) يبدأ خطوتين نصّيتين متتاليتين
(`summary_custom_from` ثم `summary_custom_to`) — كل خطوة تتحقّق من صيغة
`YYYY-MM-DD` وتُعيد نفس السؤال برسالة رفض صريحة عند خطأ الصيغة (نفس نمط
إعادة الطلب المُستخدَم بخطوات رقمية أخرى بـ`telegram_admin.py`، مثل
`extend_days`/`status_id`)."""
from __future__ import annotations

from datetime import date
from typing import Any, Callable

from app import reports_summary
from app.discovery import get_engine
from app.telegram_client import TelegramClient
from app.telegram_nav import nav_rows

SaveSessionFn = Callable[[int, str, dict[str, Any]], None]

_PERIOD_LABELS = {"day": "يومي", "week": "أسبوعي", "month": "شهري"}

# اسمَا خطوتَي المدى المخصَّص — يستوردهما telegram_admin.py لتوجيه
# _handle_step_text (نفس نمط settings_mod.BANK_STEP_NAMES).
CUSTOM_STEP_NAMES = {"summary_custom_from", "summary_custom_to"}


async def reply_summary_menu(client: TelegramClient, chat_id: int) -> None:
    buttons = [
        [
            {"text": "📆 يومي", "callback_data": "summary:period:day"},
            {"text": "🗓️ أسبوعي", "callback_data": "summary:period:week"},
            {"text": "📅 شهري", "callback_data": "summary:period:month"},
        ],
        [{"text": "🎯 مدى مخصَّص", "callback_data": "summary:custom"}],
    ]
    buttons.extend(nav_rows(None, "admin:menu"))
    await client.send_message(chat_id, "📊 التقرير الشامل — اختر الفترة:", buttons=buttons)


async def send_period_report(client: TelegramClient, chat_id: int, period: str) -> None:
    start_date, end_date = reports_summary.compute_period_range(period)
    await _send_report(client, chat_id, start_date, end_date)


async def start_custom_range(client: TelegramClient, chat_id: int, save_session: SaveSessionFn) -> None:
    save_session(chat_id, "summary_custom_from", {})
    await client.send_message(
        chat_id,
        "اكتب تاريخ البداية بصيغة YYYY-MM-DD (مثال: 2026-09-01):",
        buttons=nav_rows("admin:summary", "admin:menu"),
    )


def _parse_date(text_value: str) -> date | None:
    try:
        return date.fromisoformat(text_value.strip())
    except ValueError:
        return None


async def handle_custom_step_text(
    step: str,
    chat_id: int,
    client: TelegramClient,
    session_data: dict[str, Any],
    text_value: str,
    save_session: SaveSessionFn,
    clear_session: Callable[[int], None],
) -> None:
    if step == "summary_custom_from":
        parsed = _parse_date(text_value)
        if parsed is None:
            await client.send_message(
                chat_id, "⚠️ صيغة غير صحيحة. اكتب تاريخ البداية بصيغة YYYY-MM-DD (مثال: 2026-09-01):"
            )
            return
        save_session(chat_id, "summary_custom_to", {"from": parsed.isoformat()})
        await client.send_message(
            chat_id,
            "تمام. الآن اكتب تاريخ النهاية بصيغة YYYY-MM-DD (مثال: 2026-09-11):",
            buttons=nav_rows("admin:summary", "admin:menu"),
        )
        return

    if step == "summary_custom_to":
        parsed = _parse_date(text_value)
        if parsed is None:
            await client.send_message(
                chat_id, "⚠️ صيغة غير صحيحة. اكتب تاريخ النهاية بصيغة YYYY-MM-DD (مثال: 2026-09-11):"
            )
            return
        start_iso = session_data.get("from")
        start_date = date.fromisoformat(start_iso) if start_iso else parsed
        clear_session(chat_id)
        await _send_report(client, chat_id, start_date, parsed)
        return


async def _send_report(client: TelegramClient, chat_id: int, start_date: date, end_date: date) -> None:
    engine = get_engine()
    payload = reports_summary.build_summary(engine, start_date, end_date)
    text_body = reports_summary.build_summary_text(payload)
    await client.send_message(chat_id, text_body, buttons=nav_rows(None, "admin:menu"))
