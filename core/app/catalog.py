"""
Masar Core — كتالوج المنتجات وطلبات الأدمن (B7، الدليل §3.12، §4.10، §9
المرحلة 6). يبني فوق جداول `products`/`orders`/`wallets`/`ledger`/
`subscriptions` **الموجودة فعلًا منذ B3** (migration 0004) — لا يُنشئ جداولًا
جديدة (راجع docstring migrations/versions/0012_b7_catalog.py لتفاصيل
التوسعة). `POST /subscriptions` بـcustomers_api.py يبقى كما هو بلا لمس —
هذا الملف يضيف مسارًا إداريًا موازيًا أوضح تسمية (`/admin/orders`) بمفردات
حالة أدق (pending/active/fulfilled/cancelled بدل paid فقط) دون كسره.

    GET  /catalog                  المنتجات النشطة (active=true) — بلا تعديل
    POST /admin/orders             ينشئ طلبًا ويُفعّله فورًا حسب نوع المنتج:
                                    subscription → يمنح رصيدًا (إن وُجد) وينشئ
                                    صفّ subscriptions (starts_at/ends_at التي
                                    يقرأها guarantee.py) ويحدّث orders لـ'active'؛
                                    credits → يمنح الرصيد فقط، orders→'fulfilled'؛
                                    cv_standalone → يسجّل الطلب بحالة 'pending'
                                    مع note ("بانتظار توليد السيرة الذاتية") —
                                    التوليد الفعلي عبر cv_builder.ensure_cv_variant
                                    يحدث لاحقًا (يحتاج ملفًا مؤكّدًا + Gotenberg
                                    حيًّا، خارج نطاق هذه النقطة المتزامنة عمدًا).
    GET  /admin/orders?customer_id=  طلبات عميل واحد، الأحدث أولًا

**PRICE_TBD (قاعدة B7 غير قابلة للتفاوض):** الأسعار مؤقتة حتى يقرر أحمد —
مكانها الوحيد بالكود هو الثابت `PRICE_TBD` أدناه (يُستخدم فقط للعرض/التوثيق؛
القاعدة الفعلية هي `products.price_sar IS NULL` = "لم يُحدّد بعد". لا يظهر
أي سعر/سقف/تقدير لأي عميل من أي نقطة هنا — هذه كلها نقاط أدمن محمية بالتوكن.

**B10 — wallet_topup (شحن محفظة):** نوع منتج جديد (`products.type='wallet_topup'`،
صفّ وحيد بترحيلة 0021 برمز `WALLET`) — خلاف subscription/credits، مبلغه
متغيّر يحدّده العميل وقت الطلب لا سعر ثابت بـ`products.price_sar` (يبقى
NULL لهذا الصفّ دومًا)؛ لذلك `AdminOrderCreateRequest.amount_sar` مطلوب
لهذا النوع تحديدًا (يمرّره `telegram_admin_payments.decide_payment` من
`payment_requests.declared_amount` المؤكّد). التفعيل هنا لا يمنح رصيد
`wallets`/`ledger` التقليدي ولا ينشئ `subscriptions` — يضيف المبلغ مباشرة
لـ`customers.wallet_balance_sar` عبر `app.wallet.apply_wallet_delta_conn`
(نفس معاملة إنشاء الطلب) ويضبط `customers.billing_mode='wallet'` (أول شحن
محفظة يحوّل العميل لهذا النظام دائمًا — راجع docstring `app.wallet`)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app import planner, wallet
from app.auth import require_admin_token
from app.discovery import get_engine
from sqlalchemy import text

router = APIRouter(tags=["catalog"], dependencies=[Depends(require_admin_token)])

# المكان الوحيد بالكود لثابت "السعر لم يُحدّد بعد" (قاعدة B7 غير قابلة
# للتفاوض) — لا تُكتب "TBD"/"قريبًا" في أي مكان آخر؛ الاستعلام دائمًا عبر
# `price_sar IS NULL` على مستوى القاعدة، وهذا الثابت للعرض النصي فقط.
PRICE_TBD = None
PRICE_TBD_LABEL_AR = "يُحدّد لاحقًا"

DEFAULT_SUBSCRIPTION_DAYS = 30
ORDER_STATUS_VALUES = {"pending", "active", "fulfilled", "cancelled"}


# ---------------------------------------------------------------------------
# GET /catalog
# ---------------------------------------------------------------------------


@router.get("/catalog")
async def list_catalog() -> dict:
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT code, name_ar, type AS kind, applications_included, days,
                       price_sar, active
                FROM products
                WHERE active = true
                ORDER BY
                    CASE type WHEN 'subscription' THEN 0 WHEN 'credits' THEN 1 ELSE 2 END,
                    code
                """
            )
        ).mappings().all()

    items = []
    for r in rows:
        item = dict(r)
        item["price_sar"] = float(item["price_sar"]) if item["price_sar"] is not None else PRICE_TBD
        item["price_label_ar"] = (
            PRICE_TBD_LABEL_AR if item["price_sar"] is None else f"{item['price_sar']:.2f} ريال"
        )
        items.append(item)
    return {"items": items, "count": len(items)}


