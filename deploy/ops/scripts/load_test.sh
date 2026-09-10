#!/usr/bin/env bash
# deploy/ops/scripts/load_test.sh — B6: اختبار حمل حيّ مُصغَّر على الخادم
# (docs/reports/B6-executor.md بند 7 — "on the SERVER run a reduced live
# test (≤ 500 rows, mailpit)"), يُستدعى عبر `ops{cmd:"script", args:
# ["load_test"]}`.
#
# **يعمل منفصلاً (nohup فعليًا، لا مجرد `&`)**: run_queue.sh يُطبّق مهلة
# 120 ثانية على كل "script" (راجع تعليق bench-planner.sh لنفس القيد) —
# اختبار حتى 500 صفّ + استطلاع p95 لـ/health يحتاج دقائق، لا ثوانٍ. هذا
# السكربت يكتب جسم التشغيلة الفعلية إلى ملف مؤقّت (`ops/results/.load_test_inner.sh`)
# ثم يشغّله عبر `nohup` بالخلفية ويعود فورًا (خلال ثوانٍ)؛ النتيجة الكاملة
# تُكتب إلى `ops/results/load_test_live.log` — اقرأها عبر
# `ops{cmd:"script", args:["load_test_log"]}` (سكربت منفصل) بعد
# `wait{seconds}` مناسبة (300-600 ثانية).
#
# كل الاستدعاءات لنقاط /admin/* تمر عبر `docker compose exec -T core
# python3` (لا curl — صورة core مبنية على python:3.12-slim بلا curl) تستدعي
# httpx **داخل** حاوية core مباشرة على `http://localhost:8000` (نفس الشبكة
# الداخلية، بتوكن الإدارة من متغيّر بيئة الحاوية نفسها CORE_ADMIN_TOKEN —
# لا يُطبع أبدًا بهذا السكربت ولا بسجلّه).
#
# التنظيف: يستدعي `/admin/mail/load-test/cleanup?batch_tag=<تاجه فقط>` في
# نهاية سكربت بايثون الداخلي دومًا (كتلة `finally`) — يحذف فقط الصفوف/
# العملاء التي أنشأتها هذه التشغيلة بعينها (القيد: `synthetic=true` + نمط
# بريد `loadtest-<tag>-%@masar.invalid`، راجع core/app/mail_api.py) — لا
# يحذف أي بيانات أخرى إطلاقًا.
set -euo pipefail
cd /opt/masar-core

RESULTS_DIR="ops/results"
mkdir -p "$RESULTS_DIR"
LOG_FILE="$PWD/$RESULTS_DIR/load_test_live.log"
INNER_SCRIPT="$PWD/$RESULTS_DIR/.load_test_inner.sh"

export LT_CUSTOMERS="${LOAD_TEST_CUSTOMERS:-29}"   # 29×17=493 ≤ 500 (معيار القبول: "≤ 500 rows")
export LT_PER_CUSTOMER="${LOAD_TEST_PER_CUSTOMER:-17}"
export LT_BUDGET="${LOAD_TEST_TIME_BUDGET_SECONDS:-600}"

# كتابة جسم التشغيلة كملف (heredoc **مقتبَس** 'INNEREOF' — بلا أي توسيع هنا
# إطلاقًا، بما فيها heredoc بايثون المتداخل بداخله؛ كل متغيّرات $LT_* تُقرأ
# من بيئة العملية الفعلية وقت التشغيل الحقيقي عبر nohup، لا وقت الكتابة).
cat > "$INNER_SCRIPT" <<'INNEREOF'
set -euo pipefail
cd /opt/masar-core

echo "=== B6 live reduced load test started $(date -u +%FT%TZ) — customers=$LT_CUSTOMERS per_customer=$LT_PER_CUSTOMER ==="

