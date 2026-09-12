"""
Masar Core — دليل الشركات السعودية للتقديم المبادر (B12.2/B12.3، راجع
claude/masar_build_brief_v4_2026-09-12.md القسم 2).

مصدر البيانات: data/company_directory_seed.csv (سطر لكل شركة، بريد توظيف
متحقَّق يدويًا عبر WebFetch لصفحة المصدر الرسمية، لا اختلاق، راجع docstring
الملف نفسه للمنهجية الكاملة). scripts/import_company_directory.py يستدعي
`import_seed_csv` هنا لملء/تحديث جدول `company_directory` (idempotent —
upsert بمفتاح lower(hr_email)).

**التكامل مع مسار الإرسال الحالي بلا تعديل أي ملف لا نملكه:** لكل صفّ دليل
نشط، `ensure_company_and_job` تُنشئ (أو تُحدّث idempotent):
    1. صفّ `companies` (اسم الشركة + `careers_email` — عمود أضافته ترحيلة
       0023 خصيصًا ليُفعّل placeholder موجود أصلًا بـ
       `app.apply_email.company_directory_email`، بلا تعديل ذلك الملف).
    2. صفّ `sources` وحيد بنوع `source_type='company_directory'` (لا يُجلب
       منه شيء فعليًا — موجود فقط لأن `jobs.source_id` غير قابل لNULL).
    3. صفّ `jobs` اصطناعي واحد **لكل شركة بالدليل** (لا لكل عميل) يمثّل
       "فرصة تقديم مبادر" لتلك الشركة/العائلة — `apply_mode='email'`،
       `family=sector_family`، `out_of_region=false`، بلا `url`. هذا الصفّ
       يُعاد استخدامه لكل عميل يُخطَّط له تقديم مبادر لنفس الشركة (فرصة
       `opportunities` منفصلة لكل عميل، بعلامة `speculative=true` — عمود
       أضافته ترحيلة 0023 أيضًا) — planner.py (B12.3) هو من يُنشئ صفوف
       `opportunities` هذه، لا هذا الملف.

هذا التصميم يعيد استخدام كامل مسار الإرسال الموجود (send_builder.py →
apply_email.select_apply_email → composer.build_email) بلا أي تعديل على
ملفات لا نملكها — راجع تقرير هذه الدفعة لطلب سلكٍ إضافي اختياري (تخصيص
صياغة "مبادر" مختلفة) موجّه لـFable/الوكيل A.
"""
from __future__ import annotations

import csv
import hashlib
import logging
from functools import lru_cache
from pathlib import Path

import yaml
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

logger = logging.getLogger("masar.company_directory")

# نفس القيمة الحرفية بالضبط لـ telegram_onboarding.FLEX_LABEL (كلا الملفين
# مملوكان لهذه الدفعة) — العميل الذي اختار "مرن" أثناء onboarding يحمل هذا
# النص الحرفي الوحيد بمصفوفة customers.cities، فلا تُطابَق أي مدينة محدّدة
# (نفس القيد المعروف مسبقًا بمسار المطابقة العادي بـplanner.py/matching.py —
# غير معدّل هنا، خارج نطاق B12). التقديم المبادر يتعامل معه صراحةً: عميل
# "مرن" لا يُقيَّد بمدينة دليل الشركات إطلاقًا (`flexible=True` بـ
# fetch_candidates_for_customer). يجب إبقاء هذا الثابت مطابقًا حرفيًا
# لـtelegram_onboarding.FLEX_LABEL عند أي تعديل مستقبلي لأحدهما.
FLEXIBLE_CITY_LABEL = "🌐 مرن - أي مكان بالمملكة"

# نفس عائلات taxonomy_local.yaml الـ 21 (باستثناء out_of_scope) — أي سطر
# بملف البذر بعائلة خارج هذه القائمة يُتخطّى بصمت (سطر مُدخل خطأًا) بدل
# إدراجه بعائلة غير معروفة لا تُطابق أي عميل أبدًا.
VALID_SECTOR_FAMILIES = {
    "chem_process", "quality", "hse", "maintenance_ops", "coatings", "production",
    "project_controls", "lab_chemistry", "water_treatment", "supply_chain", "admin",
    "hospitality", "customer_ops", "finance_accounting", "hr_recruiting", "it_software",
    "logistics_ops", "construction_pm", "marketing_comms", "program_admin_ops", "legal_compliance",
}

MAX_SPECULATIVE_PER_CUSTOMER_PER_DAY = 10


def _data_dir() -> Path:
    import os

    return Path(os.environ.get("DATA_DIR", "/app/data"))