# ---------------------------------------------------------------------------
# POST /admin/orders
# ---------------------------------------------------------------------------


class AdminOrderCreateRequest(BaseModel):
    customer_id: int
    product_code: str
    starts_at: datetime | None = None
    # B10: مطلوب فقط لـproduct.type == 'wallet_topup' (مبلغ متغيّر، لا سعر
    # ثابت بـproducts.price_sar) — يُتجاهَل تمامًا لأي نوع منتج آخر.
    amount_sar: float | None = None


def _fetch_customer(conn, customer_id: int) -> dict | None:
    row = conn.execute(
        text("SELECT id, name, status, price_sar FROM customers WHERE id = :id"), {"id": customer_id}
    ).mappings().first()
    return dict(row) if row else None


def _fetch_active_product(conn, product_code: str) -> dict | None:
    row = conn.execute(
        text(
            "SELECT code, name_ar, type AS kind, applications_included, days, price_sar, active "
            "FROM products WHERE code = :code AND active = true"
        ),
        {"code": product_code},
    ).mappings().first()
    return dict(row) if row else None


def _grant_wallet_credits(conn, customer_id: int, delta: int, ref: str) -> int:
    """يمنح رصيدًا بنفس معاملة الاستدعاء (نمط customers_api.wallet_credit —
    قيد CHECK balance>=0 على مستوى القاعدة شبكة أمان إضافية). يُستخدَم هنا
    فقط بقيم موجبة (منح شراء) فلا يمكن أن يخرق القيد عمليًا، لكن الفحص يبقى
    دفاعًا بالعمق مطابقًا للنمط القائم."""
    row = conn.execute(
        text("SELECT balance FROM wallets WHERE customer_id = :id FOR UPDATE"), {"id": customer_id}
    ).first()
    current = row[0] if row else 0
    if row is None:
        conn.execute(text("INSERT INTO wallets (customer_id, balance) VALUES (:id, 0)"), {"id": customer_id})
    new_balance = current + delta
    if new_balance < 0:
        raise HTTPException(status_code=400, detail="الرصيد لا يمكن أن يصبح سالبًا")
    conn.execute(
        text("UPDATE wallets SET balance = :b, updated_at = now() WHERE customer_id = :id"),
        {"b": new_balance, "id": customer_id},
    )
    conn.execute(
        text(
            "INSERT INTO ledger (customer_id, delta, reason, ref_id, created_at) "
            "VALUES (:cid, :delta, 'purchase', :ref, now())"
        ),
        {"cid": customer_id, "delta": delta, "ref": ref},
    )
    return new_balance