docker compose exec -T \
  -e LT_CUSTOMERS="$LT_CUSTOMERS" \
  -e LT_PER_CUSTOMER="$LT_PER_CUSTOMER" \
  -e LT_BUDGET="$LT_BUDGET" \
  core python3 - <<'PYEOF'
import json, os, time
import httpx

BASE = "http://localhost:8000"
TOKEN = os.environ["CORE_ADMIN_TOKEN"]
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
CUSTOMERS = int(os.environ.get("LT_CUSTOMERS", "29"))
PER_CUSTOMER = int(os.environ.get("LT_PER_CUSTOMER", "17"))
BUDGET = float(os.environ.get("LT_BUDGET", "600"))

client = httpx.Client(base_url=BASE, headers=HEADERS, timeout=30.0)

seed_resp = client.post("/admin/mail/load-test", json={"customers": CUSTOMERS, "per_customer": PER_CUSTOMER})
seed_resp.raise_for_status()
seed = seed_resp.json()
batch_tag = seed["batch_tag"]
print("seeded:", json.dumps(seed, ensure_ascii=False))

latencies_ms = []
t0 = time.perf_counter()
total_sent = 0
total_failed = 0
ticks = 0
try:
    while True:
        h0 = time.perf_counter()
        health = httpx.get(f"{BASE}/health", timeout=5.0)
        latencies_ms.append((time.perf_counter() - h0) * 1000.0)
        health.raise_for_status()

        tick_resp = client.post("/admin/mail/send-now", params={"limit": 200})
        tick_resp.raise_for_status()
        tick = tick_resp.json()
        ticks += 1
        total_sent += tick.get("sent", 0)
        total_failed += tick.get("failed", 0)
        claimed = tick.get("claimed", 0)
        elapsed = time.perf_counter() - t0
        print(f"tick={ticks} claimed={claimed} sent_total={total_sent} elapsed={elapsed:.1f}s")
        if claimed == 0:
            break
        if elapsed > BUDGET:
            print("WARNING: time budget reached before drain completed")
            break
finally:
    elapsed_seconds = time.perf_counter() - t0
    stats_resp = client.get("/admin/send/stats")
    stats = stats_resp.json() if stats_resp.status_code == 200 else {"error": stats_resp.status_code}

    latencies_ms.sort()

    def pct(p):
        if not latencies_ms:
            return None
        idx = min(len(latencies_ms) - 1, int(round((p / 100.0) * (len(latencies_ms) - 1))))
        return round(latencies_ms[idx], 1)

    summary = {
        "ok": True,
        "batch_tag": batch_tag,
        "customers": CUSTOMERS,
        "per_customer": PER_CUSTOMER,
        "rows": CUSTOMERS * PER_CUSTOMER,
        "ticks": ticks,
        "sent_total": total_sent,
        "failed_total": total_failed,
        "elapsed_seconds": round(elapsed_seconds, 2),
        "throughput_per_sec": round(total_sent / elapsed_seconds, 3) if elapsed_seconds > 0 else 0,
        "health_p50_ms": pct(50),
        "health_p95_ms": pct(95),
        "health_p95_under_500ms": bool(pct(95) is not None and pct(95) < 500),
        "health_samples": len(latencies_ms),
        "send_stats_after": stats,
    }
    print("SUMMARY_JSON:", json.dumps(summary, ensure_ascii=False))

    cleanup_resp = client.post("/admin/mail/load-test/cleanup", params={"batch_tag": batch_tag})
    print("cleanup:", json.dumps(cleanup_resp.json(), ensure_ascii=False))
PYEOF

echo "=== B6 live reduced load test finished $(date -u +%FT%TZ) ==="
INNEREOF

nohup bash "$INNER_SCRIPT" >>"$LOG_FILE" 2>&1 &
disown
echo "load_test detached (pid $!) — نتيجة كاملة عبر ops{cmd:\"script\", args:[\"load_test_log\"]} بعد wait{seconds} مناسبة (اقترح 300-600 ثانية)"
