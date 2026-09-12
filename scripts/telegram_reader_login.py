#!/usr/bin/env python3
"""
scripts/telegram_reader_login.py — B12.1: يولّد TELEGRAM_READER_SESSION
(Telethon StringSession) تفاعليًا لحساب تيليجرام "القراءة" المخصّص
لمجمّع قنوات الوظائف (core/app/collectors/telegram_channels.py).

**يُشغّل محليًا من جهاز أحمد فقط** (لا على الخادم، ولا عبر أي جسر MCP) —
تسجيل الدخول التفاعلي يطلب رمز تحقق يصل لتطبيق تيليجرام على جواله مباشرة.
لا يُخزّن أي شيء بالمستودع أبدًا: السلسلة الناتجة تُلصَق يدويًا بـ.env
بالخادم كقيمة TELEGRAM_READER_SESSION (راجع .env.example للتوثيق الكامل).

**مهم:** هذا حساب Telegram شخصي عادي (رقم جوال + رمز تحقق) لا بوت — بوتات
تيليجرام (Bot API) لا تستطيع قراءة رسائل قنوات عامة لم تُضَف كأدمن فيها؛
Telethon (MTProto) بحساب مستخدم عادي هو المسار الوحيد الممكن لقراءة قنوات
لسنا أدمن فيها إطلاقًا.

المتطلبات المسبقة (يقوم بها أحمد مرة واحدة فقط قبل التشغيل):
    1. api_id/api_hash من https://my.telegram.org (تسجيل دخول برقم جواله).
    2. pip install telethon (أو استخدام بيئة core الافتراضية إن توفّرت محليًا).

الاستخدام:
    python scripts/telegram_reader_login.py
    (يطلب api_id ثم api_hash ثم رقم الجوال، فرمز التحقق المُرسَل للتطبيق،
     وربما كلمة مرور التحقق بخطوتين إن كانت مفعّلة على الحساب)
"""
from __future__ import annotations

import sys


def main() -> int:
    try:
        from telethon.sessions import StringSession
        from telethon.sync import TelegramClient
    except ImportError:
        print(
            "خطأ: حزمة telethon غير مثبَّتة. ثبّتها أولًا:\n    pip install telethon",
            file=sys.stderr,
        )
        return 1

    print("=== توليد جلسة قراءة تيليجرام لمجمّع قنوات الوظائف (مسار) ===\n")
    api_id_raw = input("api_id (من my.telegram.org): ").strip()
    api_hash = input("api_hash (من my.telegram.org): ").strip()
    try:
        api_id = int(api_id_raw)
    except ValueError:
        print("خطأ: api_id يجب أن يكون رقمًا صحيحًا", file=sys.stderr)
        return 1

    with TelegramClient(StringSession(), api_id, api_hash) as client:
        session_string = client.session.save()

    print("\n=== تم بنجاح ===")
    print("انسخ السطر التالي بالكامل والصقه بملف .env على الخادم (لا تشاركه بأي شات/رسالة):\n")
    print(f"TELEGRAM_READER_SESSION={session_string}")
    print(
        "\nثم أضف أيضًا (إن لم تكن موجودة أصلًا):\n"
        f"TELEGRAM_READER_API_ID={api_id}\n"
        f"TELEGRAM_READER_API_HASH={api_hash}\n"
        "\nوأعد إنشاء الحاوية (core-scheduler) حتى تُفعَّل القيم الجديدة."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