@router.post("/admin/orders")
async def create_order(body: AdminOrderCreateRequest) -> dict:
    engine = get_engine()
    with engine.begin() as conn:
        customer = _fetch_customer(conn, body.customer_id)
        if not customer:
            raise HTTPException(status_code=404, detail="عميل غير موجود")

        product = _fetch_active_product(conn, body.product_code)
        if not product:
            raise HTTPException(status_code=404, detail="منتج غير موجود أو غير مُفعّل")

        starts_at = body.starts_at or datetime.now(timezone.utc)
        if starts_at.tzinfo is None:
            starts_at = starts_at.replace(tzinfo=timezone.utc)

        if product["kind"] == "wallet_topup":
            if body.amount_sar is None or body.amount_sar <= 0:
                raise HTTPException(status_code=400, detail="amount_sar مطلوب وموجب لطلبات شحن المحفظة")
            order_amount = body.amount_sar
        else:
            order_amount = product["price_sar"]

        order_row = conn.execute(
            text(
                """
                INSERT INTO orders (customer_id, product_code, amount_sar, status, starts_at, note, created_at)
                VALUES (:cid, :code, :amount, 'pending', :starts, :note, now())
                RETURNING id
                """
            ),
            {
                "cid": body.customer_id,
                "code": product["code"],
                "amount": order_amount,
                "starts": starts_at,
                "note": None,
            },
        ).first()
        order_id = order_row[0]

        subscription_id: int | None = None
        wallet_balance: int | None = None
        wallet_balance_sar: Decimal | None = None
        credits_granted: int | None = None
        ends_at: datetime | None = None
        note: str | None = None
        new_status = "pending"

        if product["kind"] == "subscription":
            days = product["days"] or DEFAULT_SUBSCRIPTION_DAYS
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

            if product["applications_included"]:
                credits_granted = product["applications_included"]
                wallet_balance = _grant_wallet_credits(
                    conn, body.customer_id, credits_granted, ref=f"order:{order_id}"
                )

            # يُثبَّت سعر الفترة على العميل فقط إن كان معروفًا (ليس TBD) —
            # guarantee.py يقرأه لاحقًا لحساب التعويض التناسبي؛ يبقى NULL
            # بأمان (يتعامل معه `if price_sar is not None`) طالما لم يقرر
            # أحمد السعر بعد.
            if product["price_sar"] is not None:
                conn.execute(
                    text("UPDATE customers SET price_sar = :p, updated_at = now() WHERE id = :id"),
                    {"p": product["price_sar"], "id": body.customer_id},
                )

            new_status = "active"

        elif product["kind"] == "credits":
            credits_granted = product["applications_included"]
            if credits_granted:
                wallet_balance = _grant_wallet_credits(
                    conn, body.customer_id, credits_granted, ref=f"order:{order_id}"
                )
            new_status = "fulfilled"

        elif product["kind"] == "wallet_topup":
            # B10: يضيف المبلغ المؤكّد مباشرة لـwallet_balance_sar (سجلّ
            # تدقيق wallet_transactions بنفس المعاملة عبر apply_wallet_delta_conn)
            # ويحوّل العميل لـbilling_mode='wallet' — أول شحن محفظة يُفعّل هذا
            # النظام دائمًا (idempotent: تحديث بلا شرط، لا أثر لو كان مفعّلًا
            # أصلًا). لا صلة إطلاقًا بـwallets/ledger التقليديين أعلاه.
            wallet_balance_sar = wallet.apply_wallet_delta_conn(
                conn, body.customer_id, Decimal(str(order_amount)), "topup", reason=f"order:{order_id}"
            )
            conn.execute(
                text("UPDATE customers SET billing_mode = 'wallet', updated_at = now() WHERE id = :id"),
                {"id": body.customer_id},
            )
            new_status = "fulfilled"

        else:  # standalone_cv
            note = "بانتظار توليد السيرة الذاتية"
            new_status = "pending"

        conn.execute(
            text(
                """
                UPDATE orders SET status = :status, ends_at = :ends, credits_granted = :credits, note = :note
                WHERE id = :id
                """
            ),
            {
                "status": new_status,
                "ends": ends_at,
                "credits": credits_granted,
                "note": note,
                "id": order_id,
            },
        )

    return {
        "ok": True,
        "order_id": order_id,
        "status": new_status,
        "product_code": product["code"],
        "kind": product["kind"],
        "subscription_id": subscription_id,
        "credits_granted": credits_granted,
        "wallet_balance": wallet_balance,
        "wallet_balance_sar": float(wallet_balance_sar) if wallet_balance_sar is not None else None,
        "note": note,
    }


# ---------------------------------------------------------------------------
# GET /admin/orders?customer_id=
# ---------------------------------------------------------------------------


@router.get("/admin/orders")
async def list_orders(customer_id: int = Query(...)) -> dict:
    engine = get_engine()
    with engine.connect() as conn:
        if not _fetch_customer(conn, customer_id):
            raise HTTPException(status_code=404, detail="عميل غير موجود")
        rows = conn.execute(
            text(
                """
                SELECT id, customer_id, product_code, amount_sar, status, starts_at, ends_at,
                       credits_granted, note, created_at
                FROM orders
                WHERE customer_id = :cid
                ORDER BY id DESC
                """
            ),
            {"cid": customer_id},
        ).mappings().all()

    items = []
    for r in rows:
        item = dict(r)
        item["amount_sar"] = float(item["amount_sar"]) if item["amount_sar"] is not None else PRICE_TBD
        items.append(item)
    return {"customer_id": customer_id, "items": items, "count": len(items)}
