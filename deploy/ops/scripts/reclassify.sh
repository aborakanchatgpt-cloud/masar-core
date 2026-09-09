#!/usr/bin/env bash
# deploy/ops/scripts/reclassify.sh — B2b: تشغيل إعادة تصنيف العائلة المهنية
# (core/app/reclassify.py) على الخادم عبر ops("script", ["reclassify"]).
#
# يُشغّل كعملية بايثون طازجة *داخل* حاوية core الحيّة (لا `docker compose run`
# منفصل كـbench-planner.sh — هذا السكربت يكتب على جدول jobs الإنتاجي نفسه
# عبر نفس اتصال DATABASE_URL الذي تستخدمه core، فيلزم بيئتها بالضبط لا بيئة
# جديدة)، فيبني كاشات discovery.py المعجمية (_families_cache/_family_patterns_cache)
# من الصفر بمحتوى data/taxonomy_local.yaml الحالي على القرص — يلتقط أي توسعة
# معجمي حديث دون الحاجة لإعادة تشغيل حاوية core نفسها (راجع تعليق discovery.py
# أعلى classify_family لتفصيل سبب الحاجة لهذا بالذات).
#
# مُصمّم ليكون idempotent بالكامل ومكتوب فقط على عمود jobs.family (لا حذف
# ولا إدراج) — آمن لإعادة التشغيل بأي وقت، بما فيها أثناء تشغيل core نفسه.
#
# scripts/ غير منسوخ داخل صورة core (core/Dockerfile ينسخ core/app/ فقط)،
# لكن core/app/reclassify.py *هو* داخل core/app/ فيُنسَخ فعليًا ضمن الصورة —
# لذا يُستدعى كوحدة (`python -m app.reclassify`) لا كملف مسار مباشر، خلافًا
# لـbench-planner.sh الذي يستدعي scripts/bench_planner.py عبر /repo (تركيب
# القراءة فقط الكامل للمستودع).
set -euo pipefail
cd /opt/masar-core
docker compose exec -T core python -m app.reclassify
