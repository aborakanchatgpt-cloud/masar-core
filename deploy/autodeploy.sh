#!/usr/bin/env bash
# autodeploy.sh — يسحب آخر تحديث من GitHub، يعيد بناء/تشغيل الحزمة، ويطبّق
# ترحيلات قاعدة البيانات تلقائيًا كلما تغيّر أي شيء. نموذج "سحب" بالكامل
# (الخادم يسحب من GitHub بنفسه) — لا SSH ولا GitHub Actions تصل للخادم إطلاقًا.
#
# مصمم يشتغل عبر cron كل دقيقتين (يُثبّت تلقائيًا بواسطة bootstrap.sh؛ يحقق
# معيار "أي commit يظهر على الخادم خلال 5 دقائق" بهامش أمان مريح).

set -euo pipefail

APP_DIR="/opt/masar-core"
cd "$APP_DIR"

BEFORE=$(git rev-parse HEAD)
git fetch origin --quiet
git reset --hard origin/main --quiet
AFTER=$(git rev-parse HEAD)

if [ "$BEFORE" != "$AFTER" ]; then
  echo "$(date -u +%FT%TZ) — تحديث جديد ($BEFORE -> $AFTER)، إعادة البناء والتشغيل..."
  docker compose up -d --build
  echo "$(date -u +%FT%TZ) — تطبيق ترحيلات قاعدة البيانات (alembic upgrade head)..."
  docker compose run --rm -v "$APP_DIR/migrations:/migrations" core sh -c "cd /migrations && alembic upgrade head"
  # Caddyfile مربوط كملف (bind mount) فلا يلاحظ compose تغيّره — نعيد تحميل
  # إعدادات Caddy صراحةً بعد كل نشر (idempotent)، وإن فشل نعيد تشغيل الحاوية.
  echo "$(date -u +%FT%TZ) — إعادة تحميل إعدادات Caddy..."
  docker compose exec -T caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile \
    || docker compose restart caddy || true
  echo "$(date -u +%FT%TZ) — تم النشر بنجاح."
else
  : # لا شي جديد — صمت تام (يمنع تضخم اللوق)
fi