@lru_cache(maxsize=1)
def _family_labels_ar() -> dict[str, str]:
    """يقرأ label_ar لكل عائلة من data/taxonomy_local.yaml (نفس الملف الذي
    يقرأه discovery.py للتصنيف) — يُستخدم لعرض اسم مجال مقروء بعنوان الفرصة
    المبادرة (jobs.title الاصطناعي) وبموضوع الرسالة (composer.build_email)
    بدل رمز العائلة الداخلي الخام (مثال: chem_process)."""
    path = _data_dir() / "taxonomy_local.yaml"
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except OSError:
        return {}
    families = data.get("families", {}) or {}
    return {name: (spec.get("label_ar") or name) for name, spec in families.items()}


def family_label_ar(family: str) -> str:
    return _family_labels_ar().get(family, family)


# ---------------------------------------------------------------------------
# استيراد ملف البذر (idempotent — upsert بمفتاح lower(hr_email))
# ---------------------------------------------------------------------------


def import_seed_csv(engine: Engine, path: Path | None = None) -> dict:
    csv_path = path or (_data_dir() / "company_directory_seed.csv")
    if not csv_path.exists():
        logger.warning("ملف company_directory_seed.csv غير موجود بـ%s — تخطي الاستيراد", csv_path)
        return {"inserted": 0, "updated": 0, "skipped": 0}

    inserted = updated = skipped = 0
    with open(csv_path, encoding="utf-8") as f, engine.begin() as conn:
        reader = csv.DictReader(f)
        for row in reader:
            name = (row.get("name") or "").strip()
            sector_family = (row.get("sector_family") or "").strip()
            hr_email = (row.get("hr_email") or "").strip().lower()
            email_source_url = (row.get("email_source_url") or "").strip()
            if not name or not hr_email or "@" not in hr_email or not email_source_url:
                skipped += 1
                continue
            if sector_family not in VALID_SECTOR_FAMILIES:
                logger.warning("سطر دليل شركات بعائلة غير معروفة تُخطّى: %s (%s)", name, sector_family)
                skipped += 1
                continue

            result = conn.execute(
                text(
                    """
                    INSERT INTO company_directory (
                        name, name_ar, sector_family, city, hr_email, email_source_url,
                        website, verified_at, active, notes, updated_at
                    ) VALUES (
                        :name, :name_ar, :sector_family, :city, :hr_email, :email_source_url,
                        :website, NULLIF(:verified_at, '')::date, true, :notes, now()
                    )
                    ON CONFLICT (lower(hr_email)) DO UPDATE SET
                        name = EXCLUDED.name,
                        name_ar = EXCLUDED.name_ar,
                        sector_family = EXCLUDED.sector_family,
                        city = EXCLUDED.city,
                        email_source_url = EXCLUDED.email_source_url,
                        website = EXCLUDED.website,
                        verified_at = EXCLUDED.verified_at,
                        notes = EXCLUDED.notes,
                        updated_at = now()
                    RETURNING (xmax = 0) AS was_insert
                    """
                ),
                {
                    "name": name[:255],
                    "name_ar": (row.get("name_ar") or "").strip()[:255] or None,
                    "sector_family": sector_family,
                    "city": (row.get("city") or "").strip()[:120] or None,
                    "hr_email": hr_email[:255],
                    "email_source_url": email_source_url,
                    "website": (row.get("website") or "").strip() or None,
                    "verified_at": (row.get("verified_at") or "").strip(),
                    "notes": (row.get("notes") or "").strip() or None,
                },
            ).first()
            if result and result[0]:
                inserted += 1
            else:
                updated += 1

    logger.info("استيراد دليل الشركات: %s جديد، %s محدّث، %s متخطّى", inserted, updated, skipped)
    return {"inserted": inserted, "updated": updated, "skipped": skipped}


# ---------------------------------------------------------------------------
# ربط صفّ دليل بصفّ companies/sources/jobs اصطناعي (idempotent)
# ---------------------------------------------------------------------------


def _synthetic_dedup_key(directory_id: int) -> str:
    return hashlib.sha1(f"company_directory_speculative|{directory_id}".encode("utf-8")).hexdigest()


