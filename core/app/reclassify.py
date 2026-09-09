"""
Masar Core — إعادة تصنيف العائلة المهنية لكل صفوف jobs (مراجعة B2b، PLAN.md).

المشكلة: `discovery.classify_family()` يعتمد على كاشين على مستوى العملية
(`_families_cache`/`_family_patterns_cache` بـ`core/app/discovery.py`) —
يُملآن مرة واحدة فقط لكل عملية uvicorn حيّة. تعديل `data/taxonomy_local.yaml`
وحده لا يكفي لتصحيح تصنيف صفّ jobs موجود أصلًا: `family` محفوظة كما حُسبت
وقت الإدراج، ولن تتغيّر إلا حين يُعاد حسابها صراحة. كما أن نقطة
`POST /admin/discovery/backfill-locations` الموجودة تُعيد الحساب فقط لصفوف
`family IS NULL` — لا تلتقط تصحيح صفّ صُنّف سابقًا (أو ظلّ بلا تصنيف رغم
أن الكلمة المفتاحية أصبحت موجودة بالمعجم منذ ذلك الحين، وهو الحال الغالب
هنا فعليًا: فحص محلي على عيّنة حيّة أظهر أن ~25% من "غير المصنّف" الحالي
يُطابَق فعليًا بالمعجم الحالي، وبقي بلا تصنيف فقط لأنه لم تتاح إعادة حساب
منذ إدراجه).

هذا سكربت مستقل (لا نقطة HTTP إداريّة جديدة عمدًا — الحمل هنا كتابة على كل
صفّ داخل النطاق فقد يستغرق دقائق على قاعدة كبيرة، لا يناسب طلب HTTP متزامن)
يُشغّل كعملية بايثون طازجة منفصلة داخل حاوية core الحيّة:

    docker compose exec -T core python -m app.reclassify

أو عبر طابور ops الحالي بلا SSH (انظر deploy/ops/scripts/reclassify.sh):

    POST /admin/ops {"cmd":"script","args":["reclassify"]}

بما أنها عملية طازجة، الكاشان أعلاه يُبنيان من الصفر بمحتوى
`taxonomy_local.yaml` **الحالي على القرص** (بلا حاجة لإعادة تشغيل حاوية core
نفسها) — فيعيد حساب `family` لكل صفّ jobs "داخل النطاق" (`out_of_region =
false`) بصرف النظر عن قيمته الحالية، دفعة دفعة (`BATCH_SIZE`)، **idempotent
بالكامل** (تشغيله مرتين متتاليتين بلا تغيّر بالمعجم بينهما يُنتج
`changed=0` بالتشغيلة الثانية). لا يحذف ولا يُدرج أي صفّ، ولا يمسّ أي صفّ
`out_of_region = true` (مبدأ "لا نحذف من jobs أبدًا" نفسه المتّبع بكل نقاط
B2 الإدارية — هنا نُحدّث عمودًا واحدًا فقط، ولا نلمس صفوفًا خارج النطاق
أصلًا).

الاستخدام محليًا (بيئة تطوير، قاعدة بيانات تجريبية):
    cd core && DATABASE_URL=postgresql://... DATA_DIR=../data python -m app.reclassify
"""
from __future__ import annotations

import logging
import sys

from sqlalchemy import text

from app import discovery

logger = logging.getLogger("masar.reclassify")

BATCH_SIZE = 1000


def run() -> dict:
    """يعيد تصنيف `family` لكل صفوف jobs داخل النطاق (`out_of_region = false`)
    دفعة دفعة عبر ترقيم مفتاحي بـ`id` (نفس نمط `backfill-locations` بـ
    discovery_api.py)، ويُرجع ملخّص عدّادات نهائي. دالة نقية مفصولة عن
    `main()`/`__main__` عمدًا حتى تُختبر مباشرة (`core/tests/test_reclassify.py`)
    بلا حاجة لتشغيل السكربت كعملية فرعية منفصلة."""
    engine = discovery.get_engine()
    total = 0
    changed = 0
    classified_real = 0
    out_of_scope_n = 0
    unclassified_n = 0
    last_id = 0

    while True:
        with engine.begin() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT id, title, description_snippet, family
                    FROM jobs
                    WHERE out_of_region = false AND id > :last_id
                    ORDER BY id
                    LIMIT :batch
                    """
                ),
                {"last_id": last_id, "batch": BATCH_SIZE},
            ).mappings().all()
            if not rows:
                break

            for row in rows:
                total += 1
                last_id = row["id"]
                new_family = discovery.classify_family(row["title"], row["description_snippet"] or "")

                if new_family != row["family"]:
                    changed += 1
                    conn.execute(
                        text("UPDATE jobs SET family = :family WHERE id = :id"),
                        {"family": new_family, "id": row["id"]},
                    )

                if new_family is None:
                    unclassified_n += 1
                elif new_family in discovery.EXCLUDED_FAMILY_NAMES:
                    out_of_scope_n += 1
                else:
                    classified_real += 1

    # نفس تعريف discovery_api.py:stats (مراجعة B2b) — القسمة على jobs_in_region
    # الكامل، لا على (الكل − out_of_scope).
    pct_classified = ((classified_real + out_of_scope_n) / total) if total else 0.0
    pct_real = (classified_real / total) if total else 0.0

    summary = {
        "jobs_in_region_total": total,
        "rows_changed": changed,
        "classified_real": classified_real,
        "out_of_scope": out_of_scope_n,
        "unclassified": unclassified_n,
        "family_classified_pct_in_region": round(pct_classified, 4),
        "family_real_pct_in_region": round(pct_real, 4),
    }
    logger.info("reclassify: %s", summary)
    return summary


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    summary = run()
    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
