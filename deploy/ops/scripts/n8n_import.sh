#!/usr/bin/env bash
# n8n_import.sh — يستورد سير عمل مسار الجديدة (n8n/workflows/masar_*.json،
# نتاج بند B5b فقط) إلى نسخة n8n المستضافة ذاتيًا، عبر `docker compose cp`
# ثم `n8n import:workflow --input=...` داخل الحاوية مباشرة (بدون أي عمل
# يدوي بالواجهة). **لا يلمس** بقية n8n/workflows/*.json (وركفلوهات n8n
# Cloud القديمة المُصدَّرة، ولا n8n/workflows/parts/) — تلك من مسؤولية
# n8n-import.sh الموجود مسبقًا حصرًا (قاعدة القسم 0 البند 4 بـPLAN.md:
# ممنوع لمس وركفلوهات n8n القديمة غير المذكورة هنا).
#
# ملاحظة مهمّة اكتُشفت أثناء التحقق المحلي (n8n CLI 2.35.7، راجع
# docs/reports/B5b-executor.md): `n8n import:workflow` بهذه النسخة **يرفض**
# أي ملف بلا حقل "id" علوي بخطأ SQLITE/Postgres NOT NULL — أي أن جميع ملفات
# n8n/workflows/*.json القديمة (بلا id، من تصدير n8n Cloud) **لا تُستورَد
# فعليًا اليوم عبر n8n-import.sh الموجود** رغم أن ذلك السكربت لا يُبلّغ عن
# هذا كخطأ (يظن أنها "غير موجودة فيُنشئها" بينما الاستيراد نفسه يفشل). هذا
# خارج نطاق B5b (لا يلمس هذا السكربت تلك الملفات) لكنه NEEDS-OWNER/مُتابعة
# مستقبلية موثّقة بـn8n/README.md.
#
# idempotent: كل ملفات masar_*.json تحمل حقل "id" علويًا ثابتًا (مُشتقًّ
# حتميًا من اسم الملف) — n8n import:workflow يحدّث الوركفلو الموجود بنفس id
# بدل إنشاء نسخة جديدة (مُتحقَّق محليًا: استيراد نفس الملف مرتين ينتج صفًّا
# واحدًا بقاعدة البيانات، انظر docs/reports/B5b-executor.md).
#
# سير العمل المستوردة تبقى **غير نشطة (inactive) دائمًا** — هذا السكربت لا
# يُفعّل أي وركفلو أبدًا؛ التفعيل خطوة يدوية للمالك بعد التحقق من الاعتمادات
# (راجع n8n/README.md).
set -euo pipefail
cd /opt/masar-core

WORKFLOWS_DIR="n8n/workflows"
FILE_GLOB="masar_*.json"
shopt -s nullglob

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "== سرد سير العمل الحالية داخل n8n (لأسماء الملفات بلا id) =="
if ! docker compose exec -T n8n n8n list:workflow >"$TMP/.existing_list.txt" 2>"$TMP/.existing_list.err"; then
  echo "!! فشل تنفيذ n8n list:workflow داخل الحاوية — الخرج:"
  cat "$TMP/.existing_list.err" 2>/dev/null || true
  exit 1
fi

echo "== التأكد من وجود مجلد الاستيراد داخل الحاوية (وتفريغه من أي بقايا) =="
docker compose exec -T n8n sh -c 'rm -rf /tmp/n8n-import-b5b && mkdir -p /tmp/n8n-import-b5b'

imported_by_id=0
imported_new=0
skipped_existing=0
invalid=0
failed=0

for f in "$WORKFLOWS_DIR"/$FILE_GLOB; do
  base="$(basename "$f")"
  meta="$(python3 -c "
import json, sys
try:
    with open(sys.argv[1], encoding='utf-8') as fh:
        d = json.load(fh)
except Exception:
    print('INVALID')
    sys.exit(0)
name = d.get('name') or ''
wid = d.get('id') or ''
if not name:
    print('INVALID')
else:
    print(wid + '\t' + name)
" "$f")"

  if [ "$meta" = "INVALID" ]; then
    echo "  ! تجاهل (JSON غير صالح أو بلا name): $base"
    invalid=$((invalid + 1))
    continue
  fi

  wid="$(printf '%s' "$meta" | cut -f1)"
  name="$(printf '%s' "$meta" | cut -f2-)"

  if [ -z "$wid" ]; then
    # بلا id علوي — نفس منطق n8n-import.sh: تخطَّ إن كان الاسم موجودًا مسبقًا
    if awk -F'|' -v n="$name" '$2==n{found=1} END{exit !found}' "$TMP/.existing_list.txt"; then
      echo "  = موجود مسبقًا (تخطّي، بلا id): $name"
      skipped_existing=$((skipped_existing + 1))
      continue
    fi
  fi

  if ! docker compose cp "$f" "n8n:/tmp/n8n-import-b5b/$base"; then
    echo "  !! فشل نسخ $base إلى الحاوية"
    failed=$((failed + 1))
    continue
  fi

  if docker compose exec -T n8n n8n import:workflow --input="/tmp/n8n-import-b5b/$base" \
      >"$TMP/.import_out.$base.txt" 2>&1; then
    if [ -n "$wid" ]; then
      echo "  + استُورد/حُدِّث (id=$wid): $name"
      imported_by_id=$((imported_by_id + 1))
    else
      echo "  + استُورد جديدًا (بلا id): $name"
      imported_new=$((imported_new + 1))
    fi
  else
    echo "  !! فشل استيراد $base — الخرج:"
    tail -20 "$TMP/.import_out.$base.txt" | sed 's/^/     /'
    failed=$((failed + 1))
  fi
done

echo ""
echo "===== ملخص الاستيراد (n8n_import.sh — B5b) ====="
echo "حُدِّث بنفس id (idempotent): $imported_by_id"
echo "استُورد جديدًا (بلا id، أول مرة): $imported_new"
echo "تخطّي (موجود مسبقًا بلا id): $skipped_existing"
echo "غير صالح (تجاهل): $invalid"
echo "فشل: $failed"
echo ""
echo "===== قائمة سير العمل الحالية داخل n8n (بعد الاستيراد) ====="
docker compose exec -T n8n n8n list:workflow

if [ "$failed" -gt 0 ]; then
  exit 1
fi
