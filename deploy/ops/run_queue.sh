#!/usr/bin/env bash
# run_queue.sh — منفّذ قائمة انتظار "ops" (B1a): يقرأ طلبات تنفيذ من
# $APP_DIR/ops/queue (يكتبها core عبر POST /admin/ops)، ينفّذ فقط الأوامر
# المسموح بها بدقّة أدناه، ويكتب النتيجة في $APP_DIR/ops/results (يقرأها
# core عبر GET /admin/ops/{id}). لا SSH ولا Docker socket — فقط ملفات
# مشتركة بين الحاوية والمضيف عبر bind mount (انظر docker-compose.yml).
#
# يُستدعى من deploy/autodeploy.sh على كل تكة cron (كل دقيقتين تقريبًا)،
# مرّتين: قبل الـ pull وبعد اكتمال أي نشر، حتى تُخدَّم الأوامر المنتظرة
# بسرعة معقولة سواء وُجد نشر جديد أم لا.
#
# عمدًا بدون set -e: فشل معالجة طلب واحد (JSON تالف، أمر غير مدعوم، خطأ
# تنفيذ) يجب ألا يوقف معالجة بقية الطلبات في القائمة.
set -uo pipefail

APP_DIR="/opt/masar-core"
OPS_DIR="$APP_DIR/ops"
Q="$OPS_DIR/queue"
R="$OPS_DIR/results"

mkdir -p "$Q" "$R"
# الحاوية core قد تعمل بمستخدم غير root — نسمح لها بالقراءة/الكتابة هنا
chmod 1777 "$OPS_DIR" "$Q" "$R" 2>/dev/null || true

cd "$APP_DIR" || exit 0

ID_RE='^[A-Za-z0-9_-]{6,64}$'
SERVICE_RE='^(postgres|gotenberg|core|core-scheduler|caddy|n8n|n8n-db-init)$'
SCRIPT_NAME_RE='^[a-z0-9_-]+$'

