#!/usr/bin/env bash
# backup.sh — نسخة يومية مشفّرة من قاعدة بيانات Postgres (masar وn8n)، بالإضافة
# لملف إعدادات n8n.
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

# B9/A4: نسخة ملفات السيرة الذاتية المولّدة (core/app/cv_builder.py،
# CV_DATA_DIR=/data/cv داخل حاوية core، volume مُسمّى cv_data مشترك مع
# core-scheduler — راجع docker-compose.yml). غيابها من النسخ الاحتياطية
# يعني فقدانها الفعلي عند أي كارثة تمسح الـvolume رغم بقاء سجلّها بقاعدة
# البيانات. غير قاتلة عمدًا: فشل نسخ CV لا يجب أن يوقف باقي السكربت (نسخة
# قاعدة البيانات أهمّ بكثير وتسبقها هنا أصلًا).
CV_TAR_FILE="$BACKUP_DIR/cv_${TS}.tar.gz"
CV_TAR_ENC_FILE="${CV_TAR_FILE}.gpg"
if docker compose exec -T core tar -C /data -czf - cv > "$CV_TAR_FILE" 2>/dev/null; then
  if gpg --batch --yes --passphrase "$BACKUP_PASSPHRASE" --symmetric --cipher-algo AES256 -o "$CV_TAR_ENC_FILE" "$CV_TAR_FILE"; then
    rm -f "$CV_TAR_FILE"
    echo "$(date -u +%FT%TZ) — نسخة السيرة الذاتية مشفّرة: $CV_TAR_ENC_FILE"
  else
    echo "cv backup failed"
  fi
else
  rm -f "$CV_TAR_FILE"
  echo "cv backup failed"
fi

# نسخة قاعدة بيانات n8n — بأمر منفصل مع "|| true" حتى لا يوقف السكربت لو
# n8n لم يُنشر بعد على هذا الخادم (خدمة اختيارية إضافية)
N8N_DUMP_FILE="$BACKUP_DIR/n8n_${TS}.sql"
N8N_ENC_FILE="${N8N_DUMP_FILE}.gpg"
if docker compose exec -T postgres pg_dump -U "${POSTGRES_USER:-masar}" n8n > "$N8N_DUMP_FILE" 2>/dev/null; then
  gpg --batch --yes --passphrase "$BACKUP_PASSPHRASE" --symmetric --cipher-algo AES256 -o "$N8N_ENC_FILE" "$N8N_DUMP_FILE"
  rm -f "$N8N_DUMP_FILE"
else
  rm -f "$N8N_DUMP_FILE"
  echo "$(date -u +%FT%TZ) — تنبيه: تعذّر نسخ قاعدة بيانات n8n (ربما لم تُنشر بعد) — تخطّي."
fi

# نسخة ملف إعدادات n8n (config) من داخل الـ volume — أيضًا اختيارية
N8N_CONFIG_FILE="$BACKUP_DIR/n8n_config_${TS}.json"
N8N_CONFIG_ENC_FILE="${N8N_CONFIG_FILE}.gpg"
if docker compose exec -T n8n sh -c 'cat /home/node/.n8n/config' > "$N8N_CONFIG_FILE" 2>/dev/null && [ -s "$N8N_CONFIG_FILE" ]; then
  gpg --batch --yes --passphrase "$BACKUP_PASSPHRASE" --symmetric --cipher-algo AES256 -o "$N8N_CONFIG_ENC_FILE" "$N8N_CONFIG_FILE"
  rm -f "$N8N_CONFIG_FILE"
else
  rm -f "$N8N_CONFIG_FILE"
  echo "$(date -u +%FT%TZ) — تنبيه: تعذّر نسخ ملف إعدادات n8n — تخطّي."
fi

# حذف النسخ الأقدم من KEEP_DAYS يوم (masar، cv، n8n، وملف إعدادات n8n)
find "$BACKUP_DIR" -name "masar_*.sql.gpg" -mtime "+${KEEP_DAYS}" -delete
find "$BACKUP_DIR" -name "cv_*.tar.gz.gpg" -mtime "+${KEEP_DAYS}" -delete
find "$BACKUP_DIR" -name "n8n_*.sql.gpg" -mtime "+${KEEP_DAYS}" -delete
find "$BACKUP_DIR" -name "n8n_config_*.json.gpg" -mtime "+${KEEP_DAYS}" -delete

echo "$(date -u +%FT%TZ) — نسخة احتياطية مشفّرة: $ENC_FILE"

# B11.7: نسخ خارج الخادم (offsite) — نسخة الخادم المحلية وحدها لا تنجو من
# كارثة تمسح الخادم نفسه (VPS مفقود/تلف قرص). BACKUP_REMOTE (من .env، مسار
# rclone جاهز مسبقًا مثل "storagebox:masar") + وجود أمر rclone فعليًا كلاهما
# شرط؛ غياب أيّهما → "offsite skipped" بصمت بلا فشل السكربت (النسخة المحلية
# المشفّرة أعلاه اكتملت بالفعل بغضّ النظر عن هذا المتغيّر — هذا فقط طبقة حماية إضافية).
if [ -n "${BACKUP_REMOTE:-}" ] && command -v rclone >/dev/null 2>&1; then
  if rclone copy "$BACKUP_DIR" "$BACKUP_REMOTE" --include "*.gpg" --min-age 0s; then
    echo "$(date -u +%FT%TZ) — نسخ خارجي (offsite) اكتمل إلى: $BACKUP_REMOTE"
  else
    echo "$(date -u +%FT%TZ) — تنبيه: فشل النسخ الخارجي (offsite) إلى $BACKUP_REMOTE — النسخة المحلية سليمة رغم ذلك"
  fi
else
  echo "$(date -u +%FT%TZ) — offsite skipped"
fi
