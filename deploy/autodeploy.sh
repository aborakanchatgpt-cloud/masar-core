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

# n8n يحتاج N8N_ENCRYPTION_KEY — نولّده مرة واحدة فقط إن كان غائبًا عن .env
# (idempotent؛ compose نفسه يحتوي قيمة افتراضية فارغة حتى لا يفشل التشغيل
# قبل وصول هذا السطر لأول مرة).
if [ -f .env ] && ! grep -q '^N8N_ENCRYPTION_KEY=' .env; then
  if command -v openssl &>/dev/null; then
    echo "N8N_ENCRYPTION_KEY=$(openssl rand -hex 32)" >> .env
  else
    echo "N8N_ENCRYPTION_KEY=$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')" >> .env
  fi
fi

if [ "$BEFORE" != "$AFTER" ]; then
  echo "$(date -u +%FT%TZ) — تحديث جديد ($BEFORE -> $AFTER)، إعادة البناء والتشغيل..."
  docker compose up -d --build
  echo "$(date -u +%FT%TZ) — تطبيق ترحيلات قاعدة البيانات (alembic upgrade head)..."
  docker compose run --rm -v "$APP_DIR/migrations:/migrations" core sh -c "cd /migrations && alembic upgrade head"
  echo "$(date -u +%FT%TZ) — تم النشر بنجاح."
else
  : # لا شي جديد — صمت تام (يمنع تضخم اللوق)
fi
