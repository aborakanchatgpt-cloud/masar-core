#!/usr/bin/env python3
"""
scripts/bench_planner.py — B3: قياس أداء المخطِّط (core/app/planner.py) فوق
بيانات اصطناعية بحجم واقعي: 1,500 عميل × 3,000 وظيفة (معيار قبول B3 بالتكليف:
"1,500 customers × 3,000 jobs in < 5 minutes"، الدليل §9 المرحلة 3).

يعمل داخل مخطّط Postgres معزول تمامًا (`b3bench`، عبر `SET search_path`
بـplanner.run_plan_round(schema=...)) — **لا يلمس أي جدول إنتاجي إطلاقًا**
(public.customers/jobs/... الحقيقية غير مُستخدمة ولا مُعدّلة)، ثم يحذف المخطّط
بالكامل عند الانتهاء (بما فيها عند الفشل — `finally`).

التشغيل (على الخادم، عبر جسر MCP أو /admin/ops):
    docker compose exec -T core python /repo/scripts/bench_planner.py
أو عبر deploy/ops/scripts/bench-planner.sh (`ops("script", ["bench-planner"])`).

يطبع سطر JSON نهائي وحيد (آخر سطر بالمخرجات) بالنتيجة الكاملة، حتى يسهل
تحليله آليًا من تقرير القبول.
"""
from __future__ import annotations

import json
import random
import sys
import time

sys.path.insert(0, "/app")  # core/Dockerfile: WORKDIR /app، حزمة app هناك —
# هذا السكربت يعمل من /repo/scripts (تركيب المستودع الكامل للقراءة فقط) لا
# من /app/scripts (غير موجود، Dockerfile ينسخ app/ فقط) — راجع
# deploy/ops/scripts/bench-planner.sh.

from sqlalchemy import text  # noqa: E402

from app import planner  # noqa: E402
from app.discovery import get_engine  # noqa: E402

SCHEMA = "b3bench"
N_CUSTOMERS = 1500
N_JOBS = 3000
SEED = 42

FAMILIES = [
    "chem_process", "quality", "hse", "maintenance_ops", "production",
    "project_controls", "lab_chemistry", "water_treatment", "supply_chain",
    "it_software", "construction_pm", "finance_accounting",
]
CITIES = ["Riyadh", "Jeddah", "Dammam", "Khobar", "Jubail", "Yanbu", "Dhahran", "Mecca"]
SKILLS_POOL = [
    "SAP", "AutoCAD", "Aspen HYSYS", "MATLAB", "Six Sigma", "ISO 9001",
    "HAZOP", "PLC", "SCADA", "Primavera P6", "Root Cause Analysis",
]
TITLES_BY_FAMILY = {
    "chem_process": ["Process Engineer", "Chemical Engineer", "Plant Engineer"],
    "quality": ["Quality Engineer", "QA Specialist", "Quality Control Inspector"],
    "hse": ["HSE Engineer", "Safety Engineer", "Occupational Health Specialist"],
    "maintenance_ops": ["Maintenance Engineer", "Reliability Engineer", "Operations Supervisor"],
    "production": ["Production Engineer", "Production Supervisor", "Manufacturing Engineer"],
    "project_controls": ["Project Controls Engineer", "Planning Engineer", "Cost Control Engineer"],
    "lab_chemistry": ["Laboratory Technician", "Analytical Chemist", "Lab Chemist"],
    "water_treatment": ["Water Treatment Engineer", "Desalination Engineer"],
    "supply_chain": ["Supply Chain Engineer", "Procurement Engineer", "Logistics Coordinator"],
    "it_software": ["Software Engineer", "Data Analyst", "Systems Administrator"],
    "construction_pm": ["Civil Engineer", "Site Engineer", "Project Engineer"],
    "finance_accounting": ["Accountant", "Financial Analyst", "Finance Specialist"],
}
SENIORITIES = ["intern", "entry", None, "senior", "manager", "lead"]
APPLY_MODES = ["external_form", "email", "unknown"]


def _drop_schema(engine) -> None:
    with engine.begin() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE'))


def _create_schema(engine) -> None:
    with engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{SCHEMA}"'))
        conn.execute(text(f'SET LOCAL search_path TO "{SCHEMA}", public'))
        # جداول مبسّطة بنيويًا لكن متوافقة الأسماء/الأنواع مع ما يستعلمه
        # planner.py فعليًا (SELECT صريحة بأسماء أعمدة — لا SELECT *) —
        # نفس المنطق الحقيقي يعمل بلا أي تفريع كود خاص بالبنش.
        conn.execute(text("""
            CREATE TABLE customers (
                id bigserial PRIMARY KEY, status text NOT NULL DEFAULT 'active',
                target_daily integer NOT NULL DEFAULT 17,
                cities jsonb NOT NULL DEFAULT '[]', families jsonb NOT NULL DEFAULT '[]'
            )
        """))
        conn.execute(text("""
            CREATE TABLE profiles (
                customer_id bigint PRIMARY KEY, years_exp numeric(4,1), seniority text,
                nationality_saudi boolean NOT NULL DEFAULT false,
                titles jsonb NOT NULL DEFAULT '[]', skills jsonb NOT NULL DEFAULT '[]',
                degree text
            )
        """))
        conn.execute(text("CREATE TABLE wallets (customer_id bigint PRIMARY KEY, balance integer NOT NULL DEFAULT 0)"))
        conn.execute(text("""
            CREATE TABLE jobs (
                id bigserial PRIMARY KEY, title text NOT NULL, family text, years_min integer,
                seniority text, saudi_only boolean NOT NULL DEFAULT false, city text,
                skills jsonb NOT NULL DEFAULT '[]', company_id bigint, company_name text,
                apply_mode text, first_seen_at timestamptz NOT NULL DEFAULT now(),
                out_of_region boolean NOT NULL DEFAULT false
            )
        """))
        conn.execute(text("CREATE INDEX ON jobs (family, out_of_region, first_seen_at)"))
        conn.execute(text("""
            CREATE TABLE opportunities (
                id bigserial PRIMARY KEY, customer_id bigint NOT NULL, job_id bigint NOT NULL,
                score numeric(5,4) NOT NULL, tier text NOT NULL, reasons jsonb NOT NULL DEFAULT '{}',
                planned_for date NOT NULL, status text NOT NULL DEFAULT 'planned',
                created_at timestamptz NOT NULL DEFAULT now(),
                UNIQUE (customer_id, job_id)
            )
        """))
        conn.execute(text("""
            CREATE TABLE applications (
                id bigserial PRIMARY KEY, customer_id bigint, company_key text, sent_at timestamptz
            )
        """))
        conn.execute(text("""
            CREATE TABLE company_cooldowns (
                company_key text, customer_id bigint, last_sent_at timestamptz
            )
        """))


