"""
Masar Core Scheduler — عملية منفصلة (core-scheduler بـdocker-compose) تُشغّل
جولة الجامع كل 30 دقيقة (المرحلة 2، B2) وجولة المخطِّط اليومي/الساعي
(المرحلة 3، B3، الدليل §3.8).

B2: رُبط فعليًا بـcore.app.discovery — يبذر sources من data/sources_seed.csv
بشكل idempotent عند الإقلاع (upsert بالرابط)، ثم يُشغّل أول جولة فورًا (حتى
لا ينتظر التحقق 30 دقيقة)، ثم كل 30 دقيقة بعدها. أي استثناء داخل جولة واحدة
لا يوقف الجدولة (discovery.run_round لا يرفع أبدًا؛ هذا الغلاف حماية إضافية).

B3: core.app.planner.run_plan_round() يُجدول عبر CronTrigger بتوقيت UTC —
06:00 الرياض = 03:00 UTC ثم كل ساعة حتى 15:00 الرياض = 12:00 UTC (10
تشغيلات/يوم، hour="3-12" minute=0)، مطابقة لتوقيت الرياض الثابت UTC+3 بلا
توقيت صيفي (نفس الثابت RIYADH_OFFSET بـplanner.py). كل تشغيلة idempotent —
تُكمِّل فقط ما تبقّى للعملاء الذين لم يبلغوا هدفهم اليومي بعد (راجع تعليق
run_plan_round بـplanner.py)."""
from __future__ import annotations

import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app import discovery, planner

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("masar-scheduler")


def run_collector_round() -> None:
    try:
        result = discovery.run_round()
        logger.info("نتيجة الجولة: %s", result)
    except Exception:  # noqa: BLE001 — لا يجب أن يقتل جدولة APScheduler أبدًا
        logger.exception("جولة الجامع فشلت بخطأ غير متوقع — ستُحاول مجددًا بالجولة القادمة")


def run_plan_round_job() -> None:
    try:
        result = planner.run_plan_round()
        logger.info("نتيجة جولة التخطيط: %s", result)
    except Exception:  # noqa: BLE001 — نفس فلسفة run_collector_round أعلاه
        logger.exception("جولة التخطيط فشلت بخطأ غير متوقع — ستُحاول مجددًا بالتشغيلة القادمة")


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
    # B3: 06:00-15:00 الرياض (03:00-12:00 UTC) كل ساعة بالضبط — الدليل §3.8:
    # خطة 06:00 ثم تعبئة/top-up ساعية حتى 15:00. max_instances=1+coalesce=true
    # يمنعان تراكم تشغيلات متوازية لو تأخرت جولة سابقة (المخطِّط ليس بالضرورة
    # أسرع من ساعة كاملة مع 1500 عميل حقيقيين مستقبلًا).
    scheduler.add_job(
        run_plan_round_job,
        trigger=CronTrigger(hour="3-12", minute=0),
        id="plan_round",
        max_instances=1,
        coalesce=True,
    )
    logger.info(
        "Masar Core Scheduler بدأ التشغيل — جولة جامع فورًا ثم كل 30 دقيقة؛ "
        "جولة تخطيط 03:00-12:00 UTC (06:00-15:00 الرياض) كل ساعة."
    )
    run_collector_round()
    scheduler.start()


if __name__ == "__main__":
    main()
