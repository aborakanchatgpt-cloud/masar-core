#!/usr/bin/env bash
# backup.sh — نسخة يومية مشفّرة من قاعدة بيانات Postgres.
# التشفير عبر GPG بمفتاح متماثل (passphrase) محفوظ في متغير بيئة على الخادم فقط
# (BACKUP_PASSPHRASE بملف /root/.masar-backup-env) — لا يُرفع لـ GitHub أبدًا.
#
# تثبيت الجدولة (مرة واحدة، على الخادم):
#   crontab -e
#   0 3 * * * /opt/masar-core/deploy/backup.sh >> /var/log/masar-backup.log 2>&1

set -euo pipefail

APP_DIR="/opt/masar-core"
BACKUP_DIR="/opt/masar-core-backups"
ENV_FILE="/root/.masar-backup-env"
KEEP_DAYS=14

mkdir -p "$BACKUP_DIR"
cd "$APP_DIR"

# shellcheck disable=SC1090
[ -f "$ENV_FILE" ] && source "$ENV_FILE"
if [ -z "${BACKUP_PASSPHRASE:-}" ]; then
  echo "!! BACKUP_PASSPHRASE غير معرّف بـ $ENV_FILE — لازم تنشئه أول مرة يدويًا:"
  echo "   echo 'BACKUP_PASSPHRASE=<كلمة سر قوية عشوائية>' > $ENV_FILE && chmod 600 $ENV_FILE"
  exit 1
fi

# shellcheck disable=SC2016
source .env
TS=$(date -u +%Y%m%d_%H%M%S)
DUMP_FILE="$BACKUP_DIR/masar_${TS}.sql"
ENC_FILE="${DUMP_FILE}.gpg"

docker compose exec -T postgres pg_dump -U "${POSTGRES_USER:-masar}" "${POSTGRES_DB:-masar}" > "$DUMP_FILE"
gpg --batch --yes --passphrase "$BACKUP_PASSPHRASE" --symmetric --cipher-algo AES256 -o "$ENC_FILE" "$DUMP_FILE"
rm -f "$DUMP_FILE"

# حذف النسخ الأقدم من KEEP_DAYS يوم
find "$BACKUP_DIR" -name "masar_*.sql.gpg" -mtime "+${KEEP_DAYS}" -delete

echo "$(date -u +%FT%TZ) — نسخة احتياطية مشفّرة: $ENC_FILE"
