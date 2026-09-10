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
# --- HARDENING (تصليب ضد "poison job" وتجمّد الطابور) ---
# لوحظ في الإنتاج أن طابور ops قد يتجمّد لأكثر من ساعة رغم أن Core/health
# سليم، وأن commits المُرسلة عبر أمر "commit" لا تصل أبدًا. السبب الأرجح:
# سلسلة git (add/commit/push) في أمر "commit" لم تكن محاطة بـ timeout
# إطلاقًا (خلافًا لبقية الأوامر)؛ تعليق TCP على "git push" عبر SSH (أو
# مهلة اتصال طويلة) يُجمّد استدعاء run_queue.sh بالكامل لتلك التكة، ومع
# غياب أي قفل عام كانت كل تكة cron لاحقة تبدأ عملية run_queue.sh جديدة
# فتتكدّس العمليات المتوازية وتتزاحم على نفس شجرة git (قفل .git/index.lock)
# — تجمّد فعلي للطابور يطابق الأعراض الملاحظة تمامًا. كما أن أي خطأ غير
# متوقع (متغيّر غير مُعرَّف تحت set -u، استثناء بايثون، ...) في معالجة طلب
# واحد كان يمكن نظريًا أن يُنهي حلقة for كاملةً فيوقف كل الطلبات اللاحقة.
#
# الإصلاحات هنا:
#   1) قفل عام flock يمنع تشغيل نسختين من هذا السكربت في آن واحد.
#   2) كل طلب يُعالَج داخل subshell مستقل بـ set +e ومصيدة EXIT تكتب
#      نتيجة exit_code=99 مع stderr الملتقط ثم تُكمل الحلقة مهما حدث.
#   3) تحليل JSON بنداء بايثون واحد فقط يطبع id/cmd/args بحقول مفصولة
#      بـ NUL؛ أي فشل تحليل أو id غير صالح → نقل الملف إلى ops/failed
#      (بدل حذفه بصمت) مع سطر لوق.
#   4) ملفات .running "عالقة" أقدم من 10 دقائق → تُنقل إلى failed/ مع
#      نتيجة exit_code=98.
#   5) كل استدعاءات timeout أصبحت timeout -k 10 ... لضمان SIGKILL فعلي؛
#      سلسلة git في أمر "commit" محاطة بالكامل بـ timeout -k 10 300 مع
#      GIT_SSH_COMMAND (ConnectTimeout=20, BatchMode=yes).
#   6) حد أقصى 25 طلبًا لكل استدعاء (البقية تُترك للتكة التالية).
#   7) سطر لوق واحد لكل طلب في /var/log/masar-ops.log (id, cmd,
#      exit_code, seconds) — بدون أي أسرار.
#
# B1b: يضيف أوامر git/commit تسمح لجلسات Claude (عبر جسر MCP، core/app/mcp_bridge.py)
# بكتابة ملفات في المستودع وعمل commit + push دون SSH من جلسة Claude نفسها —
# فقط المضيف (هذا السكربت، بصلاحية root) يملك مفتاح النشر ويستخدمه.
set -uo pipefail

# قابل للتجاوز محليًا (اختبارات/محاكاة) عبر MASAR_APP_DIR؛ الإنتاج يستخدم
# المسار الافتراضي كما كان دائمًا.
APP_DIR="${MASAR_APP_DIR:-/opt/masar-core}"
OPS_DIR="$APP_DIR/ops"
Q="$OPS_DIR/queue"
R="$OPS_DIR/results"
S="$OPS_DIR/stage"
F="$OPS_DIR/failed"
LOG_FILE="${MASAR_OPS_LOG:-/var/log/masar-ops.log}"
LOCK_FILE="${MASAR_QUEUE_LOCK:-/var/lock/masar-run-queue.lock}"

