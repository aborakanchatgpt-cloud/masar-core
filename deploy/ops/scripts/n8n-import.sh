#!/usr/bin/env bash
# n8n-import.sh — يستورد كل سير عمل n8n المُصدَّرة من n8n/workflows/*.json
# (بما فيها الملف المُقسَّم إلى أجزاء base64 داخل n8n/workflows/parts/) إلى
# نسخة n8n المستضافة ذاتيًا، عبر أوامر n8n CLI داخل الحاوية مباشرة — بدون أي
# عمل يدوي في الواجهة. الاستيراد يشمل فقط سير العمل غير الموجود مسبقًا
# (بالاسم) حتى يكون الأمر آمنًا للتكرار (idempotent) ولا يُنشئ تكرارات عند
# تشغيله أكثر من مرة. سير العمل المستوردة تُنشأ INACTIVE دائمًا من n8n CLI.
set -euo pipefail
cd /opt/masar-core

WORKFLOWS_DIR="n8n/workflows"
PARTS_DIR="$WORKFLOWS_DIR/parts"

shopt -s nullglob

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "== نسخ ملفات سير العمل الكاملة (غير المُقسَّمة) =="
for f in "$WORKFLOWS_DIR"/*.json; do
  cp "$f" "$TMP/"
done

echo "== إعادة تجميع الأجزاء المُقسَّمة (base64) إن وُجدت =="
if [ -d "$PARTS_DIR" ]; then
  declare -A seen_bases=()
  for f in "$PARTS_DIR"/*.part*; do
    base="$(basename "$f" | sed -E 's/\.part[0-9]+x?$//')"
    seen_bases["$base"]=1
  done

  for base in "${!seen_bases[@]}"; do
    echo "  -> تجميع: $base"
    b64_tmp="$(mktemp)"
    # فرز نصي عادي كافٍ هنا: partNN بترقيم بصفر بادئ (01..95)، وأي جزء
    # مُقسَّم إضافي يحمل لاحقة "x" (مثل part14 ثم part14x) يُرتَّب بعد
    # partNN تلقائيًا لأن الأحرف الإضافية تجعل السلسلة "أطول" في المقارنة
    # النصية دون الحاجة لإعادة ترقيم الأجزاء اللاحقة.
    while IFS= read -r p; do
      cat "$p" >>"$b64_tmp"
    done < <(printf '%s\n' "$PARTS_DIR/${base}".part* | sort)

    out_file="$TMP/$base"
    if ! python3 -c "
import base64, sys
with open(sys.argv[1], 'rb') as f:
    b64 = f.read()
with open(sys.argv[2], 'wb') as f:
    f.write(base64.b64decode(b64))
" "$b64_tmp" "$out_file"; then
      echo "  !! فشل فك ترميز base64 لـ: $base"
      rm -f "$b64_tmp"
      exit 1
    fi
    rm -f "$b64_tmp"

    sha_file="$PARTS_DIR/${base}.sha256"
    if [ -f "$sha_file" ]; then
      expected="$(awk '{print $1}' "$sha_file")"
      actual="$(sha256sum "$out_file" | awk '{print $1}')"
      if [ "$expected" != "$actual" ]; then
        echo "  !! خطأ: sha256 غير مطابق لـ $base (متوقع=$expected فعلي=$actual)"
        exit 1
      fi
      echo "     sha256 مطابق: $base"
    fi
  done
fi

echo "== التحقق من صحة JSON لكل الملفات، واستخراج الأسماء، وتحديد الجديد منها =="
mkdir -p "$TMP/to_import"

echo "== التأكد من وجود مجلد الاستيراد داخل الحاوية (وتفريغه من أي بقايا سابقة) =="
docker compose exec -T n8n sh -c 'rm -rf /tmp/n8n-import && mkdir -p /tmp/n8n-import'

echo "== سرد سير العمل الحالية داخل n8n (لتفادي إنشاء تكرارات) =="
if ! docker compose exec -T n8n n8n list:workflow >"$TMP/.existing_list.txt" 2>"$TMP/.existing_list.err"; then
  echo "!! فشل تنفيذ n8n list:workflow داخل الحاوية — الخرج:"
  cat "$TMP/.existing_list.err" 2>/dev/null || true
  exit 1
fi

python3 - "$TMP" "$TMP/.existing_list.txt" "$TMP/to_import" >"$TMP/.summary.txt" <<'PYEOF'
import json
import os
import shutil
import sys

tmp_dir, existing_list_path, import_dir = sys.argv[1:4]

existing_names = set()
with open(existing_list_path, encoding="utf-8", errors="replace") as f:
    for line in f:
        line = line.rstrip("\n")
        if "|" not in line:
            continue
        _id, _, name = line.partition("|")
        name = name.strip()
        if name:
            existing_names.add(name)

os.makedirs(import_dir, exist_ok=True)

imported = []
skipped = []
invalid = []

for fname in sorted(os.listdir(tmp_dir)):
    if not fname.endswith(".json"):
        continue
    fpath = os.path.join(tmp_dir, fname)
    if not os.path.isfile(fpath):
        continue
    try:
        with open(fpath, encoding="utf-8") as f:
            data = json.load(f)
        name = data.get("name") or ""
        if not name:
            raise ValueError("no name field")
    except Exception:
        invalid.append(fname)
        continue
    if name in existing_names:
        skipped.append(name)
    else:
        shutil.copy2(fpath, os.path.join(import_dir, fname))
        imported.append(name)

print("===== ملخص الاستيراد =====")
print("تم استيراده الآن: %d" % len(imported))
for n in imported:
    print("  + %s" % n)
print("موجود مسبقًا (تم تخطيه): %d" % len(skipped))
for n in skipped:
    print("  = %s" % n)
print("غير صالح (تم تجاهله): %d" % len(invalid))
for n in invalid:
    print("  ! %s" % n)
PYEOF

if compgen -G "$TMP/to_import/*.json" >/dev/null; then
  echo "== نسخ سير العمل الجديدة إلى الحاوية واستيرادها =="
  docker compose cp "$TMP/to_import/." n8n:/tmp/n8n-import/
  docker compose exec -T n8n n8n import:workflow --separate --input=/tmp/n8n-import/
else
  echo "لا يوجد سير عمل جديد للاستيراد — كل شيء موجود مسبقًا أو غير صالح."
fi

echo ""
cat "$TMP/.summary.txt"
echo ""
echo "===== قائمة سير العمل الحالية داخل n8n (بعد الاستيراد) ====="
docker compose exec -T n8n n8n list:workflow
