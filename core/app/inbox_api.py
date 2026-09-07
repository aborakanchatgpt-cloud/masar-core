"""
Masar Core — نقاط نهاية الوارد (B4): استعراض أحداث الوارد المصنّفة لعميل،
وتشغيل جولة قراءة فورية يدويًا.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text

from app import inbox
from app.auth import require_admin_token
from app.discovery import get_engine

router = APIRouter(tags=["inbox"], dependencies=[Depends(require_admin_token)])


@router.get("/inbox/{customer_id}/events")
async def list_inbox_events(customer_id: int, limit: int = Query(default=50, ge=1, le=200)) -> list[dict]:
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id, message_id, in_reply_to, kind, from_addr, subject, snippet, created_at
                FROM inbox_events WHERE customer_id = :cid
                ORDER BY id DESC LIMIT :limit
                """
            ),
            {"cid": customer_id, "limit": limit},
        ).mappings().all()
    return [dict(r) for r in rows]


@router.post("/inbox/run-now")
async def run_inbox_now(customer_id: int | None = None) -> dict:
    customer_ids = [customer_id] if customer_id is not None else None
    return inbox.run_inbox_round(customer_ids=customer_ids)
