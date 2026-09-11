"""
Masar Core — نقاط نهاية العملاء/الملف الشخصي/المحفظة/الخطة (B3، الدليل §9
المرحلة 3). كل النقاط محمية بتوكن الإدارة (نفس نمط discovery_api.py) —
لا نظام مستخدمين نهائيين بعد (يُبنى لاحقًا بوحدة identity كاملة، B5).

    POST /customers                          إنشاء عميل (المدن/العائلات/الهدف اليومي)
    GET  /customers/{id}                     عميل + ملفه + رصيده
    GET  /customers/by-telegram/{chat_id}    telegram_chat_id → {customer_id, status} (B5c)
    POST /customers/{id}/status              تفعيل/إيقاف + سجلّ تدقيق (B5c)
    POST /customers/{id}/cv                  رفع سيرة PDF خام (multipart) (B5c)
    POST /customers/{id}/profile             ملف مهيكل (JSON) + cv_text اختياري
                                              → استخراج حتمي (rules) للحقول الناقصة
    POST /wallet/{customer_id}/credit         قيد دفتر رصيد + تحديث wallets بمعاملة واحدة
    GET  /wallet/{customer_id}                الرصيد + آخر 20 قيدًا
    POST /subscriptions                       شراء منتج (اشتراك/رصيد/سيرة مستقلة)
    GET  /plan/{customer_id}/today            فرص اليوم المخطّطة (الطبقة/الدرجة/الأسباب)
    POST /plan/run-now                        تشغيل المخطّط فورًا (عميل واحد=مزامن، الكل=خلفية)
    GET  /admin/matching/explain              شرح درجة/استبعاد زوج (عميل، وظيفة) — للمراجع

B5c: `by-telegram`/`/status`/`/cv` تسدّ NEEDS-CORE #2/#3/#4. `email_service`
أصبح NULLable (0010)؛ create_mail_link يملؤه لاحقًا (COALESCE).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import text

from app import matching, planner
from app.auth import require_admin_token
from app.collectors.field_extractor import extract_seniority, extract_skills, extract_years_required
from app.collectors.normalizer import company_key as normalize_company_key
from app.discovery import classify_family, get_engine

logger = logging.getLogger("masar.customers_api")

router = APIRouter(tags=["customers"], dependencies=[Depends(require_admin_token)])

LEDGER_REASONS = {"purchase", "application_sent", "bounce_refund", "guarantee_refund", "adjustment"}
COOLDOWN_DAYS = planner.COOLDOWN_DAYS
WEEKLY_CAP_WINDOW_DAYS = planner.WEEKLY_CAP_WINDOW_DAYS
WEEKLY_CAP_CUSTOMERS = planner.WEEKLY_CAP_CUSTOMERS

# B5c
CV_MAX_BYTES = 5 * 1024 * 1024  # 5MB
_PDF_MAGIC = b"%PDF-"
# B9/B0: أضيفت 'pending' (تسجيل ذاتي قبل تأكيد الدفع) — تسمح بالتفعيل
# اليدوي pending→active من بطاقة العميل ببوت الأدمن (نفس نقطة النهاية
# هذه)، بجانب active<->paused الحاليّتين. 'expired' عمدًا خارج هذه
# المجموعة كما كانت قبل B9 — انتقالها منطق منفصل (guarantee.py).
CUSTOMER_STATUS_ALLOWED_VALUES = {"pending", "active", "paused"}


# ---------------------------------------------------------------------------
# customers
# ---------------------------------------------------------------------------


class CustomerCreateRequest(BaseModel):
    name: str
    email_service: str | None = None  # B5c: اختياري (0010) — يملؤه create_mail_link لاحقًا
    telegram_chat_id: int | None = None
    phone: str | None = None
    cities: list[str] = Field(default_factory=list)
    families: list[str] = Field(default_factory=list)
    target_daily: int = 17


@router.post("/customers")
async def create_customer(body: CustomerCreateRequest) -> dict:
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                INSERT INTO customers (telegram_chat_id, name, phone, email_service, cities, families, target_daily)
                VALUES (:tg, :name, :phone, :email, :cities, :families, :target)
                RETURNING id
                """
            ),
            {
                "tg": body.telegram_chat_id,
                "name": body.name,
                "phone": body.phone,
                "email": body.email_service,
                "cities": json.dumps(body.cities, ensure_ascii=False),
                "families": json.dumps(body.families, ensure_ascii=False),
                "target": max(1, min(body.target_daily, planner.MAX_DAILY)),
            },
        ).first()
        customer_id = row[0]
        conn.execute(
            text("INSERT INTO wallets (customer_id, balance) VALUES (:id, 0) ON CONFLICT DO NOTHING"),
            {"id": customer_id},
        )
    return {"ok": True, "customer_id": customer_id}


