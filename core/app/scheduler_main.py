"""
Masar Core Scheduler — عملية منفصلة (core-scheduler بـdocker-compose) تُشغّل
جولة الجامع كل 30 دقيقة (المرحلة 2 بالدليل، B2).

B2: رُبط فعليًا بـcore.app.discovery — يبذر sources من data/sources_seed.csv
بشكل idempotent عند الإقلاع (upsert بالرابط)، ثم يُشغّل أول جولة فورًا (حتى
لا ينتظر التحقق 30 دقيقة)، ثم كل 30 دقيقة بعدها. أي استثناء داخل جولة واحدة
لا يوقف الجدولة (discovery.run_round لا يرفع أبدًا؛ هذا الغلاف حماية إضافية).
"""
from __future__ import annotations

import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app import discovery

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("masar-scheduler")


def run_collector_round() -> None:
    try:
        result = discovery.run_round()
        logger.info("نتيجة الجولة: %s", result)
    except Exception:  # noqa: BLE001 — لا يجب أن يقتل جدولة APScheduler أبدًا
        logger.exception("جولة الجامع فشلت بخطأ غير متوقع — ستُحاول مجددًا بالجولة القادمة")


def main() -> None:
    try:
        seed_result = discovery.seed_sources()
        logger.info("بذر المصادر عند الإقلاع: %s", seed_result)
    except Exception:
        logger.exception("تعذّر بذر المصادر عند الإقلاع — سيستمر التشغيل بما هو موجود بالقاعدة")

    scheduler = BlockingScheduler(timezone="UTC")
    # next_run_time الافتراضي لـIntervalTrigger = الآن + 30 دقيقة؛ نستدعي
    # run_collector_round() يدويًا مرة إضافية أدناه فورًا (بلا انتظار الدورة
    # الأولى) حتى لا تنتظر جلسة التحقق 30 دقيقة كاملة بعد كل نشر.
    scheduler.add_job(
        run_collector_round,
        trigger=IntervalTrigger(minutes=30),
        id="collector_round",
        max_instances=1,
        coalesce=True,
    )
    logger.info("Masar Core Scheduler بدأ التشغيل — تشغيل أول جولة فورًا ثم كل 30 دقيقة.")
    run_collector_round()
    scheduler.start()


if __name__ == "__main__":
    main()