shopt -s nullglob
mapfile -t sorted_files < <(ls -1tr "$Q"/*.json 2>/dev/null)

if [ "${#sorted_files[@]}" -eq 0 ]; then
  exit 0
fi

for f in "${sorted_files[@]}"; do
  [ -f "$f" ] || continue

  # تحليل JSON عبر python3 فقط — لا نبني/نحلل JSON يدويًا بالـ bash إطلاقًا.
  parsed=$(python3 - "$f" <<'PYEOF'
import json
import re
import sys

path = sys.argv[1]
try:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    job_id = str(data.get("id", ""))
    cmd = str(data.get("cmd", ""))
    args = data.get("args", [])
    if not isinstance(args, list):
        args = []
    args = [str(a) for a in args]
    if not re.match(r'^[A-Za-z0-9_-]{6,64}$', job_id):
        print("INVALID")
    else:
        print("OK")
        print(job_id)
        print(cmd)
        print(json.dumps(args))
except Exception:
    print("INVALID")
PYEOF
)

  status_line=$(printf '%s\n' "$parsed" | sed -n '1p')
  if [ "$status_line" != "OK" ]; then
    rm -f "$f"
    continue
  fi

  job_id=$(printf '%s\n' "$parsed" | sed -n '2p')
  cmd=$(printf '%s\n' "$parsed" | sed -n '3p')
  args_json=$(printf '%s\n' "$parsed" | sed -n '4p')

  # تحقق دفاعي إضافي (الـ id تحقّق منه بايثون أعلاه بالفعل)
  if ! [[ "$job_id" =~ $ID_RE ]]; then
    rm -f "$f"
    continue
  fi

  running="$Q/${job_id}.running"
  mv -f "$f" "$running" 2>/dev/null || continue

  echo "$(date -u +%FT%TZ) — ops: يعالج $job_id ($cmd)"

  started_at=$(date -u +%FT%TZ)
  out_tmp=$(mktemp)

  # استخرج args كمصفوفة bash بأمان (مفصولة بـ NUL حتى تحتمل أسطرًا جديدة
  # داخل عنصر واحد، مثل نص SQL بأمر psql).
  mapfile -d '' -t args < <(python3 -c '
import json
import sys

for a in json.loads(sys.argv[1]):
    sys.stdout.write(a)
    sys.stdout.write("\0")
' "$args_json")

  exit_code=0

  case "$cmd" in
    ps)
      timeout 120 docker compose ps >"$out_tmp" 2>&1
      exit_code=$?
      ;;

    logs)
      service="${args[0]:-}"
      lines="${args[1]:-100}"
      if [[ "$service" =~ $SERVICE_RE ]] && [[ "$lines" =~ ^[0-9]+$ ]] && [ "$lines" -ge 1 ] && [ "$lines" -le 500 ]; then
        timeout 120 docker compose logs --no-color --tail "$lines" "$service" >"$out_tmp" 2>&1
        exit_code=$?
      else
        echo "unknown or invalid command" >"$out_tmp"
        exit_code=2
      fi
      ;;

    restart)
      service="${args[0]:-}"
      if [[ "$service" =~ $SERVICE_RE ]]; then
        timeout 120 docker compose restart "$service" >"$out_tmp" 2>&1
        exit_code=$?
      else
        echo "unknown or invalid command" >"$out_tmp"
        exit_code=2
      fi
      ;;

    up)
      timeout 120 docker compose up -d --build >"$out_tmp" 2>&1
      exit_code=$?
      ;;

    caddy-reload)
      timeout 120 docker compose exec -T caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile >"$out_tmp" 2>&1
      exit_code=$?
      ;;

    deploy-log)
      lines="${args[0]:-100}"
      if [[ "$lines" =~ ^[0-9]+$ ]] && [ "$lines" -ge 1 ] && [ "$lines" -le 500 ]; then
        timeout 120 tail -n "$lines" /var/log/masar-autodeploy.log >"$out_tmp" 2>&1
        exit_code=$?
      else
        echo "unknown or invalid command" >"$out_tmp"
        exit_code=2
      fi
      ;;

    backup-log)
      lines="${args[0]:-100}"
      if [[ "$lines" =~ ^[0-9]+$ ]] && [ "$lines" -ge 1 ] && [ "$lines" -le 500 ]; then
        timeout 120 tail -n "$lines" /var/log/masar-backup.log >"$out_tmp" 2>&1
        exit_code=$?
      else
        echo "unknown or invalid command" >"$out_tmp"
        exit_code=2
      fi
      ;;

    sys)
      timeout 120 bash -c 'uptime; echo; free -m; echo; df -h /; echo; docker system df' >"$out_tmp" 2>&1
      exit_code=$?
      ;;

    env-keys)
      timeout 120 grep -o '^[A-Za-z0-9_]*=' "$APP_DIR/.env" >"$out_tmp" 2>&1
      exit_code=$?
      ;;

    migrate)
      timeout 120 docker compose run --rm -v "$APP_DIR/migrations:/migrations" core sh -c "cd /migrations && alembic upgrade head" >"$out_tmp" 2>&1
      exit_code=$?
      ;;

    backup-now)
      timeout 120 bash "$APP_DIR/deploy/backup.sh" >"$out_tmp" 2>&1
      exit_code=$?
      ;;

    git)
      timeout 120 bash -c 'git log --oneline -n 10 && git status --short' >"$out_tmp" 2>&1
      exit_code=$?
      ;;

    script)
      name="${args[0]:-}"
      if [[ "$name" =~ $SCRIPT_NAME_RE ]] && [ -f "$APP_DIR/deploy/ops/scripts/${name}.sh" ]; then
        timeout 120 bash "$APP_DIR/deploy/ops/scripts/${name}.sh" >"$out_tmp" 2>&1
        exit_code=$?
      else
        echo "unknown or invalid command" >"$out_tmp"
        exit_code=2
      fi
      ;;

    psql)
      sql="${args[0]:-}"
      if SQL="$sql" python3 <<'PYEOF'
import os
import re
import sys

s = os.environ.get("SQL", "").strip()
if not re.match(r"(?i)^(select|explain|show|\\d)", s):
    sys.exit(1)
body = s[:-1] if s.endswith(";") else s
sys.exit(1 if ";" in body else 0)
PYEOF
      then
        timeout 120 bash -c '
          set -a
          # shellcheck disable=SC1091
          source "$1/.env"
          set +a
          exec docker compose exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$2"
        ' _ "$APP_DIR" "$sql" >"$out_tmp" 2>&1
        exit_code=$?
      else
        echo "unknown or invalid command" >"$out_tmp"
        exit_code=2
      fi
      ;;

    *)
      echo "unknown or invalid command" >"$out_tmp"
      exit_code=2
      ;;
  esac

  finished_at=$(date -u +%FT%TZ)

  python3 - "$R/${job_id}.json" "$job_id" "$cmd" "$args_json" "$exit_code" "$started_at" "$finished_at" "$out_tmp" <<'PYEOF'
import json
import os
import sys

result_path, job_id, cmd, args_json, exit_code, started_at, finished_at, out_path = sys.argv[1:9]

args = json.loads(args_json)

with open(out_path, "rb") as fh:
    data = fh.read()
if len(data) > 30000:
    data = data[-30000:]
output = data.decode("utf-8", errors="replace")

result = {
    "id": job_id,
    "cmd": cmd,
    "args": args,
    "exit_code": int(exit_code),
    "started_at": started_at,
    "finished_at": finished_at,
    "output": output,
}

tmp_path = result_path + ".tmp"
with open(tmp_path, "w", encoding="utf-8") as fh:
    json.dump(result, fh, ensure_ascii=False)
os.replace(tmp_path, result_path)
PYEOF

  rm -f "$out_tmp" "$running"
done