def _fetch_customer_row(conn, customer_id: int) -> dict:
    row = conn.execute(
        text(
            """
            SELECT c.*, COALESCE(w.balance, 0) AS wallet_balance
            FROM customers c
            LEFT JOIN wallets w ON w.customer_id = c.id
            WHERE c.id = :id
            """
        ),
        {"id": customer_id},
    ).mappings().first()
    return dict(row) if row else None


@router.get("/customers/{customer_id}")
async def get_customer(customer_id: int) -> dict:
    engine = get_engine()
    with engine.connect() as conn:
        customer = _fetch_customer_row(conn, customer_id)
        if not customer:
            raise HTTPException(status_code=404, detail="عميل غير موجود")
        profile = conn.execute(
            text("SELECT * FROM profiles WHERE customer_id = :id"), {"id": customer_id}
        ).mappings().first()
    return {"customer": customer, "profile": dict(profile) if profile else None}


# B5c — NEEDS-CORE #2 (فهرس فريد جزئي 0010 يضمن نتيجة واحدة).
@router.get("/customers/by-telegram/{chat_id}")
async def get_customer_by_telegram(chat_id: int) -> dict:
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT id, status FROM customers WHERE telegram_chat_id = :chat_id"),
            {"chat_id": chat_id},
        ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="لا يوجد عميل بمعرّف محادثة تيليجرام هذا")
    return {"customer_id": row["id"], "status": row["status"]}


# B5c — تفعيل/إيقاف + سجلّ تدقيق (customer_status_audit، 0010) — NEEDS-CORE #4.
class CustomerStatusRequest(BaseModel):
    status: str
    note: str | None = None


@router.post("/customers/{customer_id}/status")
async def update_customer_status(customer_id: int, body: CustomerStatusRequest) -> dict:
    if body.status not in CUSTOMER_STATUS_ALLOWED_VALUES:
        raise HTTPException(status_code=400, detail=f"status غير مسموح: {body.status}")

    engine = get_engine()
    with engine.begin() as conn:
        customer = conn.execute(
            text("SELECT id, status FROM customers WHERE id = :id FOR UPDATE"), {"id": customer_id}
        ).mappings().first()
        if not customer:
            raise HTTPException(status_code=404, detail="عميل غير موجود")

        old_status = customer["status"]
        if old_status not in CUSTOMER_STATUS_ALLOWED_VALUES:
            raise HTTPException(status_code=409, detail=f"لا يمكن تغيير حالة عميل بحالة '{old_status}'")

        conn.execute(
            text("UPDATE customers SET status = :s, updated_at = now() WHERE id = :id"),
            {"s": body.status, "id": customer_id},
        )
        conn.execute(
            text(
                "INSERT INTO customer_status_audit (customer_id, old_status, new_status, note, created_at) "
                "VALUES (:cid, :old, :new, :note, now())"
            ),
            {"cid": customer_id, "old": old_status, "new": body.status, "note": body.note},
        )

    return {"ok": True, "customer_id": customer_id, "old_status": old_status, "new_status": body.status}


