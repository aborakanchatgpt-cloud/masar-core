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
#
# B1b: يضيف أوامر git/commit تسمح لجلسات Claude (عبر جسر MCP، core/app/mcp_bridge.py)
# بكتابة ملفات في المستودع وعمل commit + push دون SSH من جلسة Claude نفسها —
# فقط المضيف (هذا السكربت، بصلاحية root) يملك مفتاح النشر ويستخدمه.
set -uo pipefail

APP_DIR="/opt/masar-core"
OPS_DIR="$APP_DIR/ops"
Q="$OPS_DIR/queue"
R="$OPS_DIR/results"
S="$OPS_DIR/stage"

mkdir -p "$Q" "$R" "$S"
# الحاوية core قد تعمل بمستخدم غير root — نسمح لها بالقراءة/الكتابة هنا
chmod 1777 "$OPS_DIR" "$Q" "$R" "$S" 2>/dev/null || true

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

    # --- B1b: مفتاح النشر وأوامر git/commit (المضيف فقط يملك مفتاح SSH) ---

    deploy-key)
      # يولّد زوج مفاتيح ed25519 مرة واحدة فقط (idempotent) ويطبع المفتاح
      # العام فقط — المفتاح الخاص لا يُطبع ولا يغادر المضيف إطلاقًا. المفتاح
      # العام يُضاف يدويًا كـ Deploy Key (بصلاحية كتابة) في إعدادات مستودع
      # GitHub، ثم يُستخدم أمر git-remote-ssh لتفعيله.
      timeout 60 bash -c '
        mkdir -p /root/.ssh && chmod 700 /root/.ssh
        [ -f /root/.ssh/masar_deploy ] || ssh-keygen -t ed25519 -N "" -C "masar-core-deploy" -f /root/.ssh/masar_deploy
        cat /root/.ssh/masar_deploy.pub
      ' >"$out_tmp" 2>&1
      exit_code=$?
      ;;

    git-remote-ssh)
      # يحوّل remote origin إلى SSH بمفتاح النشر، ويثبّت هوية git للبوت.
      # git fetch في آخر السطر يتحقق فورًا من أن المفتاح يعمل فعليًا.
      timeout 60 bash -c '
        git remote set-url origin git@github.com:aborakanchatgpt-cloud/masar-core.git &&
        git config core.sshCommand "ssh -i /root/.ssh/masar_deploy -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new" &&
        git config user.name "masar-core-bot" &&
        git config user.email "masar-core-bot@users.noreply.github.com" &&
        git fetch origin --quiet &&
        echo ok
      ' >"$out_tmp" 2>&1
      exit_code=$?
      ;;

    git-remote-https)
      # تراجع (rollback) إلى HTTPS العادي إن سبّب SSH مشكلة — بلا كتابة (النشر
      # الحالي يعتمد على git pull للقراءة فقط عبر HTTPS، وهذا يبقيه يعمل).
      timeout 60 bash -c '
        git remote set-url origin https://github.com/aborakanchatgpt-cloud/masar-core.git && echo ok
      ' >"$out_tmp" 2>&1
      exit_code=$?
      ;;

    commit)
      # args: [stage_id, message]. الملفات المُرحَّلة (staged) بواسطة core عبر
      # OPS_DIR/stage/<stage_id>/ (انظر أداة repo_write في mcp_bridge.py)
      # تُنسخ إلى شجرة المستودع، ثم commit + push إلى origin/main مباشرة.
      stage_id="${args[0]:-}"
      message="${args[1]:-}"
      if ! [[ "$stage_id" =~ $ID_RE ]]; then
        echo "unknown or invalid command" >"$out_tmp"
        exit_code=2
      else
        STAGE="$S/$stage_id"
        if [ ! -d "$STAGE" ]; then
          echo "stage not found: $stage_id" >"$out_tmp"
          exit_code=2
        else
          reject=0
          rel_paths=()
          while IFS= read -r -d '' fpath; do
            rel="${fpath#"$STAGE"/}"
            case "$rel" in
              *..*|.git/*|ops/*|.env|.env.*)
                reject=1
                ;;
            esac
            rel_paths+=("$rel")
          done < <(find "$STAGE" -type f -print0)

          if [ "$reject" -eq 1 ] || [ "${#rel_paths[@]}" -eq 0 ]; then
            echo "rejected: invalid file path in stage" >"$out_tmp"
            exit_code=3
          else
            for rel in "${rel_paths[@]}"; do
              mkdir -p "$APP_DIR/$(dirname "$rel")"
              cp "$STAGE/$rel" "$APP_DIR/$rel"
            done
            for rel in "${rel_paths[@]}"; do
              git add -A -- "$rel" >>"$out_tmp" 2>&1
            done
            if git diff --cached --quiet; then
              echo "nothing to commit" >"$out_tmp"
              exit_code=0
            else
              if git -c user.name=masar-core-bot -c user.email=masar-core-bot@users.noreply.github.com commit -q -m "$message" >"$out_tmp" 2>&1; then
                if git push -q origin HEAD:main >>"$out_tmp" 2>&1; then
                  sha=$(git rev-parse HEAD)
                  echo "committed and pushed: $sha" >>"$out_tmp"
                  touch "$OPS_DIR/.deploy_needed"
                  rm -rf "$STAGE"
                  exit_code=0
                else
                  echo "push failed — commit left local; next autodeploy tick will discard it via git reset --hard origin/main" >>"$out_tmp"
                  exit_code=4
                fi
              else
                echo "git commit failed" >"$out_tmp"
                exit_code=4
              fi
            fi
          fi
        fi
      fi
      ;;

    stage-clean)
      timeout 60 bash -c '
        find "$1" -mindepth 1 -maxdepth 1 -type d -mtime +1 -exec rm -rf {} + 2>/dev/null
        echo done
      ' _ "$S" >"$out_tmp" 2>&1
      exit_code=$?
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
