"""
Masar Core — نقاط نهاية التقرير اليومي (B5a البند 1). كل النقاط محمية بتوكن
الإدارة (نفس نمط bقية core/app/*_api.py).

    GET  /admin/reports/pending          صفوف status='queued' (payload+text)، مُصَفّحة
    POST /admin/reports/{id}/delivered   {channel} → status='delivered'
    POST /admin/reports/run              {date?} → تشغيل يدوي فوري (مزامن)
    GET  /customers/{id}/report?date=    تقرير عميل واحد (من القاعدة إن وُجد،
                                          وإلا يُبنى حيًّا للمعاينة بلا تخزين)
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from app import reports
from app.auth import require_admin_token
from app.discovery import get_engine
from app.pacing import to_riyadh_naive, utc_now

router = APIRouter(dependencies=[Depends(require_admin_token)])
admin_router = APIRouter(prefix="/admin/reports", tags=["reports-admin"])
customer_router = APIRouter(prefix="/customers", tags=["reports"])

DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 200


@admin_router.get("/pending")
async def pending_reports(
    limit: int = Query(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    after_id: int = Query(default=0, ge=0),
) -> dict:
    """مُصَفّحة بمؤشّر (cursor) بسيط على id (ix_daily_reports_status +
    ترتيب id يخدم صفحة تالية بلا OFFSET مكلف عند نمو الجدول)."""
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id, customer_id, report_date, payload, status, channel, created_at, delivered_at
                FROM daily_reports
                WHERE status = 'queued' AND id > :after_id
                ORDER BY id
                LIMIT :limit
                """
            ),
            {"after_id": after_id, "limit": limit},
        ).mappings().all()
    items = [dict(r) for r in rows]
    next_after_id = items[-1]["id"] if len(items) == limit else None
    return {"items": items, "count": len(items), "next_after_id": next_after_id}


class ReportDeliveredRequest(BaseModel):
    channel: str


@admin_router.post("/{report_id}/delivered")
async def mark_delivered(report_id: int, body: ReportDeliveredRequest) -> dict:
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                UPDATE daily_reports SET status = 'delivered', delivered_at = now(), channel = :channel
                WHERE id = :id
                RETURNING id, status
                """
            ),
            {"id": report_id, "channel": body.channel},
        ).first()
    if not row:
        raise HTTPException(status_code=404, detail="تقرير غير موجود")
    return {"ok": True, "id": report_id, "status": row[1]}


class RunReportsRequest(BaseModel):
    # الحقل مُسمَّى report_date داخليًا (لا `date`) عمدًا — Pydantic v2 مع
    # `from __future__ import annotations` (PEP 563) يفشل بخطأ تقييم غامض
    # حين يحمل حقل نفس اسم نوعه (`date: date | None`، تعارض تقييم forward-ref
    # مؤكّد تجريبيًا بهذا الملف) — alias="date" يُبقي شكل الطلب الخارجي
    # {"date": "..."} كما يصفه التكليف حرفيًا بلا أي تغيير على المستدعي.
    report_date: date | None = Field(default=None, alias="date")

    model_config = {"populate_by_name": True}


@admin_router.post("/run")
async def run_reports(body: RunReportsRequest | None = None) -> dict:
    """تشغيل مزامن (مباشر، بلا BackgroundTasks) عمدًا — يُستخدم للتحقق
    الفوري (مثال: بعد النشر الحي) حيث يحتاج المستدعي النتيجة فورًا، وحجم
    العملاء النشطين الحالي صغير بما يكفي لبقاء الاستجابة سريعة؛ إن كبر
    القاعدة لاحقًا (B6، 1,500 عميل) يمكن تحويلها لـBackgroundTasks بسهولة
    (نفس نمط /admin/discovery/run-now) بلا تغيير عقد الاستدعاء الخارجي —
    ليس ضروريًا الآن (الدليل: مهمة التقرير خفيفة — قراءات مفهرسة فقط)."""
    target_date = (body.report_date if body else None) or to_riyadh_naive(utc_now()).date()
    result = reports.run_reports_round(report_date=target_date)
    return result


@customer_router.get("/{customer_id}/report")
async def customer_report(customer_id: int, date: date | None = Query(default=None)) -> dict:
    target_date = date or to_riyadh_naive(utc_now()).date()
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT id, customer_id, report_date, payload, status, channel, created_at, delivered_at
                FROM daily_reports WHERE customer_id = :cid AND report_date = :d
                """
            ),
            {"cid": customer_id, "d": target_date},
        ).mappings().first()
    if row:
        return {"stored": True, **dict(row)}

    payload = reports.build_customer_report(customer_id, target_date, engine=engine)
    if payload is None:
        raise HTTPException(status_code=404, detail="عميل غير موجود")
    return {"stored": False, "customer_id": customer_id, "report_date": target_date.isoformat(), "payload": payload}


router.include_router(admin_router)
router.include_router(customer_router)
