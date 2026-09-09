"""
Masar Core Scheduler — عملية منفصلة (core-scheduler بـdocker-compose) تُشغّل
جولة الجامع كل 30 دقيقة (المرحلة 2، B2) وجولة المخطّط اليومي/الساعي
(المرحلة 3، B3، الدليل §3.8).

B2: رُبط فعليًا بـcore.app.discovery — يبذر sources من data/sources_seed.csv
بشكل idempotent عند الإقلاع (upsert بالرابط)، ثم يُشغّل أول جولة فورًا (حتى
لا ينتظر التحقق 30 دقيقة)، ثم كل 30 دقيقة بعدها. أي استثناء داخل جولة واحدة
لا يوقف الجدولة (discovery.run_round لا يرفع أبدًا؛ هذا الغلاف حماية إضافية).

B3: core.app.planner.run_plan_round() يُجدول عبر CronTrigger بتوقيت UTC —
06:00 الرياض = 03:00 UTC ثم كل ساعة حتى 15:00 الرياض = 12:00 UTC (10
تشغيلات/يوم، hour="3-12" minute=0)، مطابقة لتوقيت الرياض الثابت UTC+3 بلا
توقيت صيفي (نفس الثابت RIYADH_OFFSET بـplanner.py). كل تشغيلة idempotent —
تُكمّل فقط ما تبقّى للعملاء الذين لم يبلغوا هدفهم اليومي بعد (راجع تعليق
دورة run_plan_round بـplanner.py).

B4 (الإرسال والوارد — منفّذ آخر، إدراج محروس بلا لمس منطق B2/B3 أعلاه):
    - queue_builder_round: كل 10 دقائق، بناء/تحديث send_queue من الفرص
      المخطّطة (send_builder.py) — فقط ضمن نافذة الإرسال (الأحد-الخميس
      08:00-16:30 الرياض)؛ خارج النافذة تُتخطّى الدورة بصمت (تُبنى الطابور
      لاحقًا حين تُفتح النافذة، بلا حاجة لانتظار Cron إضافي).
    - sender_tick: كل دقيقة، إرسال الدفعة المستحقة (sender.py) — فحص
      النافذة والتباعد الزمني (jitter) داخلي بالكامل بـsender.send_tick.
    - inbox_round: كل 15 دقيقة، قراءة الوارد لكل صندوق بريد نشط
      (inbox.py) — بلا قيد نافذة (الارتدادات/الردود تصل في أي وقت).
"""
from __future__ import annotations

import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app import discovery, guarantee, inbox, pacing, planner, reports, send_builder, sender

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


def run_queue_builder_job() -> None:
    """B4: بناء/تحديث send_queue — فقط ضمن نافذة الإرسال (الأحد-الخميس
    08:00-16:30 الرياض). فحص النافذة هنا صراحة (لا داخل send_builder نفسها)
    حتى تبقى send_builder.build_queue_round قابلة للاستدعاء اليدوي/الاختباري
    بلا قيد وقت عبر /admin/mail/queue-now."""
    try:
        if not pacing.is_in_window(pacing.to_riyadh_naive(pacing.utc_now())):
            logger.info("queue_builder: خارج نافذة الإرسال — تخطّي هذه الدورة")
            return
        result = send_builder.build_queue_round()
        logger.info("نتيجة بناء طابور الإرسال: %s", result)
    except Exception:  # noqa: BLE001
        logger.exception("بناء طابور الإرسال فشل بخطأ غير متوقع — ستُحاول مجددًا بالدورة القادمة")


def run_sender_tick_job() -> None:
    """B4: إرسال الدفعة المستحقة — فحص النافذة والتباعد الزمني داخلي
    بالكامل بـsender.send_tick (بما في ذلك وضع sink/dry-run الذي قد يتجاوز
    قيد النافذة عمدًا للاختبار المحلي — راجع توثيق sender.py)."""
    try:
        result = sender.send_tick()
        logger.info("نتيجة دورة الإرسال: %s", result)
    except Exception:  # noqa: BLE001
        logger.exception("دورة الإرسال فشلت بخطأ غير متوقع — ستُحاول مجددًا بالدقيقة القادمة")


def run_inbox_round_job() -> None:
    """B4: قراءة الوارد — بلا قيد نافذة (الارتدادات/ردود الشركات تصل بأي وقت)."""
    try:
        result = inbox.run_inbox_round()
        logger.info("نتيجة جولة الوارد: %s", result)
    except Exception:  # noqa: BLE001
        logger.exception("جولة الوارد فشلت بخطأ غير متوقع — ستُحاول مجددًا بالدورة القادمة")


