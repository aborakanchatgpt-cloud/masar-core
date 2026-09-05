#!/usr/bin/env bash
# bootstrap.sh — يُشغَّل مرة واحدة فقط على خادم Ubuntu 24.04 جديد (Hetzner CX22)
# يجهّز الخادم بالكامل تلقائيًا من الصفر: Docker، جدار الحماية، استنساخ
# المستودع، تشغيل الحزمة، تطبيق الترحيلات، وجدولة autodeploy.sh (نموذج
# السحب كل دقيقتين) وbackup.sh (نسخة يومية مشفّرة) — بدون أي خطوة يدوية
# إضافية بخلاف تعديل MASAR_DOMAIN بملف .env (يحتاج DNS فعليًا يشير للخادم).
#
# الاستخدام (كمستخدم root، بعد استنساخ المستودع بالتوكن يدويًا مرة واحدة):
#   cd /opt/masar-core && bash deploy/bootstrap.sh

set -euo pipefail

REPO_URL="${MASAR_REPO_URL:-git@github.com:CHANGE_ME/masar-core.git}"
APP_DIR="/opt/masar-core"
BACKUP_ENV_FILE="/root/.masar-backup-env"

echo "==> [1/8] تحديث النظام وتثبيت الأدوات الأساسية"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get upgrade -y
apt-get install -y ca-certificates curl gnupg git ufw unattended-upgrades gnupg2

echo "==> [2/8] تثبيت Docker Engine + Compose plugin (الطريقة الرسمية)"
if ! command -v docker &>/dev/null; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo \
    "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
    $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | tee /etc/apt/sources.list.d/docker.list >/dev/null
  apt-get update -y
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
systemctl enable --now docker

echo "==> [3/8] جدار الحماية (UFW) — نفتح فقط SSH وHTTP/HTTPS"
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

echo "==> [4/8] تفعيل التحديثات الأمنية التلقائية"
dpkg-reconfigure -f noninteractive unattended-upgrades || true

echo "==> [5/8] استنساخ مستودع masar-core (أو تخطي إن كان موجودًا مسبقًا)"
if [ ! -d "$APP_DIR/.git" ]; then
  git clone "$REPO_URL" "$APP_DIR"
fi
cd "$APP_DIR"

if [ ! -f .env ]; then
  cp .env.example .env
  # كلمة مرور Postgres + توكن أدمن Core عشوائيان قويان — يُولَّدان مرة واحدة فقط
  RANDOM_PW=$(openssl rand -hex 24)
  ADMIN_TOKEN=$(openssl rand -hex 32)
  sed -i "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=${RANDOM_PW}/" .env
  echo "CORE_ADMIN_TOKEN=${ADMIN_TOKEN}" >> .env
fi
# لا يوجد دومين بعد: نستخدم <IP>.sslip.io (يحلّ تلقائيًا إلى عنوان الخادم) حتى يصدر Caddy شهادة HTTPS صحيحة
if grep -q '^MASAR_DOMAIN=core.example.com' .env; then
  PUBLIC_IP=$(curl -4 -fsS https://api.ipify.org || curl -4 -fsS https://ifconfig.me)
  sed -i "s/^MASAR_DOMAIN=.*/MASAR_DOMAIN=${PUBLIC_IP}.sslip.io/" .env
fi
MASAR_DOMAIN_VALUE=$(grep '^MASAR_DOMAIN=' .env | cut -d= -f2-)
# القيمة تُقرأ من .env (سواء وُلّدت الآن أو بتشغيل سابق) لطباعتها بالنهاية
CORE_ADMIN_TOKEN_VALUE=$(grep '^CORE_ADMIN_TOKEN=' .env | cut -d= -f2-)

echo "==> [6/8] تشغيل الحزمة (docker compose up -d --build)"
docker compose up -d --build

echo "==> [7/8] تطبيق ترحيلات قاعدة البيانات (alembic upgrade head)"
sleep 5  # مهلة قصيرة لضمان جاهزية Postgres الكاملة بعد healthcheck
docker compose run --rm -v "$APP_DIR/migrations:/migrations" core sh -c "cd /migrations && alembic upgrade head"

echo "==> [8/8] جدولة autodeploy.sh (كل دقيقتين) وbackup.sh (يوميًا 03:00)"
if [ ! -f "$BACKUP_ENV_FILE" ]; then
  BACKUP_PASSPHRASE=$(openssl rand -hex 24)
  echo "BACKUP_PASSPHRASE=${BACKUP_PASSPHRASE}" > "$BACKUP_ENV_FILE"
  chmod 600 "$BACKUP_ENV_FILE"
fi
CRON_MARKER="# masar-core (يُدار تلقائيًا بواسطة bootstrap.sh)"
# ملاحظة: grep يعيد 1 عند عدم وجود سطور (crontab فارغ) — نضيف "|| true" حتى لا يوقف set -e/pipefail السكربت.
# ونستدعي السكربتات عبر bash حتى لا نعتمد على صلاحية التنفيذ (تضيع عند الرفع من واجهة GitHub).
EXISTING_CRON=$( (crontab -l 2>/dev/null || true) | grep -vF "$CRON_MARKER" | grep -v "masar-core/deploy/" || true )
{ [ -n "$EXISTING_CRON" ] && echo "$EXISTING_CRON"; \
  echo "$CRON_MARKER"; \
  echo "*/2 * * * * bash $APP_DIR/deploy/autodeploy.sh >> /var/log/masar-autodeploy.log 2>&1"; \
  echo "0 3 * * * bash $APP_DIR/deploy/backup.sh >> /var/log/masar-backup.log 2>&1"; \
} | crontab -

echo ""
echo "=================================================================="
echo "تم! ملخص الإعداد:"
echo "  الحالة:            cd $APP_DIR && docker compose ps"
echo "  سجلات Core:        docker compose logs -f core"
echo "  فحص محلي سريع:     curl -s http://localhost:8000/health"
echo "  الرابط العام (HTTPS): https://${MASAR_DOMAIN_VALUE}/health"
echo "  فحص نقطة محمية:    curl -s http://localhost:8000/admin/ping -H \"Authorization: Bearer ${CORE_ADMIN_TOKEN_VALUE}\""
echo ""
echo "  CORE_ADMIN_TOKEN (احتفظ به — يُستخدم بترويسة Authorization بكل"
echo "  استدعاء من n8n لاحقًا عبر اعتماد HTTP Header Auth باسم"
echo "  \"Masar Core Admin Token\"):"
echo "  ${CORE_ADMIN_TOKEN_VALUE}"
echo ""
echo "  autodeploy.sh مجدول كل دقيقتين، backup.sh يوميًا 03:00 — تحقق: crontab -l"
echo ""
echo "الدومين الحالي: ${MASAR_DOMAIN_VALUE} — لو اشتريت دومينًا لاحقًا عدّل MASAR_DOMAIN بـ .env ثم: docker compose up -d"
echo "=================================================================="
