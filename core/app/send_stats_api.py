"""
Masar Core — مراقبة تصليب الحمل (B6، docs/reports/B6-executor.md بند 4:
"/admin/stats monitoring... sent/min, queue depth, oldest queued age,
failures by class, mailbox rate-limit hits").

نقطة جديدة `GET /admin/send/stats` (لا تصطدم بـ`GET /admin/stats` الحالية
لـcore/app/discovery_api.py — تلك لمقاييس الاكتشاف/الجامع، هذه لمقاييس
الإرسال/الوارد فقط) تجمع:
    - مقاييس قاعدة البيانات (sender.get_queue_stats): عمق الطابور بكل حالة،
      عمر أقدم صفّ مستحق، معدّل الإرسال بآخر 5 دقائق — صحيحة عبر إعادة
      تشغيل العملية (لا تعتمد على عدّادات in-process).
    - عدّادات هذه العملية منذ آخر إقلاع (sender.get_runtime_metrics):
      إجمالي مُرسَل/فاشل هذه العملية، تصنيف الفشل، عدد مرات تحديد وتيرة
      صندوق البريد (mailbox_rate_limit_hits).
    - حالة مجمّع اتصالات قاعدة البيانات (SQLAlchemy pool) — مفيد لتشخيص
      استنزاف pool_size/max_overflow أثناء اختبار تحميل.
    - ملخّص صناديق البريد المؤجَّلة بتراجع (inbox.py) والقسم الحالي لدورة
      الوارد.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text

from app import inbox, sender
from app.auth import require_admin_token
from app.discovery import get_engine

router = APIRouter(prefix="/admin/send", tags=["send-stats"], dependencies=[Depends(require_admin_token)])


def _pool_stats() -> dict:
    engine = get_engine()
    pool = engine.pool
    try:
        return {
            "size": pool.size(),
            "checked_out": pool.checkedout(),
            "overflow": pool.overflow(),
        }
    except Exception:  # noqa: BLE001 — pool قد لا يدعم كل هذه الطرق (نمط اختباري بديل)
        return {}


@router.get("/stats")
async def send_stats() -> dict:
    engine = get_engine()
    queue = sender.get_queue_stats(engine)
    runtime = sender.get_runtime_metrics()

    with engine.connect() as conn:
        mail_links_backoff = conn.execute(
            text(
                "SELECT count(*) FROM mail_links WHERE status = 'ok' AND next_check_at IS NOT NULL AND next_check_at > now()"
            )
        ).scalar()
        mail_links_active = conn.execute(
            text("SELECT count(*) FROM mail_links WHERE status = 'ok'")
        ).scalar()

    return {
        "queue": queue,
        "process_metrics": runtime,
        "db_pool": _pool_stats(),
        "inbox": {
            "partition_count": inbox.INBOX_PARTITION_COUNT,
            "current_partition": inbox.current_partition(inbox.INBOX_PARTITION_COUNT),
            "mailboxes_active": int(mail_links_active or 0),
            "mailboxes_in_backoff": int(mail_links_backoff or 0),
        },
        "config": {
            "send_workers": sender.SEND_WORKERS,
            "mailbox_min_interval_seconds": sender.MAILBOX_MIN_INTERVAL_SECONDS,
            "global_rate_per_second": sender.GLOBAL_RATE_PER_SECOND,
            "max_attempts": sender.MAX_ATTEMPTS,
        },
    }