def _seed(engine) -> None:
    rng = random.Random(SEED)
    with engine.begin() as conn:
        conn.execute(text(f'SET LOCAL search_path TO "{SCHEMA}", public'))

        customers_rows = []
        profiles_rows = []
        wallets_rows = []
        for i in range(1, N_CUSTOMERS + 1):
            fams = rng.sample(FAMILIES, k=rng.choice([1, 1, 2]))
            cities = rng.sample(CITIES, k=rng.choice([1, 2, 2, 3]))
            years = round(rng.uniform(0, 20), 1)
            titles = [{"title": t, "weight": 1.0} for t in TITLES_BY_FAMILY[fams[0]][:2]]
            skills = rng.sample(SKILLS_POOL, k=rng.randint(1, 4))
            customers_rows.append(
                {"id": i, "status": "active", "target": 17, "cities": json.dumps(cities), "families": json.dumps(fams)}
            )
            profiles_rows.append(
                {
                    "id": i,
                    "years": years,
                    "seniority": rng.choice(SENIORITIES),
                    "saudi": rng.random() < 0.6,
                    "titles": json.dumps(titles, ensure_ascii=False),
                    "skills": json.dumps(skills, ensure_ascii=False),
                }
            )
            wallets_rows.append({"id": i, "balance": 50})

        conn.execute(
            text(
                "INSERT INTO customers (id, status, target_daily, cities, families) "
                "VALUES (:id, :status, :target, :cities, :families)"
            ),
            customers_rows,
        )
        conn.execute(
            text(
                "INSERT INTO profiles (customer_id, years_exp, seniority, nationality_saudi, titles, skills) "
                "VALUES (:id, :years, :seniority, :saudi, :titles, :skills)"
            ),
            profiles_rows,
        )
        conn.execute(text("INSERT INTO wallets (customer_id, balance) VALUES (:id, :balance)"), wallets_rows)

        jobs_rows = []
        for j in range(1, N_JOBS + 1):
            fam = rng.choice(FAMILIES)
            years_min = rng.choice([None, 0, 1, 2, 3, 5, 8, 10])
            city = rng.choice(CITIES + [None])
            skills = rng.sample(SKILLS_POOL, k=rng.randint(0, 3))
            company_n = j % 300
            jobs_rows.append(
                {
                    "id": j,
                    "title": rng.choice(TITLES_BY_FAMILY[fam]),
                    "family": fam,
                    "years_min": years_min,
                    "seniority": rng.choice(SENIORITIES),
                    "saudi_only": rng.random() < 0.1,
                    "city": city,
                    "skills": json.dumps(skills, ensure_ascii=False),
                    "company_id": company_n,
                    "company_name": f"Bench Company {company_n}",
                    "apply_mode": rng.choice(APPLY_MODES),
                    "days_ago": rng.randint(0, 14),
                }
            )
        conn.execute(
            text(
                """
                INSERT INTO jobs (
                    id, title, family, years_min, seniority, saudi_only, city, skills,
                    company_id, company_name, apply_mode, first_seen_at, out_of_region
                ) VALUES (
                    :id, :title, :family, :years_min, :seniority, :saudi_only, :city, :skills,
                    :company_id, :company_name, :apply_mode, now() - (:days_ago || ' days')::interval, false
                )
                """
            ),
            jobs_rows,
        )


def main() -> int:
    engine = get_engine()
    _drop_schema(engine)
    try:
        t0 = time.perf_counter()
        _create_schema(engine)
        _seed(engine)
        seed_seconds = time.perf_counter() - t0

        t1 = time.perf_counter()
        result = planner.run_plan_round(schema=SCHEMA, engine=engine)
        plan_seconds = time.perf_counter() - t1

        summary = {
            "ok": True,
            "n_customers": N_CUSTOMERS,
            "n_jobs": N_JOBS,
            "seed_seconds": round(seed_seconds, 3),
            "plan_seconds": round(plan_seconds, 3),
            "planner_reported_seconds": result.get("seconds"),
            "customers_active": result.get("customers_active"),
            "customers_planned": result.get("customers_planned"),
            "opportunities_planned": result.get("opportunities_planned"),
            "under_5_minutes": plan_seconds < 300,
        }
        print(json.dumps(summary, ensure_ascii=False))
        return 0
    finally:
        _drop_schema(engine)


if __name__ == "__main__":
    raise SystemExit(main())
