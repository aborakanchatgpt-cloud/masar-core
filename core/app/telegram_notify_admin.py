"""
Masar Core — إشعار الأدمن (أحمد) وإشعار العميل المباشر عبر تيليجرام (B8:
إزالة n8n بالكامل — بديل جسري `core-notify-admin`/`core-notify-customer`
اللذين كانا بـn8n، راجع n8n/workflows/core-notify-admin__UubD6Kba97XGJlVt.json
وn8n/workflows/core-notify-customer__S6AuI9VPaaQxhfhw.json). يُستدعى من أي
وحدة أخرى بالمستودع تحتاج تنبيه أحمد فورًا (تعويض ضمان يحتاج اعتماده، عطل
تحميل راوتر عند الإقلاع) بدل استدعاء HTTP خارجي لويبهوك n8n كما كان مفترضًا.

ملاحظة بحث موثّقة (وقت كتابة هذا الملف): `grep -rniE "n8n|notify" core/app/*.py`
لم يُظهر أي نقطة استدعاء HTTP فعلية لجسوري core-notify-admin/customer داخل
بايثون — الجسور كانت مُجهّزة بـn8n بانتظار مُستدعٍ لم يُكتب داخل Core بعد
(core-notify-admin نفسه كان يُمرّر فقط لركفلو فرعي آخر
 job-bot-claude-discovery-notifier__ZiVlsBibVps3pxGB.json الذي يرسل فعليًا
لمحادثة أحمد الثابتة 677475661). الدالتان هنا إذًا نقطتا دخول **جديدتان**
لا استبدال حرفي لسطر كود موجود — وُصّلتا فعليًا بنقطتين حقيقيتين (راجع
REPORT.md للتفاصيل الكاملة): core/app/guarantee.py (تعويض ضمان معلّق يحتاج
اعتماد المالك) وcore/app/main.py (فشل تحميل راوتر عند الإقلاع — نفس سيناريو
"B4 hotfix" الموثّق حرفيًا بتعليق main.py، أول مثال "crash alert" بالتكليف).
"""
from __future__ import annotations

import logging
import os

from sqlalchemy import text as sql_text

from app.discovery import get_engine
from app.telegram_notify import TelegramNotConfigured, send_message

logger = logging.getLogger("masar.telegram_notify_admin")

# معرّف محادثة أحمد الثابت بتيليجرام — نفس القيمة المستخدمة حرفيًا بكل
# ركفلوهات n8n التي كانت تُنبّهه مباشرة (chatId="677475661" بكل من
# job-bot-claude-discovery-notifier، job-bot-customer-retention-auto،
# webhook-watchdog، admin-error-alert-central). تبقى هنا قيمة افتراضية
# قابلة للتجاوز عبر TELEGRAM_ADMIN_CHAT_ID بالبيئة (مثال: لو غيّر أحمد
# حسابه لاحقًا) بلا تعديل كود.
DEFAULT_ADMIN_CHAT_ID = "677475661"


def _active_delegate_chat_ids() -> list[int]:
    """B9/B2: قائمة chat_id لكل مفوّض نشط **مربوط فعليًا** — best-effort:
    أي خطأ قاعدة بيانات هنا (مثال: نادرًا، ترتيب نشر جعل الجدول غير موجود
    بعد) يُسجّل ولا يمنع إشعار المالك نفسه (يُستدعى دومًا بعده لا قبله)."""
    try:
        engine = get_engine()
        with engine.connect() as conn:
            rows = conn.execute(
                sql_text(
                    "SELECT telegram_chat_id FROM admin_delegates "
                    "WHERE active = true AND telegram_chat_id IS NOT NULL"
                )
            ).all()
        return [int(r[0]) for r in rows]
    except Exception:  # noqa: BLE001
        logger.exception("تعذّر جلب قائمة المفوّضين النشطين لإشعارهم")
        return []


def notify_admin(text: str) -> bool:
    """يرسل `text` عبر بوت الأدمن (TELEGRAM_ADMIN_BOT_TOKEN) لمحادثة أحمد
    **ولكل مفوّض نشط مربوط فعليًا** (B9/B2، قرار أحمد: المفوّضون يستلمون
    نفس تنبيهات المالك). أفضل جهد لكل مستلم على حدة — فشل إرسال لمفوّض
    واحد لا يمنع إرسال البقية ولا يؤثر على قيمة الإرجاع.

    **best-effort دومًا**: أي فشل (توكن غير معرّف، خطأ شبكة، رفض تيليجرام)
    يُسجّل بالسجلّ فقط ولا يرفع استثناءً أبدًا — حتى لا يُسقط استدعاء تنبيه
    إداري أي مسار حرج آخر (تقييم ضمان، إقلاع الخدمة نفسه) بخطأ شبكة عابر،
    نفس فلسفة عزل الأخطاء المُتّبعة بكل جولات core/app/scheduler_main.py.
    يرجع True إن نجح إرسال **المالك** تحديدًا (لا تغيير بعقد الإرجاع القديم)،
    بصرف النظر عن نجاح/فشل إشعار المفوّضين."""
    chat_id = os.environ.get("TELEGRAM_ADMIN_CHAT_ID", DEFAULT_ADMIN_CHAT_ID)
    owner_sent = False
    token_missing = False
    try:
        send_message(chat_id, text, token_env_var="TELEGRAM_ADMIN_BOT_TOKEN")
        owner_sent = True
    except TelegramNotConfigured:
        token_missing = True
        logger.warning("TELEGRAM_ADMIN_BOT_TOKEN غير معرّف — تعذّر إشعار الأدمن: %s", text[:120])
    except Exception:  # noqa: BLE001 — best-effort دومًا، راجع توثيق الدالة أعلاه
        logger.exception("فشل إشعار الأدمن عبر تيليجرام (بداية النص: %s)", text[:120])

    if token_missing:
        return owner_sent  # نفس التوكن غير معرّف لكل مستلم — لا فائدة من محاولة المفوّضين

    for deleg_chat_id in _active_delegate_chat_ids():
        try:
            send_message(deleg_chat_id, text, token_env_var="TELEGRAM_ADMIN_BOT_TOKEN")
        except Exception:  # noqa: BLE001 — best-effort لكل مستلم على حدة
            logger.exception("فشل إشعار مفوّض عبر تيليجرام (chat_id=%s)", deleg_chat_id)

    return owner_sent


def notify_customer(chat_id: int | str, text: str) -> bool:
    """نفس فلسفة notify_admin أعلاه (best-effort، لا يرفع استثناءً أبدًا)
    لكن لعميل محدد عبر بوت العميل — بديل جسر core-notify-customer (n8n)
    لأي مسار مستقبلي يحتاج إرسال رسالة عميل مفردة فورية (خارج الدفعات
    المُدارة بجولات scheduler_main.py التي تستدعي telegram_notify.send_message
    مباشرة بدل هذه الدالة — راجع تعليق reports_relay.py/retention.py لسبب
    عدم استخدامها هناك: تلك الوحدات تحتاج معرفة الفشل الفعلي per-item
    (لتحديد هل تُعلّم كمُسلّمة أم لا)، بعكس هذه الدالة best-effort الصرفة)."""
    try:
        send_message(chat_id, text, token_env_var="TELEGRAM_CUSTOMER_BOT_TOKEN")
        return True
    except TelegramNotConfigured:
        logger.warning("TELEGRAM_CUSTOMER_BOT_TOKEN غير معرّف — تعذّر إشعار العميل %s", chat_id)
        return False
    except Exception:  # noqa: BLE001
        logger.exception("فشل إشعار العميل %s عبر تيليجرام", chat_id)
        return False
