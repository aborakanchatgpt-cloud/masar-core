"""
Masar Core Scheduler — عملية منفصلة (core-scheduler بـ docker-compose) تُشغّل
الجامع دوريًا كل 30 دقيقة حسب معيار المرحلة 2 بالدليل.

حاليًا: هيكل تشغيل فقط (bootstrap). الربط الفعلي بجدول `sources` النشطة
وحفظ الوظائف الجديدة عبر منطق core.discovery يُستكمل بعد اعتماد أول دفعة
شركات من Source Curator (لا فائدة من تشغيل الجامع بدون مصادر حقيقية معتمدة).
"""
from __future__ import annotations

import logging

from apscheduler.schedulers.blocking import BlockingScheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("masar-scheduler")


def run_collector_round() -> None:
    logger.info(
        "جولة جامع مجدولة بدأت — بانتظار اعتماد أول دفعة مصادر (جدول sources) "
        "من جلسة Source Curator قبل تفعيل الجمع الفعلي."
    )
    # TODO (المرحلة 2):
    #   1. قراءة الصفوف النشطة (enabled=true) من جدول sources.
    #   2. لكل مصدر: استدعاء core.app.collectors.<source_type>.fetch_jobs(...)
    #   3. تطبيع كل وظيفة عبر normalizer.dedup_key وحفظ الجديد فقط في جدول jobs.
    #   4. تحديث metrics_hourly (avg_per_day, last_count) لكل مصدر.
    #   5. تنبيه عند last_count=0 لثلاث دورات متتالية أو 3 أخطاء متتالية (تعطيل المصدر).


def main() -> None:
    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(run_collector_round, "interval", minutes=30, id="collector_round")
    logger.info("Masar Core Scheduler بدأ التشغيل — الجامع كل 30 دقيقة.")
    scheduler.start()


if __name__ == "__main__":
    main()