mkdir -p "$Q" "$R" "$S" "$F"
# الحاوية core قد تعمل بمستخدم غير root — نسمح لها بالقراءة/الكتابة هنا
chmod 1777 "$OPS_DIR" "$Q" "$R" "$S" "$F" 2>/dev/null || true
mkdir -p "$(dirname "$LOCK_FILE")" 2>/dev/null || true
mkdir -p "$(dirname "$LOG_FILE")" 2>/dev/null || true

# --- (٨) ضامن ما قبل الـ commit: انكسر الإنتاج مرتين لأن سطر تعليق فقد
# علامة "# " من أوله فتحوّل إلى كود بايثون غير صالح، ووصل ذلك مباشرة إلى
# main عبر أمر "commit" دون أي فحص. نكتب هنا سكربتَي فحص بايثون صغيرين
# (يُستدعيان من داخل أمر commit أدناه، قبل git add مباشرة، لكل ملف .py
# و.yaml/.yml مُرحَّل) بدل تضمين كود بايثون داخل سلسلة bash -c ذات
# الاقتباس الأحادي (تعارض في علامات الاقتباس). فحص .py عبر ast.parse فقط
# (بدون كتابة .pyc)، وفحص YAML عبر PyYAML إن كانت مثبّتة على المضيف فقط
# (نتجاهل الفحص بصمت إن لم تكن مثبّتة).
cat >"$OPS_DIR/.masar_py_syntax_check.py" <<'PYEOF'
import ast
import sys

path = sys.argv[1]
try:
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
except OSError as e:
    print(str(e))
    sys.exit(1)

try:
    ast.parse(src, filename=path)
except SyntaxError as e:
    print("%s (line %s)" % (e.msg, e.lineno))
    sys.exit(1)
except Exception as e:
    print(str(e))
    sys.exit(1)

sys.exit(0)
PYEOF

cat >"$OPS_DIR/.masar_yaml_check.py" <<'PYEOF'
import sys

path = sys.argv[1]
try:
    import yaml
except ImportError:
    # PyYAML غير مثبّتة على المضيف — نتجاهل الفحص بصمت (لا رفض).
    sys.exit(0)

try:
    with open(path, encoding="utf-8") as fh:
        yaml.safe_load(fh)
except Exception as e:
    print(str(e))
    sys.exit(1)

sys.exit(0)
PYEOF
chmod 0644 "$OPS_DIR/.masar_py_syntax_check.py" "$OPS_DIR/.masar_yaml_check.py" 2>/dev/null || true

cd "$APP_DIR" || exit 0

# --- (1) قفل عام: لا يعمل أكثر من نسخة واحدة من هذا السكربت في نفس اللحظة ---
exec 9>"$LOCK_FILE" 2>/dev/null || exec 9>/dev/null
if ! flock -n 9; then
  # نسخة أخرى تعمل الآن — نخرج بصمت (exit 0) دون أي إزعاج في اللوق.
  exit 0
fi

ID_RE='^[A-Za-z0-9_-]{6,64}$'
SERVICE_RE='^(postgres|gotenberg|core|core-scheduler|caddy|n8n|n8n-db-init)$'
SCRIPT_NAME_RE='^[a-z0-9_-]+$'

MAX_JOBS_PER_RUN=25
STALE_RUNNING_SECS=600  # 10 دقائق

shopt -s nullglob

# سطر لوق واحد لكل طلب — بدون أسرار (فقط id/cmd/exit_code/seconds).
log_line() {
  local jid="$1" jcmd="$2" jexit="$3" jsecs="$4"
  printf '%s\tid=%s\tcmd=%s\texit=%s\tsecs=%s\n' \
    "$(date -u +%FT%TZ)" "$jid" "$jcmd" "$jexit" "$jsecs" >>"$LOG_FILE" 2>/dev/null || true
}

