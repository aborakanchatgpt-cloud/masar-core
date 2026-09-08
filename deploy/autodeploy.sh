#!/usr/bin/env bash
# autodeploy.sh — يسحب آخر تحديث من GitHub، يعيد بناء/تشغيل الحزمة، ويطبّق
# ترحيلات قاعدة البيانات تلقائيًا كلما تغيّر أي شيء. نموذج "سحب" بالكامل
# (الخادم يسحب من GitHub بنفسه) — لا SSH ولا GitHub Actions تصل للخادم إطلاقًا.
#
# مصمم يشتغل عبر cron كل دقيقتين (يُثبّت تلقائيًا بواسطة bootstrap.sh؛ يحقق
# معيار "أي commit يظهر على الخادم خلال 5 دقائق" بهامش أمان مريح).
#
# B1b: (أ) يولّد MCP_BRIDGE_TOKEN مرة واحدة في .env (idempotent) — جسر MCP
# (core/app/mcp_bridge.py) معطّل (503) طالما لم يُعرّف هذا المتغيّر. (ب) يُعيد
# البناء أيضًا عند وجود علامة ops/.deploy_needed — يضعها أمر "commit" في
# run_queue.sh بعد push ناجح من المضيف نفسه (حالة يكون فيها git fetch/reset
# أدناه بلا أثر لأن HEAD يساوي origin/main أصلاً، فلا يوجد فرق BEFORE/AFTER
# ليُشغّل النشر لولا العلامة.
#
# B4: (ج) يولّد MAIL_FERNET_KEY مرة واحدة في .env (idempotent، نفس نمط
# MCP_BRIDGE_TOKEN تمامًا) — مفتاح Fernet صالح (32 بايت عشوائي بترميز
# base64 آمن للروابط) يُستخدم لتشفير كلمات مرور تطبيق Gmail بجدول
# mail_links (core/app/mail_crypto.py). `openssl rand -base64 32` ينتج نفس
# طول/بنية `Fernet.generate_key()` (32 بايت خام مُرمّزة base64)؛ `tr '+/' '-_'`
# يحوّل الأبجدية القياسية base64 لأبجدية base64 الآمنة للروابط التي يشترطها Fernet
# تحديدًا — لا حاجة لتثبيت حزمة cryptography على المضيف نفسه لتوليد المفتاح.
#
# --- HARDENING ---
# لا قفل عام كان موجودًا سابقًا: تكة cron جديدة (كل دقيقتين) يمكن أن تبدأ
# بينما "docker compose up -d --build" من التكة السابقة ما زال يعمل (بناء
# صورة قد يستغرق دقائق)، فتتشابك عمليتا نشر متزامنتين على نفس الحاوية —
# نتائج غير متوقعة وربما تضارب مع طابور ops. الإصلاحات:
#   1) قفل عام flock حول جسم السكربت بالكامل — تكة متزامنة تخرج فورًا (0).
#   2) "docker compose up -d --build" و"alembic upgrade head" محاطان الآن
#      بـ timeout -k 10 (1500s و600s على التوالي)؛ عند فشل/انتهاء المهلة
#      نسجّل بوضوح ونُكمل لتشغيل طابور ops بدل التعليق، ونُبقي علامة
#      ops/.deploy_needed (لا نحذفها) لتُعاد المحاولة في التكة التالية —
#      كانت تُحذف فورًا حتى لو فشل النشر لاحقًا فتُفقد نهائيًا.
#   3) منطق إعادة إنشاء Caddyfile واستدعاءا run_queue.sh (قبل/بعد) كما هما.
set -euo pipefail

# قابل للتجاوز محليًا (اختبارات/محاكاة) عبر MASAR_APP_DIR.
APP_DIR="${MASAR_APP_DIR:-/opt/masar-core}"
LOCK_FILE="${MASAR_DEPLOY_LOCK:-/var/lock/masar-autodeploy.lock}"

cd "$APP_DIR"

mkdir -p "$(dirname "$LOCK_FILE")" 2>/dev/null || true

# --- (1) قفل عام: لا تعمل نسختان من autodeploy.sh في آن واحد ---
exec 8>"$LOCK_FILE"
if ! flock -n 8; then
  # نشر آخر يعمل الآن (على الأرجح "docker compose up -d --build" لم ينته
  # بعد) — نخرج بصمت (0) بدل التزاحم على نفس الحاوية/الشجرة.
  exit 0
fi

