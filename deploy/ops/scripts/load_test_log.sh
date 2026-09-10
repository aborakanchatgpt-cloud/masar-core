#!/usr/bin/env bash
# deploy/ops/scripts/load_test_log.sh — B6: يقرأ سجلّ اختبار الحمل الحيّ
# المُصغَّر الذي أطلقه load_test.sh بالخلفية (nohup) — يُستدعى عبر
# `ops{cmd:"script", args:["load_test_log"]}` بعد `wait{seconds}` كافية
# (300-600 ثانية) من استدعاء load_test.sh.
set -euo pipefail
cd /opt/masar-core

LOG_FILE="ops/results/load_test_live.log"

if [ ! -f "$LOG_FILE" ]; then
  echo "لا يوجد سجلّ بعد — لم يُستدعَ load_test.sh، أو لم يكتب أي سطر حتى الآن."
  exit 0
fi

echo "== آخر 200 سطر من $LOG_FILE =="
tail -n 200 "$LOG_FILE"

if grep -q "load test finished" "$LOG_FILE"; then
  echo
  echo "== الحالة: انتهت التشغيلة =="
else
  echo
  echo "== الحالة: لا تزال قيد التشغيل (لا يوجد سطر 'finished' بعد) — أعد وwait{seconds} أطول ثم استدعِ هذا السكربت مجددًا =="
fi