# يكتب نتيجة JSON بذرّية (write-then-rename) عبر بايثون فقط.
# الاستدعاء: write_result_json result_path job_id cmd exit_code started_at finished_at out_path [args...]
write_result_json() {
  python3 - "$@" <<'PYEOF'
import json
import os
import sys

result_path, job_id, cmd, exit_code, started_at, finished_at, out_path = sys.argv[1:8]
args = sys.argv[8:]

try:
    with open(out_path, "rb") as fh:
        data = fh.read()
except OSError:
    data = b""
if len(data) > 30000:
    data = data[-30000:]
output = data.decode("utf-8", errors="replace")

try:
    exit_code_int = int(exit_code)
except ValueError:
    exit_code_int = 99

result = {
    "id": job_id,
    "cmd": cmd,
    "args": args,
    "exit_code": exit_code_int,
    "started_at": started_at,
    "finished_at": finished_at,
    "output": output,
}

tmp_path = result_path + ".tmp"
with open(tmp_path, "w", encoding="utf-8") as fh:
    json.dump(result, fh, ensure_ascii=False)
os.replace(tmp_path, result_path)
PYEOF
}

# مساعد: يستدعي write_result_json مع توسيع args بأمان (يتجنّب توسيع مصفوفة
# فارغة عبر "${args[@]}" مباشرة).
write_result_with_args() {
  local result_path="$1" jid="$2" jcmd="$3" jexit="$4" jstart="$5" jfinish="$6" jout="$7"
  if [ "${#args[@]}" -gt 0 ]; then
    write_result_json "$result_path" "$jid" "$jcmd" "$jexit" "$jstart" "$jfinish" "$jout" "${args[@]}"
  else
    write_result_json "$result_path" "$jid" "$jcmd" "$jexit" "$jstart" "$jfinish" "$jout"
  fi
}

