#!/usr/bin/env python3
"""
scripts/import_company_directory.py — B12.2: استيراد/تحديث دليل الشركات
(company_directory) من data/company_directory_seed.csv.

idempotent بالكامل (upsert بمفتاح lower(hr_email) — راجع
core/app/company_directory.py:import_seed_csv) — يمكن تشغيله مرارًا بأمان
كلما أُضيفت شركات جديدة للملف.

الاستخدام (على الخادم، عبر جسر MCP أو /admin/ops، أو محليًا للاختبار):
    DATABASE_URL=postgresql+psycopg://... python scripts/import_company_directory.py
    python scripts/import_company_directory.py --csv data/company_directory_seed.csv

يطبع سطر JSON نهائي وحيد بالنتيجة ({"inserted": n, "updated": n, "skipped": n}).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "core"))  # لإتاحة `from app import company_directory` محليًا

from sqlalchemy import create_engine  # noqa: E402

from app import company_directory  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--csv",
        type=Path,
        default=REPO_ROOT / "data" / "company_directory_seed.csv",
        help="مسار ملف بذر دليل الشركات (افتراضي: data/company_directory_seed.csv)",
    )
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("خطأ: متغيّر DATABASE_URL غير معرّف", file=sys.stderr)
        return 1
    if database_url.startswith("postgresql://"):
        database_url = "postgresql+psycopg://" + database_url[len("postgresql://") :]

    engine = create_engine(database_url, pool_pre_ping=True)
    result = company_directory.import_seed_csv(engine, args.csv)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
