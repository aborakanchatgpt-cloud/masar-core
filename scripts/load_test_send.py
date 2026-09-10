#!/usr/bin/env python3
"""
scripts/load_test_send.py — B6: اختبار حمل كامل الحجم لطابور الإرسال
(docs/reports/B6-executor.md بند 7: "1,500 customers × 17 = 25,500 rows,
processed in DRY_RUN with the mailpit sink... in < 6 hours").

يُشغَّل **محليًا في صندوق الرمل** بكامل الحجم (25,500 صفّ افتراضيًا) — لا
على الخادم (هناك يُستخدَم `deploy/ops/scripts/load_test.sh` بحجم مُصغَّر
عبر نقطة `/admin/mail/load-test` الموجودة أصلًا، راجع تعليقها هناك).

ماذا يفعل بالضبط:
    1. يشغّل sink SMTP محلي (aiosmtpd، يستقبل ويُهمِل — بديل مكافئ لـmailpit
       بلا الحاجة لـDocker بصندوق الرمل) على منفذ محلي.
    2. يشغّل **عملية uvicorn فعلية منفصلة** (app.main:app، تمامًا كحاوية
       core بالإنتاج) — هي "الـAPI" الذي يجب ألا "يتضوّر جوعًا" (starve)
       أثناء الحمل؛ خيط مستقل يستطلع `GET /health` كل 200ms طوال التشغيلة
       ويحسب p95 لزمن الاستجابة.
    3. يبذر 1,500 عميل اختبار (status='paused'، بريد `loadtest-<tag>-N@masar.invalid`
       — نفس اصطلاح `POST /admin/mail/load-test` بـcore/app/mail_api.py
       تمامًا) و25,500 صفّ send_queue (`synthetic=true`) — **بترتيب إدراج
       متشابك بين العملاء** (دورة عبر كل عميل قبل الصفّ التالي لكل واحد)
       عمدًا: الترتيب الطبيعي بـ`/admin/mail/load-test` (كل صفوف عميل تلو
       الآخر) يُنتج تكتّلًا غير واقعي (17 صفًّا متتاليًا لنفس العميل قد
       تُطالَب بها دفعة واحدة) يصطدم بحدّ SEND_MAILBOX_MIN_INTERVAL_SECONDS
       الجديد (B6) بطريقة لا تحدث أبدًا بالإنتاج الحقيقي (send_builder.py
       يباعد صفوف نفس العميل 8-15 دقيقة دومًا وقت البناء — لا يمكن لأكثر
       من صفّ واحد لنفس العميل أن يصبح مستحقًّا بنفس اللحظة تمامًا في مسار
       الإنتاج الطبيعي). التشابك هنا يُحاكي "طابور متراكم بعد توقّف" بواقعية:
       آلاف العملاء المختلفين مستحقّون معًا، لا عميل واحد بعشرات الصفوف.
    4. يستدعي `sender.send_tick()` مباشرة (بلا HTTP، بنفس محرّك قاعدة
       البيانات المضبوط بنفس متغيرات البيئة التي يستخدمها core-scheduler
       فعليًا) تباعًا بلا توقّف حتى يفرغ الطابور (claimed=0) أو تنتهي مهلة
       الأمان (`--time-budget-seconds`) — القياس المباشر لأقصى معدّل تصريف
       ممكن؛ الإنتاج الحقيقي (تكة كل دقيقة عبر core-scheduler) لا يمكن أن
       يكون أبطأ من هذا (APScheduler بـ`coalesce=True, max_instances=1`
       يستدعي التكة التالية فور انتهاء السابقة إن تراكم طابور، لا ينتظر
       الدقيقة الكاملة).
    5. يطبع سطر JSON نهائي وحيد (آخر سطر) بالنتيجة الكاملة + امتداد لزمن
       الخادم الفعلي (Hetzner CX23، 2 vCPU — نفس عدد الأنوية هنا بصندوق
       الرمل، راجع تحذير الامتداد بالمخرجات).
    6. ينظّف بيانات الاختبار (send_queue + customers الاصطناعيين فقط،
       بنفس شرط `synthetic=true`/نمط البريد `@masar.invalid`) ما لم يُمرَّر
       `--keep-data`، وينهي العمليتين الفرعيتين (sink + API).

التشغيل: cd core && DATABASE_URL=... python ../scripts/load_test_send.py
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
CORE_DIR = REPO_ROOT / "core"
sys.path.insert(0, str(CORE_DIR))

from sqlalchemy import create_engine, text  # noqa: E402


def _log(msg: str) -> None:
    print(f"[load_test_send] {msg}", file=sys.stderr, flush=True)


def start_smtp_sink(port: int, log_path: pathlib.Path):
    cmd = [sys.executable, "-m", "aiosmtpd", "-n", "-l", f"localhost:{port}"]
    log_file = open(log_path, "w")
    proc = subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.STDOUT)
    return proc, log_file


def start_api(port: int, env: dict, log_path: pathlib.Path):
    cmd = [
        sys.executable, "-m", "uvicorn", "app.main:app",
        "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning",
    ]
    log_file = open(log_path, "w")
    proc = subprocess.Popen(cmd, cwd=str(CORE_DIR), env=env, stdout=log_file, stderr=subprocess.STDOUT)
    return proc, log_file


def wait_for_health(base_url: str, timeout_seconds: float = 30.0) -> bool:
    import httpx
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(f"{base_url}/health", timeout=2.0)
            if resp.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.3)
    return False


def health_poller(base_url: str, stop_event: threading.Event, latencies_ms: list, interval_seconds: float = 0.2):
    import httpx
    with httpx.Client(timeout=5.0) as client:
        while not stop_event.is_set():
            t0 = time.perf_counter()
            try:
                client.get(f"{base_url}/health")
                ok = True
            except Exception:
                ok = False
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            if ok:
                latencies_ms.append(elapsed_ms)
            time.sleep(interval_seconds)


def percentile(values: list, pct: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    idx = min(len(s) - 1, int(round((pct / 100.0) * (len(s) - 1))))
    return s[idx]


def seed(engine, n_customers: int, per_customer: int, batch_tag: str, chunk_size: int = 2000) -> list[int]:
    """يبذر عملاء اختبار + صفوف send_queue بترتيب متشابك بين العملاء
    (راجع تعليق رأس الملف، بند 3) — لا يستدعي send_builder/planner إطلاقًا
    (بنفس فلسفة نقطة `/admin/mail/load-test` الموجودة: قياس معدّل تصريف
    المُرسِل وحده، بلا منطق تخطيط/مطابقة)."""
    with engine.begin() as conn:
        customer_ids: list[int] = []
        for i in range(n_customers):
            row = conn.execute(
                text(
                    """
                    INSERT INTO customers (name, email_service, status, cities, families, target_daily)
                    VALUES (:name, :email, 'paused', '[]', '[]', :target)
                    ON CONFLICT (email_service) DO UPDATE SET name = EXCLUDED.name
                    RETURNING id
                    """
                ),
                {
                    "name": f"Load Test {batch_tag} #{i}",
                    "email": f"loadtest-{batch_tag}-{i}@masar.invalid",
                    "target": per_customer,
                },
            ).first()
            customer_ids.append(row[0])

        placeholder_pdf = pathlib.Path("/tmp/masar_b6_loadtest") / "_loadtest_placeholder.pdf"
        placeholder_pdf.parent.mkdir(parents=True, exist_ok=True)
        if not placeholder_pdf.is_file():
            placeholder_pdf.write_bytes(
                b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>"
            )

        now = datetime.now(timezone.utc)
        rows_to_insert = []
        # متشابك: دورة "الجولة n" على كل العملاء، لا كل صفوف عميل تلو الآخر
        # (راجع تعليق رأس الملف — يمنع تكتّل 17 صفًّا لنفس العميل بنفس claim).
        for n in range(per_customer):
            for cid in customer_ids:
                rows_to_insert.append(
                    {
                        "customer_id": cid,
                        "to_email": "loadtest-recipient@masar.invalid",
                        "subject": f"[load-test {batch_tag}] Application #{n}",
                        "body_text": "Load test synthetic message.",
                        "attachments": '[{"path": "%s", "filename": "CV.pdf"}]' % placeholder_pdf,
                        "send_after": now,
                    }
                )

        inserted_total = 0
        for start in range(0, len(rows_to_insert), chunk_size):
            chunk = rows_to_insert[start : start + chunk_size]
            conn.execute(
                text(
                    """
                    INSERT INTO send_queue (
                        customer_id, opportunity_id, job_id, to_email, cc_email, subject,
                        body_text, body_html, attachments, send_after, attempts, status, synthetic, created_at
                    ) VALUES (
                        :customer_id, NULL, NULL, :to_email, NULL, :subject,
                        :body_text, NULL, CAST(:attachments AS jsonb), :send_after, 0, 'queued', true, now()
                    )
                    """
                ),
                chunk,
            )
            inserted_total += len(chunk)

    _log(f"seeded {len(customer_ids)} customers, {inserted_total} send_queue rows (batch_tag={batch_tag})")
    return customer_ids


def cleanup(engine, batch_tag: str) -> dict:
    pattern = f"loadtest-{batch_tag}-%@masar.invalid"
    with engine.begin() as conn:
        deleted_queue = conn.execute(
            text(
                """
                DELETE FROM send_queue WHERE synthetic = true
                  AND customer_id IN (SELECT id FROM customers WHERE email_service LIKE :pattern)
                """
            ),
            {"pattern": pattern},
        ).rowcount
        deleted_customers = conn.execute(
            text("DELETE FROM customers WHERE status = 'paused' AND email_service LIKE :pattern"),
            {"pattern": pattern},
        ).rowcount
    return {"send_queue_deleted": deleted_queue, "customers_deleted": deleted_customers}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--customers", type=int, default=1500)
    parser.add_argument("--per-customer", type=int, default=17)
    parser.add_argument("--db-url", default=os.environ.get(
        "DATABASE_URL", "postgresql+psycopg://masar_b6:masar_b6@localhost:5432/masar_b6_test"
    ))
    parser.add_argument("--smtp-port", type=int, default=1125)
    parser.add_argument("--api-port", type=int, default=8011)
    parser.add_argument("--claim-limit", type=int, default=200, help="نفس CLAIM_BATCH_LIMIT_DEFAULT الإنتاجي")
    parser.add_argument("--time-budget-seconds", type=float, default=3600.0, help="مهلة أمان قصوى (افتراضي: ساعة واحدة محليًا — كافية جدًا لإثبات <6 ساعات)")
    parser.add_argument("--keep-data", action="store_true", help="لا تحذف بيانات الاختبار الاصطناعية بعد التشغيلة")
    parser.add_argument("--server-vcpus", type=int, default=2, help="لحساب امتداد زمن الخادم الحقيقي (Hetzner CX23=2)")
    args = parser.parse_args()

    tag = f"lt{int(time.time())}"
    db_url = args.db_url
    api_base = f"http://127.0.0.1:{args.api_port}"

    fernet_key = subprocess.run(
        [sys.executable, "-c", "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    api_env = dict(os.environ)
    api_env.update({
        "DATABASE_URL": db_url,
        "CORE_ADMIN_TOKEN": "load-test-token",
        "MAIL_FERNET_KEY": fernet_key,
        "MAIL_SINK_SMTP": f"localhost:{args.smtp_port}",
        "MAIL_LIVE": "false",
        "DB_POOL_SIZE": "10",
        "DB_MAX_OVERFLOW": "5",
        "OPS_DIR": "/tmp/masar_b6_loadtest_ops",
        "DATA_DIR": str(REPO_ROOT / "data"),
        "PYTHONPATH": str(CORE_DIR),
    })
    # عملية القياس نفسها (send_tick) تستخدم نفس متغيّرات core-scheduler
    # الإنتاجية للمحرّك (DB_POOL_SIZE أكبر لأنها تحمل SEND_WORKERS خيطًا).
    os.environ["DATABASE_URL"] = db_url
    os.environ.setdefault("MAIL_FERNET_KEY", fernet_key)
    os.environ["MAIL_SINK_SMTP"] = f"localhost:{args.smtp_port}"
    os.environ["MAIL_LIVE"] = "false"
    os.environ.setdefault("DB_POOL_SIZE", "15")
    os.environ.setdefault("DB_MAX_OVERFLOW", "15")

    tmp_dir = pathlib.Path("/tmp/masar_b6_loadtest")
    tmp_dir.mkdir(parents=True, exist_ok=True)

    _log(f"starting SMTP sink on :{args.smtp_port}")
    smtp_proc, smtp_log = start_smtp_sink(args.smtp_port, tmp_dir / "smtp_sink.log")
    time.sleep(1.0)

    _log(f"starting API (uvicorn app.main:app) on :{args.api_port}")
    api_proc, api_log = start_api(args.api_port, api_env, tmp_dir / "api.log")

    result: dict = {"ok": False}
    try:
        if not wait_for_health(api_base, timeout_seconds=30.0):
            raise RuntimeError(f"API لم يستجب على {api_base}/health خلال 30 ثانية — راجع {tmp_dir / 'api.log'}")
        _log("API healthy — بدء البذر")

        from app.discovery import get_engine as get_app_engine
        from app import sender

        engine = get_app_engine()
        # محرّك منفصل خفيف للبذر/التنظيف (بلا تعارض على نفس pool مع send_tick).
        seed_engine = create_engine(db_url.replace("postgresql://", "postgresql+psycopg://", 1) if db_url.startswith("postgresql://") else db_url)

        customer_ids = seed(seed_engine, args.customers, args.per_customer, tag)
        expected_rows = len(customer_ids) * args.per_customer

        stop_event = threading.Event()
        latencies_ms: list = []
        poller = threading.Thread(target=health_poller, args=(api_base, stop_event, latencies_ms), daemon=True)
        poller.start()

        _log(f"بدء الاستنزاف — {expected_rows} صفّ، claim_limit={args.claim_limit}")
        t0 = time.perf_counter()
        total_sent = 0
        total_failed = 0
        ticks = 0
        while True:
            tick_result = sender.send_tick(limit=args.claim_limit, ignore_window=True, engine=engine)
            ticks += 1
            total_sent += tick_result.get("sent", 0)
            total_failed += tick_result.get("failed", 0)
            claimed = tick_result.get("claimed", 0)
            elapsed = time.perf_counter() - t0
            if ticks % 20 == 0 or claimed == 0:
                _log(f"tick={ticks} claimed={claimed} sent_total={total_sent} failed_total={total_failed} elapsed={elapsed:.1f}s")
            if claimed == 0:
                break
            if elapsed > args.time_budget_seconds:
                _log("مهلة الأمان (--time-budget-seconds) بلغت — إيقاف الاستنزاف قبل اكتمال الطابور")
                break

        elapsed_seconds = time.perf_counter() - t0
        stop_event.set()
        poller.join(timeout=2.0)

        p50 = percentile(latencies_ms, 50)
        p95 = percentile(latencies_ms, 95)
        p99 = percentile(latencies_ms, 99)

        throughput_per_sec = total_sent / elapsed_seconds if elapsed_seconds > 0 else 0.0
        projected_25500_seconds = (25500 / throughput_per_sec) if throughput_per_sec > 0 else None

        # امتداد بسيط لزمن الخادم الحقيقي: نفس عدد الأنوية هنا (راجع --server-vcpus)
        # لكن الخادم الحقيقي يتشارك تلك الأنوية مع postgres+n8n+gotenberg+caddy+
        # mailpit في نفس اللحظة (هنا: postgres محلي فقط بلا تنافس) — عامل تحفّظ
        # 0.5 (نصف الطاقة الفعلية المتاحة تقديريًا) مُوثَّق صراحة، لا قياس حقيقي.
        conservatism_factor = 0.5
        projected_server_seconds = (
            projected_25500_seconds / conservatism_factor if projected_25500_seconds else None
        )

        db_queue_stats = sender.get_queue_stats(engine)

        result = {
            "ok": True,
            "batch_tag": tag,
            "customers": len(customer_ids),
            "per_customer": args.per_customer,
            "expected_rows": expected_rows,
            "ticks": ticks,
            "sent_total": total_sent,
            "failed_total": total_failed,
            "elapsed_seconds": round(elapsed_seconds, 2),
            "throughput_per_sec": round(throughput_per_sec, 3),
            "under_6_hours_local": elapsed_seconds < 6 * 3600,
            "projected_25500_seconds": round(projected_25500_seconds, 1) if projected_25500_seconds else None,
            "projected_25500_hours": round(projected_25500_seconds / 3600, 3) if projected_25500_seconds else None,
            "projected_server_hours_conservative": (
                round(projected_server_seconds / 3600, 3) if projected_server_seconds else None
            ),
            "conservatism_factor_applied": conservatism_factor,
            "api_health_p50_ms": round(p50, 1) if p50 else None,
            "api_health_p95_ms": round(p95, 1) if p95 else None,
            "api_health_p99_ms": round(p99, 1) if p99 else None,
            "api_health_samples": len(latencies_ms),
            "api_health_p95_under_500ms": bool(p95 is not None and p95 < 500),
            "db_queue_stats_after": db_queue_stats,
            "sender_runtime_metrics": sender.get_runtime_metrics(),
        }

        if not args.keep_data:
            result["cleanup"] = cleanup(seed_engine, tag)
        else:
            result["cleanup"] = {"skipped": True, "batch_tag": tag}

    finally:
        api_proc.terminate()
        smtp_proc.terminate()
        try:
            api_proc.wait(timeout=10)
        except Exception:
            api_proc.kill()
        try:
            smtp_proc.wait(timeout=10)
        except Exception:
            smtp_proc.kill()
        api_log.close()
        smtp_log.close()

    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
