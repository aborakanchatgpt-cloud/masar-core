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

# قائمة انتظار "ops" (B1a): نخدم أي أوامر مُنتظرة أولًا (قبل أي pull) حتى لا
# ينتظر طلب أُرسل للتو اكتمال دورة النشر كاملة.
bash "$APP_DIR/deploy/ops/run_queue.sh" || true

BEFORE=$(git rev-parse HEAD)
git fetch origin --quiet
git reset --hard origin/main --quiet
AFTER=$(git rev-parse HEAD)

if [ "$BEFORE" != "$AFTER" ]; then
  echo "$(date -u +%FT%TZ) — تحديث جديد ($BEFORE -> $AFTER)، إعادة البناء والتشغيل..."
  docker compose up -d --build
  echo "$(date -u +%FT%TZ) — تطبيق ترحيلات قاعدة البيانات (alembic upgrade head)..."
  docker compose run --rm -v "$APP_DIR/migrations:/migrations" core sh -c "cd /migrations && alembic upgrade head"
  # Caddyfile مربوط كملف واحد (bind mount): بعد "git reset" يصبح الملف على المضيف
  # inode جديدًا بينما الحاوية ما زالت ترى النسخة القديمة — لذلك "caddy reload"
  # داخل الحاوية لا يرى التغيير. الحل الصحيح: إعادة إنشاء حاوية Caddy عندما
  # يتغيّر Caddyfile في هذا التحديث (ثوانٍ من الانقطاع فقط، والشهادات محفوظة في volume).
  if git diff --name-only "$BEFORE" "$AFTER" | grep -qx 'Caddyfile'; then
    echo "$(date -u +%FT%TZ) — تغيّر Caddyfile: إعادة إنشاء حاوية Caddy..."
    docker compose up -d --force-recreate --no-deps caddy || true
  fi
  echo "$(date -u +%FT%TZ) — تم النشر بنجاح."
  # نخدم أي أوامر ops تراكمت أثناء النشر فورًا بدل انتظار التكة التالية.
  bash "$APP_DIR/deploy/ops/run_queue.sh" || true
else
  : # لا شي جديد — صمت تام (يمنع تضخم اللوق)
fi