# توليد MCP_BRIDGE_TOKEN مرة واحدة فقط — idempotent، لا يُستبدل إن كان موجودًا.
if ! grep -q '^MCP_BRIDGE_TOKEN=' "$APP_DIR/.env" 2>/dev/null; then
  echo "MCP_BRIDGE_TOKEN=$(openssl rand -hex 32)" >>"$APP_DIR/.env"
fi

# B4: توليد MAIL_FERNET_KEY مرة واحدة فقط — idempotent، لا يُستبدل إن كان
# موجودًا (استبداله يُفقد القدرة على فك تشفير أي كلمة مرور تطبيق مخزّنة أصلاً).
if ! grep -q '^MAIL_FERNET_KEY=' "$APP_DIR/.env" 2>/dev/null; then
  echo "MAIL_FERNET_KEY=$(openssl rand -base64 32 | tr '+/' '-_')" >>"$APP_DIR/.env"
fi

# قائمة انتظار "ops" (B1a): نخدم أي أوامر مُنتظرة أولاً (قبل أي pull) حتى لا
# ينتظر طلب أُرسل للتو اكتمال دورة النشر كاملة.
bash "$APP_DIR/deploy/ops/run_queue.sh" || true

BEFORE=$(git rev-parse HEAD)
git fetch origin --quiet
git reset --hard origin/main --quiet
AFTER=$(git rev-parse HEAD)

if [ "$BEFORE" != "$AFTER" ] || [ -f "$APP_DIR/ops/.deploy_needed" ]; then
  echo "$(date -u +%FT%TZ) — تحديث جديد ($BEFORE -> $AFTER)، إعادة البناء والتشغيل..."

  deploy_ok=1

  if timeout -k 30 1500 docker compose up -d --build; then
    echo "$(date -u +%FT%TZ) — تطبيق ترحيلات قاعدة البيانات (alembic upgrade head)..."
    if ! timeout -k 10 600 docker compose run --rm -v "$APP_DIR/migrations:/migrations" core sh -c "cd /migrations && alembic upgrade head"; then
      echo "$(date -u +%FT%TZ) — فشل/انتهت مهلة alembic upgrade head — سنُبقي علامة .deploy_needed لإعادة المحاولة في التكة القادمة، ونُكمل لتشغيل طابور ops." >&2
      deploy_ok=0
    fi
  else
    echo "$(date -u +%FT%TZ) — فشل/انتهت مهلة docker compose up -d --build — سنُبقي علامة .deploy_needed لإعادة المحاولة في التكة القادمة، ونُكمل لتشغيل طابور ops." >&2
    deploy_ok=0
  fi

  # Caddyfile مربوط كملف واحد (bind mount): بعد "git reset" يصبح الملف على المضيف
  # inode جديدًا بينما الحاوية ما زالت ترى النسخة القديمة — لذلك "caddy reload"
  # داخل الحاوية لا يرى التغيير. الحل الصحيح: إعادة إنشاء حاوية Caddy عندما
  # يتغيّر Caddyfile في هذا التحديث (ثوانِ من الانقطاع فقط، والشهادات محفوظة في volume).
  # نُنفّذها فقط إذا نجح "up" أعلاه — إعادة إنشاء caddy على حزمة لم تُبنَ/تُشغَّل
  # بنجاح بلا فائدة وقد تُطيل مدة الانقطاع.
  if [ "$deploy_ok" -eq 1 ] && git diff --name-only "$BEFORE" "$AFTER" | grep -qx 'Caddyfile'; then
    echo "$(date -u +%FT%TZ) — تغيّر Caddyfile: إعادة إنشاء حاوية Caddy..."
    timeout -k 10 120 docker compose up -d --force-recreate --no-deps caddy || true
  fi

  if [ "$deploy_ok" -eq 1 ]; then
    # علامة .deploy_needed تُحذف فقط بعد نشر ناجح فعليًا — عند الفشل نُبقيها
    # كي تعيد التكة القادمة المحاولة بدل فقدان النشر المطلوب نهائيًا.
    rm -f "$APP_DIR/ops/.deploy_needed"
    echo "$(date -u +%FT%TZ) — تم النشر بنجاح."
  else
    echo "$(date -u +%FT%TZ) — النشر لم يكتمل بنجاح؛ سيُعاد المحاولة في التكة القادمة (لن يُحذف ops/.deploy_needed)." >&2
  fi

  # نخدم أي أوامر ops تراكمت أثناء النشر فورًا بدل انتظار التكة التالية —
  # سواء نجح النشر أم لا (طابور ops يجب ألا يتجمّد بسبب فشل نشر واحد).
  bash "$APP_DIR/deploy/ops/run_queue.sh" || true
else
  : # لا شي جديد — صمت تام (يمنع تضخّم اللوق)
fi
