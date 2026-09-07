#!/usr/bin/env bash
# deploy/ops/scripts/bench-planner.sh — B3: تشغيل قياس أداء المخطِّط
# (scripts/bench_planner.py) على الخادم عبر ops("script", ["bench-planner"]).
#
# يُستدعى من deploy/ops/run_queue.sh (timeout 120 ثانية على مستوى run_queue
# نفسه — قياس محلي بهذه المهمة أظهر ~28 ثانية لـ1,500 عميل × 3,000 وظيفة،
# فهامش الأمان كبير؛ إن كبر حجم البيانات الاصطناعية مستقبلًا يجب رفع هذا
# الحد بـrun_queue.sh أيضًا وليس فقط هنا — راجع فجوة موثّقة بتقرير القبول
# docs/reports/B3-executor.md).
#
# scripts/ غير منسوخ داخل صورة core (core/Dockerfile ينسخ core/app/ فقط)،
# لكنه متاح داخل الحاوية عبر التركيب الكامل للمستودع للقراءة فقط
# (./:/repo:ro بـdocker-compose.yml) — لذلك المسار هنا /repo/scripts وليس
# scripts/ نسبيًا لمجلد عمل الحاوية (/app).
set -euo pipefail
cd /opt/masar-core
docker compose exec -T core python /repo/scripts/bench_planner.py
