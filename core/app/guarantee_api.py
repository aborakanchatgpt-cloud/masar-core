"""
Masar Core — نقاط نهاية دفتر الضمان/التعويض (B5a البند 3).

    GET  /admin/guarantee/pending    صفوف status='refund_pending'، مُصَفّحة
    POST /admin/guarantee/{id}/settle  → status='settled' (اعتماد/تحويل يدوي
                                        من المالك — لا تحويل تلقائي أبدًا)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text

from app.auth import require_admin_token
from app.discovery import get_engine

router = APIRouter(prefix="/admin/guarantee", tags=["guarantee"], dependencies=[Depends(require_admin_token)])

DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 200


@router.get("/pending")
async def pending_guarantees(
    limit: int = Query(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    after_id: int = Query(default=0, ge=0),
) -> dict:
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT g.id, g.customer_id, c.name AS customer_name, g.period_start, g.period_end,
                       g.target, g.counted_sent, g.bounced, g.shortfall, g.extension_days,
                       g.refund_amount, g.status, g.created_at, g.updated_at
                FROM guarantee_ledger g JOIN customers c ON c.id = g.customer_id
                WHERE g.status = 'refund_pending' AND g.id > :after_id
                ORDER BY g.id
                LIMIT :limit
                """
            ),
            {"after_id": after_id, "limit": limit},
        ).mappings().all()
    items = [dict(r) for r in rows]
    next_after_id = items[-1]["id"] if len(items) == limit else None
    return {"items": items, "count": len(items), "next_after_id": next_after_id}


@router.post("/{ledger_id}/settle")
async def settle_guarantee(ledger_id: int) -> dict:
    """يُعلَّم كمُسدّد بعد أن يحوّل المالك (أحمد) المبلغ يدويًا — idempotent:
    استدعاء ثانٍ على صفّ 'settled' أصلًا لا يرفع خطأًا (200 no-op)."""
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT id, status FROM guarantee_ledger WHERE id = :id"), {"id": ledger_id}
        ).mappings().first()
        if not row:
            raise HTTPException(status_code=404, detail="سجلّ ضمان غير موجود")

        if row["status"] == "settled":
            return {"ok": True, "id": ledger_id, "status": "settled", "already_settled": True}

        if row["status"] != "refund_pending":
            raise HTTPException(
                status_code=400,
                detail=f"لا يمكن تسوية سجلّ بحالة '{row['status']}' — يجب أن يكون 'refund_pending'",
            )

        conn.execute(
            text("UPDATE guarantee_ledger SET status = 'settled', updated_at = now() WHERE id = :id"),
            {"id": ledger_id},
        )
    return {"ok": True, "id": ledger_id, "status": "settled", "already_settled": False}