# --- (4) تنظيف ملفات .running "عالقة" أقدم من 10 دقائق ---
(
  set +e
  now_epoch=$(date -u +%s)
  for rf in "$Q"/*.running; do
    [ -f "$rf" ] || continue
    mtime=$(stat -c %Y "$rf" 2>/dev/null || stat -f %m "$rf" 2>/dev/null)
    [[ "$mtime" =~ ^[0-9]+$ ]] || mtime="$now_epoch"
    age=$(( now_epoch - mtime ))
    if [ "$age" -gt "$STALE_RUNNING_SECS" ]; then
      base=$(basename -- "$rf")
      stale_job_id="${base%.running}"
      note_tmp=$(mktemp)
      echo "stale running marker (age ${age}s, > ${STALE_RUNNING_SECS}s)" >"$note_tmp"
      started_at=$(date -u -d "@$mtime" +%FT%TZ 2>/dev/null || date -u +%FT%TZ)
      finished_at=$(date -u +%FT%TZ)
      write_result_json "$R/${stale_job_id}.json" "$stale_job_id" "unknown" 98 \
        "$started_at" "$finished_at" "$note_tmp"
      rm -f "$note_tmp"
      mkdir -p "$F"
      mv -f "$rf" "$F/$base" 2>/dev/null || rm -f "$rf"
      echo "$(date -u +%FT%TZ) — ops: ملف .running عالق، نُقل إلى failed/: $base (age ${age}s)" >&2
      log_line "$stale_job_id" "unknown" 98 "$age"
    fi
  done
) || true

mapfile -t sorted_files < <(ls -1tr "$Q"/*.json 2>/dev/null)

if [ "${#sorted_files[@]}" -eq 0 ]; then
  exit 0
fi

processed=0

for f in "${sorted_files[@]}"; do
  [ -f "$f" ] || continue

  # --- (6) حد أقصى لعدد الطلبات في هذه التكة — الباقي يُترك للتكة التالية ---
  if [ "$processed" -ge "$MAX_JOBS_PER_RUN" ]; then
    break
  fi
  processed=$((processed + 1))

  # --- (3) تحليل قوي عبر نداء بايثون واحد: id, cmd, ثم كل args — كلها
  # مفصولة بـ NUL (تحتمل أسطرًا جديدة/محارف خاصة داخل أي عنصر). عند أي
  # فشل تحليل أو id غير صالح نطبع "INVALID" فقط ولا نبني JSON يدويًا.
  parse_tmp=$(mktemp)
  python3 - "$f" >"$parse_tmp" 2>/dev/null <<'PYEOF'
import json
import re
import sys

path = sys.argv[1]


def emit(*parts):
    sys.stdout.buffer.write(b"\0".join(p.encode("utf-8", "surrogateescape") for p in parts))
    sys.stdout.buffer.write(b"\0")


try:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("root is not a JSON object")
    job_id = str(data.get("id", ""))
    cmd = str(data.get("cmd", ""))
    args = data.get("args", [])
    if not isinstance(args, list):
        args = []
    args = [str(a) for a in args]
    if not re.match(r'^[A-Za-z0-9_-]{6,64}$', job_id):
        emit("INVALID")
    else:
        emit("OK", job_id, cmd, *args)
except Exception:
    emit("INVALID")
PYEOF
  py_rc=$?

  fields=()
  if [ "$py_rc" -eq 0 ]; then
    mapfile -d '' -t fields <"$parse_tmp"
  fi
  rm -f "$parse_tmp"

  status="${fields[0]:-INVALID}"

  if [ "$status" != "OK" ] || [ "${#fields[@]}" -lt 3 ]; then
    base=$(basename -- "$f")
    mkdir -p "$F"
    mv -f "$f" "$F/$base" 2>/dev/null || rm -f "$f"
    echo "$(date -u +%FT%TZ) — ops: طلب غير قابل للتحليل أو id غير صالح، نُقل إلى failed/: $base" >&2
    log_line "unknown" "unknown" 97 0
    continue
  fi

  job_id="${fields[1]}"
  cmd="${fields[2]}"
  if [ "${#fields[@]}" -gt 3 ]; then
    args=("${fields[@]:3}")
  else
    args=()
  fi

  # تحقق دفاعي إضافي (الـ id تحقّق منه بايثون أعلاه بالفعل)
  if ! [[ "$job_id" =~ $ID_RE ]]; then
    base=$(basename -- "$f")
    mkdir -p "$F"
    mv -f "$f" "$F/$base" 2>/dev/null || rm -f "$f"
    echo "$(date -u +%FT%TZ) — ops: id غير صالح بعد التحقق الدفاعي، نُقل إلى failed/: $base" >&2
    log_line "${job_id:-unknown}" "${cmd:-unknown}" 97 0
    continue
  fi

  running="$Q/${job_id}.running"
  mv -f "$f" "$running" 2>/dev/null || continue

  echo "$(date -u +%FT%TZ) — ops: يعالج $job_id ($cmd)"

  started_at=$(date -u +%FT%TZ)
  job_start_epoch=$(date -u +%s)
  out_tmp=$(mktemp)
  err_tmp=$(mktemp)

  # --- (2) عزل كامل: أي خطأ غير متوقع أثناء معالجة هذا الطلب (بما فيه
  # متغيّر غير معرَّف تحت set -u) يُنهي هذا الـ subshell فقط، ولا يوقف
  # الحلقة الخارجية إطلاقًا — مصيدة EXIT تكتب exit_code=99 كخط دفاع أخير.
  (
    set +e
    job_done=0

    # يلتقط أي رسائل خطأ من الصدفة نفسها (مثل "unbound variable") في err_tmp
    exec 2>"$err_tmp"

    trap '
      rc=$?
      if [ "$job_done" -ne 1 ]; then
        {
          echo "unexpected error in run_queue.sh while processing job $job_id (internal exit $rc)"
          echo "---- captured stderr ----"
          cat "$err_tmp" 2>/dev/null
        } >"$out_tmp" 2>/dev/null
        finished_at_trap=$(date -u +%FT%TZ)
        write_result_with_args "$R/${job_id}.json" "$job_id" "$cmd" 99 "$started_at" "$finished_at_trap" "$out_tmp"
        rm -f "$out_tmp" "$err_tmp" "$running" 2>/dev/null
        job_end_epoch_trap=$(date -u +%s)
        log_line "$job_id" "$cmd" 99 "$(( job_end_epoch_trap - job_start_epoch ))"
      fi
    ' EXIT

    exit_code=0

    case "$cmd" in
      ps)
        timeout -k 10 120 docker compose ps >"$out_tmp" 2>&1
        exit_code=$?
        ;;

      logs)
        service="${args[0]:-}"
        lines="${args[1]:-100}"
        if [[ "$service" =~ $SERVICE_RE ]] && [[ "$lines" =~ ^[0-9]+$ ]] && [ "$lines" -ge 1 ] && [ "$lines" -le 500 ]; then
          timeout -k 10 120 docker compose logs --no-color --tail "$lines" "$service" >"$out_tmp" 2>&1
          exit_code=$?
        else
          echo "unknown or invalid command" >"$out_tmp"
          exit_code=2
        fi
        ;;

      restart)
        service="${args[0]:-}"
        if [[ "$service" =~ $SERVICE_RE ]]; then
          timeout -k 10 120 docker compose restart "$service" >"$out_tmp" 2>&1
          exit_code=$?
        else
          echo "unknown or invalid command" >"$out_tmp"
          exit_code=2
        fi
        ;;

      up)
        timeout -k 10 120 docker compose up -d --build >"$out_tmp" 2>&1
        exit_code=$?
        ;;

      caddy-reload)
        timeout -k 10 120 docker compose exec -T caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile >"$out_tmp" 2>&1
        exit_code=$?
        ;;

      deploy-log)
        lines="${args[0]:-100}"
        if [[ "$lines" =~ ^[0-9]+$ ]] && [ "$lines" -ge 1 ] && [ "$lines" -le 500 ]; then
          timeout -k 10 120 tail -n "$lines" /var/log/masar-autodeploy.log >"$out_tmp" 2>&1
          exit_code=$?
        else
          echo "unknown or invalid command" >"$out_tmp"
          exit_code=2
        fi
        ;;

      backup-log)
        lines="${args[0]:-100}"
        if [[ "$lines" =~ ^[0-9]+$ ]] && [ "$lines" -ge 1 ] && [ "$lines" -le 500 ]; then
          timeout -k 10 120 tail -n "$lines" /var/log/masar-backup.log >"$out_tmp" 2>&1
          exit_code=$?
        else
          echo "unknown or invalid command" >"$out_tmp"
          exit_code=2
        fi
        ;;

      sys)
        timeout -k 10 120 bash -c 'uptime; echo; free -m; echo; df -h /; echo; docker system df' >"$out_tmp" 2>&1
        exit_code=$?
        ;;

      env-keys)
        timeout -k 10 120 grep -o '^[A-Za-z0-9_]*=' "$APP_DIR/.env" >"$out_tmp" 2>&1
        exit_code=$?
        ;;

      migrate)
        # HARDENING: نفس نمط autodeploy.sh (إصلاح تجمّد alembic upgrade head
        # — core-scheduler يُشغّل run_collector_round() فورًا عند إقلاعه
        # ماسكًا معاملات كتابة على jobs/opportunities تتصادم مع ALTER TABLE
        # بالترحيلات). نوقف core-scheduler، نُرحّل، ثم نُعيد تشغيله دومًا —
        # الأوامر الثلاثة متتالية (لا "&&") فيُنفَّذ التشغيل حتى لو فشل/انتهت
        # مهلة أمر الترحيل نفسه.
        timeout -k 10 60 docker compose stop core-scheduler >>"$out_tmp" 2>&1
        timeout -k 10 120 docker compose run --rm -e PGOPTIONS='-c lock_timeout=120s' -v "$APP_DIR/migrations:/migrations" core sh -c "cd /migrations && alembic upgrade head" >>"$out_tmp" 2>&1
        exit_code=$?
        timeout -k 10 60 docker compose start core-scheduler >>"$out_tmp" 2>&1
        ;;

      backup-now)
        timeout -k 10 120 bash "$APP_DIR/deploy/backup.sh" >"$out_tmp" 2>&1
        exit_code=$?
        ;;

      git)
        timeout -k 10 120 bash -c 'git log --oneline -n 10 && git status --short' >"$out_tmp" 2>&1
        exit_code=$?
        ;;

      script)
        name="${args[0]:-}"
        if [[ "$name" =~ $SCRIPT_NAME_RE ]] && [ -f "$APP_DIR/deploy/ops/scripts/${name}.sh" ]; then
          timeout -k 10 120 bash "$APP_DIR/deploy/ops/scripts/${name}.sh" >"$out_tmp" 2>&1
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
          timeout -k 10 120 bash -c '
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
        timeout -k 10 60 bash -c '
          mkdir -p /root/.ssh && chmod 700 /root/.ssh
          [ -f /root/.ssh/masar_deploy ] || ssh-keygen -t ed25519 -N "" -C "masar-core-deploy" -f /root/.ssh/masar_deploy
          cat /root/.ssh/masar_deploy.pub
        ' >"$out_tmp" 2>&1
        exit_code=$?
        ;;

      git-remote-ssh)
        # يحوّل remote origin إلى SSH بمفتاح النشر، ويثبّت هوية git للبوت.
        # git fetch في آخر السطر يتحقق فورًا من أن المفتاح يعمل فعليًا.
        timeout -k 10 60 bash -c '
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
        timeout -k 10 60 bash -c '
          git remote set-url origin https://github.com/aborakanchatgpt-cloud/masar-core.git && echo ok
        ' >"$out_tmp" 2>&1
        exit_code=$?
        ;;

      commit)
        # args: [stage_id, message]. الملفات المُرحَّلة (staged) بواسطة core عبر
        # OPS_DIR/stage/<stage_id>/ (انظر أداة repo_write في mcp_bridge.py)
        # تُنسخ إلى شجرة المستودع، ثم commit + push إلى origin/main مباشرة.
        #
        # HARDENING: كامل سلسلة git (نسخ + add + commit + push) كانت من دون
        # أي timeout في السابق — هذا هو المرشّح الأقوى لتجميد الطابور بالكامل
        # (تعليق git push على SSH يُجمّد استدعاء run_queue.sh كله). الآن
        # محاطة بـ timeout -k 10 300 مع GIT_SSH_COMMAND (ConnectTimeout=20,
        # BatchMode=yes) حتى لا ينتظر أي مطالبة تفاعلية أو اتصال معلّق.
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
            GIT_SSH_COMMAND="ssh -o ConnectTimeout=20 -o BatchMode=yes -i /root/.ssh/masar_deploy -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new" \
            timeout -k 10 300 bash -c '
              set -uo pipefail
              STAGE="$1"
              APP_DIR="$2"
              message="$3"
              OPS_DIR="$4"

              reject=0
              rel_paths=()
              while IFS= read -r -d "" fpath; do
                rel="${fpath#"$STAGE"/}"
                case "$rel" in
                  *..*|.git/*|ops/*|.env|.env.*)
                    reject=1
                    ;;
                esac
                rel_paths+=("$rel")
              done < <(find "$STAGE" -type f -print0)

              if [ "$reject" -eq 1 ] || [ "${#rel_paths[@]}" -eq 0 ]; then
                echo "rejected: invalid file path in stage"
                exit 3
              fi

              cd "$APP_DIR" || exit 4

              existed_before=()
              for rel in "${rel_paths[@]}"; do
                if [ -e "$APP_DIR/$rel" ]; then
                  existed_before+=("$rel")
                fi
              done

              for rel in "${rel_paths[@]}"; do
                mkdir -p "$APP_DIR/$(dirname "$rel")"
                cp "$STAGE/$rel" "$APP_DIR/$rel"
              done

              # --- (٨) ضامن قبل git add: نرفض أي ملف غير صالح نحويًا قبل
              # أن يصل commit + push، بدل اكتشافه بعد وصوله إلى الإنتاج
              # (حدث مرتين بسبب سطر تعليق فقد "# " من أوله). عند أول فشل
              # نطبع سبب الرفض، ونُعيد الملفات المُلمَسة إلى حالتها قبل
              # النسخ (checkout لما كان موجودًا، وحذف ما كان جديدًا كليًا)،
              # ثم exit 5 — فلا يتم أي git add ولا commit إطلاقًا.
              reject_msg=""
              for rel in "${rel_paths[@]}"; do
                case "$rel" in
                  *.py)
                    check_out=$(python3 "$OPS_DIR/.masar_py_syntax_check.py" "$APP_DIR/$rel" 2>&1)
                    if [ $? -ne 0 ]; then
                      reject_msg="rejected: syntax error in $rel: $check_out"
                      break
                    fi
                    ;;
                esac
              done

              if [ -z "$reject_msg" ]; then
                for rel in "${rel_paths[@]}"; do
                  if [ "$rel" = "docker-compose.yml" ]; then
                    check_out=$(docker compose -f "$APP_DIR/docker-compose.yml" config -q 2>&1)
                    if [ $? -ne 0 ]; then
                      reject_msg="rejected: invalid docker-compose.yml: $check_out"
                    fi
                    break
                  fi
                done
              fi

              if [ -z "$reject_msg" ] && python3 -c "import yaml" >/dev/null 2>&1; then
                for rel in "${rel_paths[@]}"; do
                  case "$rel" in
                    data/*.yaml|data/*.yml)
                      check_out=$(python3 "$OPS_DIR/.masar_yaml_check.py" "$APP_DIR/$rel" 2>&1)
                      if [ $? -ne 0 ]; then
                        reject_msg="rejected: invalid yaml in $rel: $check_out"
                        break
                      fi
                      ;;
                  esac
                done
              fi

              if [ -n "$reject_msg" ]; then
                echo "$reject_msg"
                for rel in "${rel_paths[@]}"; do
                  git checkout -- "$rel" 2>/dev/null
                done
                for rel in "${rel_paths[@]}"; do
                  was_old=0
                  for old in "${existed_before[@]}"; do
                    if [ "$old" = "$rel" ]; then
                      was_old=1
                      break
                    fi
                  done
                  if [ "$was_old" -eq 0 ]; then
                    git clean -f -- "$rel" 2>/dev/null
                  fi
                done
                exit 5
              fi

              for rel in "${rel_paths[@]}"; do
                git add -A -- "$rel"
              done

              if git diff --cached --quiet; then
                echo "nothing to commit"
                exit 0
              fi

              if git -c user.name=masar-core-bot -c user.email=masar-core-bot@users.noreply.github.com commit -q -m "$message"; then
                if git push -q origin HEAD:main; then
                  sha=$(git rev-parse HEAD)
                  echo "committed and pushed: $sha"
                  touch "$OPS_DIR/.deploy_needed"
                  rm -rf "$STAGE"
                  exit 0
                else
                  echo "push failed — commit left local; next autodeploy tick will discard it via git reset --hard origin/main"
                  exit 4
                fi
              else
                echo "git commit failed"
                exit 4
              fi
            ' _ "$STAGE" "$APP_DIR" "$message" "$OPS_DIR" >"$out_tmp" 2>&1
            exit_code=$?
          fi
        fi
        ;;

      stage-clean)
        timeout -k 10 60 bash -c '
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
    write_result_with_args "$R/${job_id}.json" "$job_id" "$cmd" "$exit_code" "$started_at" "$finished_at" "$out_tmp"

    job_end_epoch=$(date -u +%s)
    log_line "$job_id" "$cmd" "$exit_code" "$(( job_end_epoch - job_start_epoch ))"

    rm -f "$out_tmp" "$err_tmp" "$running"
    job_done=1
  )
done