# B5c — NEEDS-CORE #3. CV_DATA_DIR/<id>/uploaded_cv.pdf (اتفاقية cv_builder.py)؛
# المسار/hash على customers مباشرة (0010) لا profiles (قد لا يوجد صفّها بعد).
@router.post("/customers/{customer_id}/cv")
async def upload_customer_cv(customer_id: int, file: UploadFile = File(...)) -> dict:
    engine = get_engine()
    with engine.connect() as conn:
        if not _fetch_customer_row(conn, customer_id):
            raise HTTPException(status_code=404, detail="عميل غير موجود")

    content = await file.read()
    if len(content) > CV_MAX_BYTES:
        raise HTTPException(status_code=400, detail=f"حجم الملف يتجاوز {CV_MAX_BYTES // (1024 * 1024)}MB")
    if not content.startswith(_PDF_MAGIC):
        raise HTTPException(status_code=400, detail="الملف المرفوع ليس PDF صالحًا")

    sha256_hex = hashlib.sha256(content).hexdigest()
    cv_dir = Path(os.environ.get("CV_DATA_DIR", "/data/cv")) / str(customer_id)
    cv_dir.mkdir(parents=True, exist_ok=True)
    cv_path = cv_dir / "uploaded_cv.pdf"
    cv_path.write_bytes(content)

    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE customers SET cv_pdf_path = :path, cv_pdf_sha256 = :sha, "
                "cv_pdf_uploaded_at = now(), updated_at = now() WHERE id = :id"
            ),
            {"path": str(cv_path), "sha": sha256_hex, "id": customer_id},
        )

    return {
        "ok": True,
        "customer_id": customer_id,
        "cv_pdf_path": str(cv_path),
        "cv_pdf_sha256": sha256_hex,
        "bytes": len(content),
    }


# ---------------------------------------------------------------------------
# profile — استخراج حتمي (rules) للحقول الناقصة من cv_text (الدليل §4.3،
# نسخة B3: بلا مسار Claude — يُضاف لاحقًا بمهمة Profile Extractor المجدولة
# بالمرحلة 5. لا يُعدّل core/app/collectors/field_extractor.py هنا — يُعاد
# استخدام دوال الاستخراج الموجودة أصلًا لنص الإعلانات على نص السيرة أيضًا
# (نفس المنطق الحتمي بالكلمات المفتاحية/الأنماط يصلح للنصين معًا).
# ---------------------------------------------------------------------------


class ProfileUpsertRequest(BaseModel):
    cv_text: str | None = None
    cv_pdf_path: str | None = None
    years_exp: float | None = None
    seniority: str | None = None
    degree: str | None = None
    certs: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    titles: list[str] = Field(default_factory=list)
    nationality_saudi: bool = False
    languages: list[str] = Field(default_factory=list)


def _extract_titles_from_cv(cv_text: str, limit: int = 8) -> list[str]:
    """تخمين حتمي بسيط لمسميات محتملة: أسطر السيرة التي تُطابق عائلة مهنية
    معروفة بمعجم taxonomy_local.yaml (نفس classify_family المستخدمة
    بمحرّك الاكتشاف — استيراد قراءة فقط، لا تعديل)، بترتيب الظهور بلا تكرار."""
    found: list[str] = []
    for raw_line in cv_text.splitlines():
        line = raw_line.strip(" \t-•*:")
        if not line or len(line) > 120:
            continue
        if classify_family(line) and line not in found:
            found.append(line)
        if len(found) >= limit:
            break
    return found