def run_daily_reports_job() -> None:
    """B5a: تقرير العميل اليومي — 19:00 الرياض (16:00 UTC، راجع CronTrigger
    أدناه) لكل عميل نشط، idempotent per (customer_id, report_date)."""
    try:
        result = reports.run_reports_round()
        logger.info("نتيجة جولة التقارير اليومية: %s", result)
    except Exception:  # noqa: BLE001
        logger.exception("جولة التقارير اليومية فشلت بخطأ غير متوقع — ستُحاول مجددًا غدًا")


def run_guarantee_round_job() -> None:
    """B5a: تقييم دفتر الضمان — 00:30 الرياض (21:30 UTC اليوم السابق) لكل
    اشتراك انتهت فترته فعليًا (تمديد يومين تلقائي/تعويض/تمديد انقطاع بريد)."""
    try:
        result = guarantee.run_guarantee_round()
        logger.info("نتيجة جولة تقييم الضمان: %s", result)
    except Exception:  # noqa: BLE001
        logger.exception("جولة تقييم الضمان فشلت بخطأ غير متوقع — ستُحاول مجددًا غدًا")


def main() -> None:
    try:
        seed_result = discovery.seed_sources()
        logger.info("بذر المصادر عند الإقلاع: %s", seed_result)
    except Exception:
        logger.exception("تعذّر بذر المصادر عند الإقلاع — سيستمر التشغيل بما هو موجود بالقاعدة")

    scheduler = BlockingScheduler(timezone="UTC")
    # next_run_time الافتراضي لـIntervalTrigger = الآن + 30 دقيقة؛ نستدعي
    # run_collector_round() يدويًا مرة إضافية أدناه فورًا (بلا انتظار جلسة التحقق
    # الأولى) حتى لا تنتظر 30 دقيقة كاملة بعد كل نشر.
    scheduler.add_job(
        run_collector_round,
        trigger=IntervalTrigger(minutes=30),
        id="collector_round",
        max_instances=1,
        coalesce=True,
    )
    # B3: 06:00-15:00 الرياض (03:00-12:00 UTC) كل ساعة بالضبط — الدليل §3.8:
    # خطة 06:00 ثم تعبئة/top-up ساعية حتى 15:00. max_instances=1+coalesce=true
    # يمنعان تراكم تشغيلات متوازية لو تأخرت جولة سابقة (المخطّط ليس بالضرورة
    # أسرع من ساعة كاملة مع 1500 عميل حقيقيين مستقبلًا).
    scheduler.add_job(
        run_plan_round_job,
        trigger=CronTrigger(hour="3-12", minute=0),
        id="plan_round",
        max_instances=1,
        coalesce=True,
    )
    # B4: بناء طابور الإرسال كل 10 دقائق (فحص نافذة الإرسال داخليًا).
    scheduler.add_job(
        run_queue_builder_job,
        trigger=IntervalTrigger(minutes=10),
        id="queue_builder_round",
        max_instances=1,
        coalesce=True,
    )
    # B4: إرسال الدفعة المستحقة كل دقيقة (fine-grained pacing/jitter داخل sender.py).
    scheduler.add_job(
        run_sender_tick_job,
        trigger=IntervalTrigger(minutes=1),
        id="sender_tick",
        max_instances=1,
        coalesce=True,
    )
    # B4: قراءة الوارد كل 15 دقيقة (بلا قيد نافذة).
    scheduler.add_job(
        run_inbox_round_job,
        trigger=IntervalTrigger(minutes=15),
        id="inbox_round",
        max_instances=1,
        coalesce=True,
    )
    # B5a: التقرير اليومي 19:00 الرياض = 16:00 UTC (ثابت، بلا توقيت صيفي —
    # نفس ثابت RIYADH_OFFSET بكل الملفات).
    scheduler.add_job(
        run_daily_reports_job,
        trigger=CronTrigger(hour=16, minute=0),
        id="daily_reports",
        max_instances=1,
        coalesce=True,
    )
    # B5a: تقييم دفتر الضمان يوميًا 00:30 الرياض = 21:30 UTC (اليوم السابق).
    scheduler.add_job(
        run_guarantee_round_job,
        trigger=CronTrigger(hour=21, minute=30),
        id="guarantee_round",
        max_instances=1,
        coalesce=True,
    )
    logger.info(
        "Masar Core Scheduler بدأ التشغيل — جولة جامع فورًا ثم كل 30 دقيقة؛ "
        "جولة تخطيط 03:00-12:00 UTC (06:00-15:00 الرياض) كل ساعة؛ "
        "B4: بناء طابور كل 10 دقائق، إرسال كل دقيقة، وارد كل 15 دقيقة؛ "
        "B5a: تقرير يومي 16:00 UTC، تقييم ضمان 21:30 UTC."
    )
    run_collector_round()
    scheduler.start()


if __name__ == "__main__":
    main()
