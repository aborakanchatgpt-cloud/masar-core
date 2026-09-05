#!/usr/bin/env python3
"""
استيراد ESCO إلى جدول taxonomy_terms (المرحلة 2، القسم 9).

الاستخدام (على الخادم الفعلي، بعد تحميل ملف occupations CSV يدويًا من
https://esco.ec.europa.eu/en/use-esco/download إلى data/esco/):

    python scripts/import_esco.py --csv data/esco/occupations_en.csv --lang en

المنطق: يقرأ عمودي preferredLabel وaltLabels لكل مهنة ESCO، ويقارنها بكلمات
data/taxonomy_local.yaml لكل عائلة — أي تطابق (ولو جزئي) يُضاف كمصطلح جديد
لتلك العائلة بجدول taxonomy_terms، مع تفادي التكرار (uq_taxonomy_term_family_lang).

هذا "مرشّح أول" آلي فقط — الدليل نفسه يطلب مراجعة يدوية لعينة من النتائج
(نفس مبدأ التحقق بمعيار قبول المرحلة 2)، وليس استيرادًا أعمى لكل ESCO.
لا يحتاج اتصال شبكة أثناء التشغيل — يقرأ ملف CSV محلي فقط.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path

import yaml
from sqlalchemy import create_engine, text

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_family_keywords(taxonomy_path: Path) -> dict[str, list[str]]:
    """يبني {family: [كل الكلمات المفتاحية عربي+إنجليزي]} من taxonomy_local.yaml."""
    with open(taxonomy_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    families: dict[str, list[str]] = {}
    for family_key, family_data in data.get("families", {}).items():
        keywords = list(family_data.get("keywords_en", [])) + list(family_data.get("keywords_ar", []))
        families[family_key] = [kw.lower() for kw in keywords]
    return families


def match_family(text_value: str, families: dict[str, list[str]]) -> str | None:
    lowered = text_value.lower()
    for family_key, keywords in families.items():
        for kw in keywords:
            if kw and kw in lowered:
                return family_key
    return None


def iter_esco_rows(csv_path: Path):
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            preferred = (row.get("preferredLabel") or "").strip()
            alt_labels_raw = (row.get("altLabels") or "").strip()
            alt_labels = [a.strip() for a in alt_labels_raw.split("\n") if a.strip()]
            if preferred:
                yield preferred
            for alt in alt_labels:
                yield alt


def import_esco(csv_path: Path, lang: str, database_url: str | None) -> list[tuple[str, str]]:
    families = load_family_keywords(REPO_ROOT / "data" / "taxonomy_local.yaml")

    matches: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for term in iter_esco_rows(csv_path):
        family = match_family(term, families)
        if not family:
            continue
        key = (family, term.strip())
        if key in seen:
            continue
        seen.add(key)
        matches.append(key)

    if database_url:
        engine = create_engine(database_url)
        with engine.begin() as conn:
            for family, term in matches:
                conn.execute(
                    text(
                        """
                        INSERT INTO taxonomy_terms (family, term, lang, excluded)
                        VALUES (:family, :term, :lang, false)
                        ON CONFLICT (family, term, lang) DO NOTHING
                        """
                    ),
                    {"family": family, "term": term, "lang": lang},
                )

    return matches


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True, help="مسار ملف ESCO occupations CSV محليًا")
    parser.add_argument("--lang", default="en", help="رمز اللغة (en/ar) — افتراضي en")
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL"),
        help="رابط Postgres (افتراضيًا من متغيّر البيئة DATABASE_URL). اتركه فارغًا لتجربة جافة (dry run) بدون كتابة.",
    )
    args = parser.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"خطأ: الملف غير موجود: {csv_path}", file=sys.stderr)
        sys.exit(1)

    matches = import_esco(csv_path, args.lang, args.database_url)

    by_family: dict[str, int] = {}
    for family, _term in matches:
        by_family[family] = by_family.get(family, 0) + 1

    mode = "كُتبت لقاعدة البيانات" if args.database_url else "تجربة جافة (dry run) — لم تُكتب لأي قاعدة بيانات"
    print(f"تم إيجاد {len(matches)} مصطلح مطابق ({mode}):")
    for family, count in sorted(by_family.items(), key=lambda x: -x[1]):
        print(f"  {family}: {count}")


if __name__ == "__main__":
    main()
