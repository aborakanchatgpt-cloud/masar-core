#!/usr/bin/env python3
"""
scripts/classify_report.py — B2b: تقرير تغطية التصنيف المهني على جدول jobs
الحي، عبر الجلسة الموجودة أصلًا بقاعدة البيانات (`app.discovery.get_engine()`
— نفس الاتصال والإعدادات (pool/DATABASE_URL) التي يستخدمها core الحيّ، لا
اتصال منفصل بإعدادات مختلفة). قراءة فقط بالكامل — لا يُعدّل أي صفّ (لإعادة
التصنيف فعليًا استخدم core/app/reclassify.py، أو
POST /admin/ops {"cmd":"script","args":["reclassify"]}).

يطبع: (1) ملخّص jobs_in_region/classified_real/out_of_scope/unclassified +
كلا المقياسين (family_classified_pct_in_region يشمل out_of_scope،
family_real_pct_in_region لا يشمله — نفس تعريف discovery_api.py:stats بعد
مراجعة B2b)، (2) توزيع لكل نوع مصدر، (3) أكثر N عنوان غير مصنّف تكرارًا
(لتوسيع data/taxonomy_local.yaml لاحقًا — نفس منطق GET /admin/unclassified-sample
لكن بلا حاجة لطلب HTTP إداري).

التشغيل (على الخادم، عبر جسر MCP أو /admin/ops):
    docker compose exec -T core python /repo/scripts/classify_report.py
    docker compose exec -T core python /repo/scripts/classify_report.py --top 50

محليًا (بيئة تطوير):
    cd core && DATABASE_URL=postgresql://... DATA_DIR=../data \
        python /path/to/scripts/classify_report.py

يطبع سطر JSON نهائي وحيد (آخر سطر بالمخرجات) بنفس اتفاقية
scripts/bench_planner.py، حتى يسهل تحليله آليًا من تقرير القبول.
"""
from __future__ import annotations

import argparse
import json
import sys

sys.path.insert(0, "/app")  # core/Dockerfile: WORKDIR /app، حزمة app هناك —
# هذا السكربت يعمل من /repo/scripts (تركيب المستودع الكامل للقراءة فقط) لا
# من /app/scripts (غير موجود، Dockerfile ينسخ app/ فقط) — راجع
# deploy/ops/scripts/classify-report.sh. محليًا (بلا /app) نضيف core/ النسبي
# كملاذ ثانٍ حتى يعمل السكربت خارج الحاوية أيضًا.
try:
    from app.discovery import EXCLUDED_FAMILY_NAMES, get_engine  # noqa: E402
except ImportError:
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
    from app.discovery import EXCLUDED_FAMILY_NAMES, get_engine  # noqa: E402

from sqlalchemy import text  # noqa: E402


def build_report(top_n: int) -> dict:
    engine = get_engine()
    excluded_sql = "(" + ", ".join(f"'{f}'" for f in sorted(EXCLUDED_FAMILY_NAMES)) + ")"

    with engine.connect() as conn:
        row = conn.execute(
            text(
                f"""
                SELECT
                    count(*) FILTER (WHERE out_of_region = false) AS jobs_in_region,
                    count(*) FILTER (
                        WHERE out_of_region = false AND family IS NOT NULL
                              AND family NOT IN {excluded_sql}
                    ) AS classified_real,
                    count(*) FILTER (
                        WHERE out_of_region = false AND family IN {excluded_sql}
                    ) AS out_of_scope,
                    count(*) FILTER (WHERE out_of_region = false AND family IS NULL) AS unclassified
                FROM jobs
                """
            )
        ).mappings().first()

        by_source = conn.execute(
            text(
                """
                SELECT s.source_type,
                       count(*) AS jobs_in_region,
                       count(*) FILTER (WHERE j.family IS NULL) AS unclassified
                FROM jobs j JOIN sources s ON s.id = j.source_id
                WHERE j.out_of_region = false
                GROUP BY s.source_type
                ORDER BY jobs_in_region DESC
                """
            )
        ).mappings().all()

        top_unclassified = conn.execute(
            text(
                """
                SELECT title, count(*) AS n
                FROM jobs
                WHERE out_of_region = false AND family IS NULL
                GROUP BY title
                ORDER BY n DESC, title
                LIMIT :n
                """
            ),
            {"n": top_n},
        ).mappings().all()

    jobs_in_region = row["jobs_in_region"] or 0
    classified_real = row["classified_real"] or 0
    out_of_scope = row["out_of_scope"] or 0
    unclassified = row["unclassified"] or 0

    pct_classified = ((classified_real + out_of_scope) / jobs_in_region) if jobs_in_region else 0.0
    pct_real = (classified_real / jobs_in_region) if jobs_in_region else 0.0

    return {
        "jobs_in_region": jobs_in_region,
        "classified_real": classified_real,
        "out_of_scope": out_of_scope,
        "unclassified": unclassified,
        "family_classified_pct_in_region": round(pct_classified, 4),
        "family_real_pct_in_region": round(pct_real, 4),
        "target_family_classified_pct": 0.80,
        "target_family_real_pct": 0.70,
        "meets_classified_target": pct_classified >= 0.80,
        "meets_real_target": pct_real >= 0.70,
        "by_source": [dict(r) for r in by_source],
        "top_unclassified_titles": [dict(r) for r in top_unclassified],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top", type=int, default=40, help="عدد أكثر العناوين غير المصنّفة تكرارًا (افتراضيًا 40)")
    args = parser.parse_args()

    report = build_report(args.top)

    print(f"jobs_in_region                    = {report['jobs_in_region']}")
    print(f"classified_real                   = {report['classified_real']}")
    print(f"out_of_scope                      = {report['out_of_scope']}")
    print(f"unclassified                      = {report['unclassified']}")
    print(
        f"family_classified_pct_in_region   = {report['family_classified_pct_in_region']:.4f}"
        f"  (الهدف >= 0.80 — {'✓' if report['meets_classified_target'] else '✗'})"
    )
    print(
        f"family_real_pct_in_region         = {report['family_real_pct_in_region']:.4f}"
        f"  (الهدف >= 0.70 — {'✓' if report['meets_real_target'] else '✗'})"
    )
    print("\nبحسب نوع المصدر:")
    for r in report["by_source"]:
        print(f"  {r['source_type']:<18} in_region={r['jobs_in_region']:<6} unclassified={r['unclassified']}")
    if report["top_unclassified_titles"]:
        print(f"\nأكثر {len(report['top_unclassified_titles'])} عنوان غير مصنّف تكرارًا:")
        for r in report["top_unclassified_titles"]:
            print(f"  {r['n']:>3}  {r['title']}")

    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