@router.post("/customers/{customer_id}/profile")
async def upsert_profile(customer_id: int, body: ProfileUpsertRequest) -> dict:
    engine = get_engine()
    with engine.connect() as conn:
        if not _fetch_customer_row(conn, customer_id):
            raise HTTPException(status_code=404, detail="عميل غير موجود")

    years_exp = body.years_exp
    seniority = body.seniority
    skills = list(body.skills)
    titles = list(body.titles)
    used_rules = False

    if body.cv_text:
        if years_exp is None:
            derived_years, _ = extract_years_required(body.cv_text)
            if derived_years is not None:
                years_exp = float(derived_years)
                used_rules = True
        if seniority is None:
            derived_seniority = extract_seniority(body.cv_text)
            if derived_seniority is not None:
                seniority = derived_seniority
                used_rules = True
        if not skills:
            derived_skills = extract_skills(body.cv_text)
            if derived_skills:
                skills = derived_skills
                used_rules = True
        if not titles:
            derived_titles = _extract_titles_from_cv(body.cv_text)
            if derived_titles:
                titles = derived_titles
                used_rules = True

    extracted_by = "rules" if used_rules else None
    titles_json = [{"title": t, "weight": 1.0} for t in titles]

    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO profiles (
                    customer_id, cv_text, cv_pdf_path, years_exp, seniority, degree,
                    certs, skills, titles, nationality_saudi, languages, updated_at, extracted_by
                ) VALUES (
                    :customer_id, :cv_text, :cv_pdf_path, :years_exp, :seniority, :degree,
                    :certs, :skills, :titles, :nationality_saudi, :languages, now(), :extracted_by
                )
                ON CONFLICT (customer_id) DO UPDATE SET
                    cv_text = COALESCE(EXCLUDED.cv_text, profiles.cv_text),
                    cv_pdf_path = COALESCE(EXCLUDED.cv_pdf_path, profiles.cv_pdf_path),
                    years_exp = EXCLUDED.years_exp,
                    seniority = EXCLUDED.seniority,
                    degree = COALESCE(EXCLUDED.degree, profiles.degree),
                    certs = EXCLUDED.certs,
                    skills = EXCLUDED.skills,
                    titles = EXCLUDED.titles,
                    nationality_saudi = EXCLUDED.nationality_saudi,
                    languages = EXCLUDED.languages,
                    updated_at = now(),
                    extracted_by = EXCLUDED.extracted_by
                """
            ),
            {
                "customer_id": customer_id,
                "cv_text": body.cv_text,
                "cv_pdf_path": body.cv_pdf_path,
                "years_exp": years_exp,
                "seniority": seniority,
                "degree": body.degree,
                "certs": json.dumps(body.certs, ensure_ascii=False),
                "skills": json.dumps(skills, ensure_ascii=False),
                "titles": json.dumps(titles_json, ensure_ascii=False),
                "nationality_saudi": body.nationality_saudi,
                "languages": json.dumps(body.languages, ensure_ascii=False),
                "extracted_by": extracted_by,
            },
        )

    return {
        "ok": True,
        "customer_id": customer_id,
        "extracted_by": extracted_by,
        "years_exp": years_exp,
        "seniority": seniority,
        "skills": skills,
        "titles": titles,
    }


# ---------------------------------------------------------------------------
# wallet — قيد دفتر الرصيد + تحديث wallets **بمعاملة واحدة**، مع قفل صف
# (FOR UPDATE) وقيد CHECK بمستوى القاعدة (balance>=0) كشبكة أمان مزدوجة —
# معيار قبول B3: "الرصيد لا يصبح سالبًا أبدًا".
# ---------------------------------------------------------------------------


class WalletCreditRequest(BaseModel):
    delta: int
    reason: str
    ref: str | None = None


@router.post("/wallet/{customer_id}/credit")
async def wallet_credit(customer_id: int, body: WalletCreditRequest) -> dict:
    if body.reason not in LEDGER_REASONS:
        raise HTTPException(status_code=400, detail=f"سبب غير معروف: {body.reason}")

    engine = get_engine()
    with engine.begin() as conn:
        if not _fetch_customer_row(conn, customer_id):
            raise HTTPException(status_code=404, detail="عميل غير موجود")

        row = conn.execute(
            text("SELECT balance FROM wallets WHERE customer_id = :id FOR UPDATE"), {"id": customer_id}
        ).first()
        current = row[0] if row else 0
        if row is None:
            conn.execute(text("INSERT INTO wallets (customer_id, balance) VALUES (:id, 0)"), {"id": customer_id})

        new_balance = current + body.delta
        if new_balance < 0:
            raise HTTPException(
                status_code=400,
                detail=f"الرصيد لا يمكن أن يصبح سالبًا (الحالي={current}، المطلوب={body.delta})",
            )

        conn.execute(
            text("UPDATE wallets SET balance = :b, updated_at = now() WHERE customer_id = :id"),
            {"b": new_balance, "id": customer_id},
        )
        conn.execute(
            text(
                """
                INSERT INTO ledger (customer_id, delta, reason, ref_id, created_at)
                VALUES (:customer_id, :delta, :reason, :ref, now())
                """
            ),
            {"customer_id": customer_id, "delta": body.delta, "reason": body.reason, "ref": body.ref},
        )

    return {"ok": True, "customer_id": customer_id, "balance": new_balance}


@router.get("/wallet/{customer_id}")
async def get_wallet(customer_id: int) -> dict:
    engine = get_engine()
    with engine.connect() as conn:
        customer = _fetch_customer_row(conn, customer_id)
        if not customer:
            raise HTTPException(status_code=404, detail="عميل غير موجود")
        ledger_rows = conn.execute(
            text(
                """
                SELECT id, delta, reason, ref_id, created_at FROM ledger
                WHERE customer_id = :id ORDER BY id DESC LIMIT 20
                """
            ),
            {"id": customer_id},
        ).mappings().all()
    return {
        "customer_id": customer_id,
        "balance": customer["wallet_balance"],
        "ledger": [dict(r) for r in ledger_rows],
    }


# ---------------------------------------------------------------------------
# subscriptions — شراء منتج: order + ledger(grant) + (اشتراك فعلي إن كان
# type='subscription') بنفس المعاملة (الدليل §3.12: "شراء = orders +
# ledger(grant). الاشتراك = subscriptions أيضًا"). standalone_cv لا يمنح
# رصيدًا (خدمة منفصلة عن اقتصاد التقديمات، الدليل §4.10).
# ---------------------------------------------------------------------------


class SubscriptionCreateRequest(BaseModel):
    customer_id: int
    product_code: str


@router.post("/subscriptions")
async def create_subscription(body: SubscriptionCreateRequest) -> dict:
    engine = get_engine()
    with engine.begin() as conn:
        if not _fetch_customer_row(conn, body.customer_id):
            raise HTTPException(status_code=404, detail="عميل غير موجود")

        product = conn.execute(
            text("SELECT * FROM products WHERE code = :code AND active = true"), {"code": body.product_code}
        ).mappings().first()
        if not product:
            raise HTTPException(status_code=404, detail="منتج غير موجود أو غير مُفعّل")

        order_row = conn.execute(
            text(
                """
                INSERT INTO orders (customer_id, product_code, amount_sar, status)
                VALUES (:cid, :code, :amount, 'paid') RETURNING id
                """
            ),
            {"cid": body.customer_id, "code": product["code"], "amount": product["price_sar"]},
        ).first()
        order_id = order_row[0]

        subscription_id = None
        new_balance = None
        credits = product["credits"]
        if credits:
            row = conn.execute(
                text("SELECT balance FROM wallets WHERE customer_id = :id FOR UPDATE"), {"id": body.customer_id}
            ).first()
            current = row[0] if row else 0
            if row is None:
                conn.execute(
                    text("INSERT INTO wallets (customer_id, balance) VALUES (:id, 0)"), {"id": body.customer_id}
                )
            new_balance = current + credits
            conn.execute(
                text("UPDATE wallets SET balance = :b, updated_at = now() WHERE customer_id = :id"),
                {"b": new_balance, "id": body.customer_id},
            )
            conn.execute(
                text(
                    """
                    INSERT INTO ledger (customer_id, delta, reason, ref_id, created_at)
                    VALUES (:cid, :delta, 'purchase', :ref, now())
                    """
                ),
                {"cid": body.customer_id, "delta": credits, "ref": f"order:{order_id}"},
            )

        if product["type"] == "subscription":
            days = product["days"] or 30
            starts_at = datetime.now(timezone.utc)
            ends_at = starts_at + timedelta(days=days)
            sub_row = conn.execute(
                text(
                    """
                    INSERT INTO subscriptions (customer_id, product_code, starts_at, ends_at, daily_target, status)
                    VALUES (:cid, :code, :starts, :ends, :target, 'active') RETURNING id
                    """
                ),
                {
                    "cid": body.customer_id,
                    "code": product["code"],
                    "starts": starts_at,
                    "ends": ends_at,
                    "target": planner.DEFAULT_TARGET_DAILY,
                },
            ).first()
            subscription_id = sub_row[0]

    return {
        "ok": True,
        "order_id": order_id,
        "subscription_id": subscription_id,
        "wallet_balance": new_balance,
    }


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------


@router.get("/plan/{customer_id}/today")
async def plan_today(customer_id: int, day: date | None = Query(default=None)) -> dict:
    engine = get_engine()
    target_day = day or (planner.now_riyadh().date())
    with engine.connect() as conn:
        if not _fetch_customer_row(conn, customer_id):
            raise HTTPException(status_code=404, detail="عميل غير موجود")
        rows = conn.execute(
            text(
                """
                SELECT o.id, o.job_id, o.score, o.tier, o.reasons, o.status, o.planned_for,
                       j.title, j.company_name, j.city, j.family, j.url
                FROM opportunities o JOIN jobs j ON j.id = o.job_id
                WHERE o.customer_id = :cid AND o.planned_for = :day
                ORDER BY o.score DESC
                """
            ),
            {"cid": customer_id, "day": target_day},
        ).mappings().all()
    return {
        "customer_id": customer_id,
        "date": target_day.isoformat(),
        "count": len(rows),
        "opportunities": [dict(r) for r in rows],
    }


@router.post("/plan/run-now")
async def plan_run_now(background_tasks: BackgroundTasks, customer_id: int | None = Query(default=None)) -> dict:
    if customer_id is not None:
        result = planner.run_plan_round(customer_ids=[customer_id])
        return {"ok": True, "sync": True, "result": result}
    background_tasks.add_task(planner.run_plan_round)
    return {"ok": True, "started": True}


# ---------------------------------------------------------------------------
# admin/matching/explain — شرح درجة/استبعاد زوج (عميل، وظيفة) واحد فقط، حيًا
# (لا يمر عبر مسار المخطّط الدفعي — استعلامات مفردة، مقبولة لأداة تشخيص).
# ---------------------------------------------------------------------------


@router.get("/admin/matching/explain")
async def matching_explain(customer_id: int = Query(...), job_id: int = Query(...)) -> dict:
    engine = get_engine()
    with engine.connect() as conn:
        customer_row = conn.execute(
            text(
                """
                SELECT c.id, c.cities, c.families, p.years_exp, p.seniority, p.nationality_saudi,
                       p.titles, p.skills, p.degree
                FROM customers c JOIN profiles p ON p.customer_id = c.id
                WHERE c.id = :id
                """
            ),
            {"id": customer_id},
        ).mappings().first()
        if not customer_row:
            raise HTTPException(status_code=404, detail="عميل أو ملف شخصي غير موجود")

        job_row = conn.execute(
            text(
                """
                SELECT id AS job_id, title, family, years_min, seniority, saudi_only, city, skills,
                       company_name, apply_mode, first_seen_at, out_of_region
                FROM jobs WHERE id = :id
                """
            ),
            {"id": job_id},
        ).mappings().first()
        if not job_row:
            raise HTTPException(status_code=404, detail="وظيفة غير موجودة")

        now = planner.now_riyadh()
        ck = normalize_company_key(job_row.get("company_name"))
        cooldown_pairs = planner.fetch_cooldown_pairs(conn, now)
        weekly_cap_companies = planner.fetch_weekly_cap_companies(conn, now)

    profile = planner.row_to_profile(dict(customer_row))
    job = planner.row_to_job(dict(job_row))
    job.out_of_region = bool(job_row.get("out_of_region"))

    widened_family = bool(job.family) and job.family not in profile.families
    result = matching.evaluate(
        profile,
        job,
        now=now,
        widened_family=widened_family,
        cooldown_active=bool(ck) and (customer_id, ck) in cooldown_pairs,
        weekly_cap_reached=bool(ck) and ck in weekly_cap_companies,
    )

    return {
        "customer_id": customer_id,
        "job_id": job_id,
        "disqualified": result.disqualified,
        "reasons": result.reasons,
        "score": result.score,
        "score_parts": result.score_parts,
        "tier": result.tier,
        "widened_family_pass": widened_family,
    }