def ensure_company_and_job(conn: Connection, directory_row: dict) -> int:
    """يضمن وجود صفّ companies (بـcareers_email مضبوط) وصفّ sources وصفّ jobs
    اصطناعي وحيد لهذا الصفّ من الدليل، ويُرجع jobs.id الناتج — يُستدعى من
    planner.py (B12.3) قبل إدراج فرصة `opportunities` مبادرة. idempotent
    بالكامل (ON CONFLICT/تحديث) — قابل للاستدعاء مرارًا بلا آثار جانبية
    غير مرغوبة، ورخيص (كل الاستعلامات على مفاتيح مفهرسة)."""
    directory_id = directory_row["id"]
    name = directory_row["name"]
    hr_email = directory_row["hr_email"]
    sector_family = directory_row["sector_family"]
    city = directory_row.get("city")

    # الاسم الحقيقي (بلا بادئة اصطناعية) عمدًا — يبقى متوافقًا مع
    # company_key() المستخدمة بمنطق التبريد/السقف الأسبوعي (fetch_cooldown_pairs/
    # fetch_weekly_cap_companies بـplanner.py وsend_builder.py) لو ظهرت نفس
    # الشركة لاحقًا كمصدر وظائف معلن حقيقي (ATS) — نفس الهوية، لا ازدواج.
    company_row = conn.execute(
        text(
            """
            INSERT INTO companies (name, status, careers_email)
            VALUES (:name, 'active', :email)
            ON CONFLICT (name) DO UPDATE SET careers_email = EXCLUDED.careers_email
            RETURNING id
            """
        ),
        {"name": name, "email": hr_email},
    ).first()
    company_id = company_row[0]

    source_row = conn.execute(
        text(
            """
            INSERT INTO sources (company_id, source_type, source_url, enabled)
            VALUES (:company_id, 'company_directory', :url, false)
            ON CONFLICT (source_url) DO UPDATE SET company_id = EXCLUDED.company_id
            RETURNING id
            """
        ),
        {"company_id": company_id, "url": f"company-directory://{directory_id}"},
    ).first()
    source_id = source_row[0]

    # jobs.title = اسم العائلة المهنية بالعربية (لا اسم الشركة ولا مسمّى
    # وظيفي مُختلَق) — composer.build_email يستقبله كـ{title} برسالة التقديم
    # المبادر (B12.4)، فيقرأ طبيعيًا "طلب توظيف مبادر — الجودة" مثلًا.
    dedup_key = _synthetic_dedup_key(directory_id)
    job_row = conn.execute(
        text(
            """
            INSERT INTO jobs (
                source_id, company_id, title, family, dedup_key, company_name,
                city, apply_mode, out_of_region, skills, raw_json
            ) VALUES (
                :source_id, :company_id, :title, :family, :dedup_key, :company_name,
                :city, 'email', false, '[]', :raw_json
            )
            ON CONFLICT (dedup_key) DO UPDATE SET last_seen_at = now()
            RETURNING id
            """
        ),
        {
            "source_id": source_id,
            "company_id": company_id,
            "title": family_label_ar(sector_family),
            "family": sector_family,
            "dedup_key": dedup_key,
            "company_name": name,
            "city": city,
            "raw_json": "{}",
        },
    ).first()
    job_id = job_row[0]

    conn.execute(
        text("UPDATE company_directory SET company_id = :cid, job_id = :jid WHERE id = :id"),
        {"cid": company_id, "jid": job_id, "id": directory_id},
    )
    return job_id


# ---------------------------------------------------------------------------
# ترشيح صفوف الدليل لعميل قصير الهدف اليومي (B12.3) — SQL مباشر، بلا ORM
# ---------------------------------------------------------------------------


def is_flexible_cities(cities: list[str]) -> bool:
    return FLEXIBLE_CITY_LABEL in (cities or [])


def fetch_candidates_for_customer(
    conn: Connection, families: list[str], cities: list[str], flexible: bool, limit: int
) -> list[dict]:
    """صفوف دليل نشطة تطابق عائلات العميل، ومدنه المختارة (أو أي مدينة إن
    كان مرنًا — `flexible=True` يعني العميل اختار "🌐 مرن" بonboarding، راجع
    `telegram_onboarding.FLEX_LABEL`). لا ترتيب مطابقة/درجة هنا (التقديم
    المبادر تكميلي بحت، لا مطابقة مُقيّمة) — فقط ترتيب عشوائي مستقر
    (`id`) لتوزيع عادل بين شركات الدليل عبر الأيام."""
    if not families:
        return []
    sql = """
        SELECT id, name, name_ar, sector_family, city, hr_email, email_source_url
        FROM company_directory
        WHERE active = true AND sector_family = ANY(:families)
    """
    params: dict = {"families": families, "limit": limit}
    if not flexible:
        if not cities:
            return []
        sql += " AND (city IS NULL OR city = ANY(:cities))"
        params["cities"] = cities
    sql += " ORDER BY id LIMIT :limit"
    rows = conn.execute(text(sql), params).mappings().all()
    return [dict(r) for r in rows]


def stats(conn: Connection) -> dict:
    row = conn.execute(
        text(
            """
            SELECT
                count(*) FILTER (WHERE active) AS active_total,
                count(*) AS total,
                count(DISTINCT sector_family) FILTER (WHERE active) AS families_covered,
                count(DISTINCT city) FILTER (WHERE active AND city IS NOT NULL) AS cities_covered
            FROM company_directory
            """
        )
    ).mappings().first()
    by_family_rows = conn.execute(
        text(
            """
            SELECT sector_family, count(*) AS n FROM company_directory
            WHERE active = true GROUP BY sector_family ORDER BY n DESC
            """
        )
    ).all()
    return {
        "active_total": int(row["active_total"] or 0),
        "total": int(row["total"] or 0),
        "families_covered": int(row["families_covered"] or 0),
        "cities_covered": int(row["cities_covered"] or 0),
        "by_family": {r[0]: r[1] for r in by_family_rows},
    }
