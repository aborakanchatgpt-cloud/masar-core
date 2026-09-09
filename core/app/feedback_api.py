"""
Masar Core — تغذية العميل الراجعة على تقديم محدَّد (B5a البند 2، الدليل §3.7
"التعلّم": "👎 ... → blocklist"، §3.13: أزرار 👎/🎉 لكل تقديم).

    POST /customers/{id}/feedback  {send_queue_id, kind, note?}
        kind='thumbs_down' → يُسجَّل application_feedback **و** يُستبعَد
            company_id هذه الوظيفة من كل مطابقة مستقبلية لهذا العميل
            (customer_company_exclusions — يقرأه send_builder.py قبل بناء
            الطابور، راجع تعليق _fetch_candidate_opportunities هناك).
        kind='celebrate' → يُسجَّل فقط (لا استبعاد، لا أثر جانبي آخر).
        **idempotent**: تكرار نفس (customer_id, send_queue_id, kind) لا يرفع
        خطأًا — 200 {"ok": true, "already_recorded": true} بلا إدراج مكرّر
        ولا استبعاد شركة مكرّر (ON CONFLICT DO NOTHING على كلا الجدولين).

لا يلمس core/app/customers_api.py (ملف B3 WIP بانتظار مراجعة قبول منفصلة)
— راوتر مستقل بادئته أيضًا `/customers/{id}` لكن بمسار فرعي مختلف كليًا
(`/feedback`)، بلا أي تصادم مسار مع customers_api.py (`/customers/{id}`،
`/customers/{id}/profile`).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth import require_admin_token
from app.discovery import get_engine
from sqlalchemy import text

logger = logging.getLogger("masar.feedback_api")

router = APIRouter(prefix="/customers", tags=["feedback"], dependencies=[Depends(require_admin_token)])

FEEDBACK_KINDS = {"thumbs_down", "celebrate"}


class FeedbackRequest(BaseModel):
    send_queue_id: int
    kind: str
    note: str | None = None


@router.post("/{customer_id}/feedback")
async def submit_feedback(customer_id: int, body: FeedbackRequest) -> dict:
    if body.kind not in FEEDBACK_KINDS:
        raise HTTPException(status_code=400, detail=f"kind غير معروف: {body.kind}")

    engine = get_engine()
    with engine.begin() as conn:
        customer = conn.execute(text("SELECT id FROM customers WHERE id = :id"), {"id": customer_id}).first()
        if not customer:
            raise HTTPException(status_code=404, detail="عميل غير موجود")

        sq_row = conn.execute(
            text("SELECT id, customer_id, job_id FROM send_queue WHERE id = :id"), {"id": body.send_queue_id}
        ).mappings().first()
        if not sq_row or sq_row["customer_id"] != customer_id:
            raise HTTPException(status_code=404, detail="لا يوجد تقديم بهذا المعرّف لهذا العميل")

        inserted = conn.execute(
            text(
                """
                INSERT INTO application_feedback (customer_id, send_queue_id, kind, note, created_at)
                VALUES (:cid, :sqid, :kind, :note, now())
                ON CONFLICT (customer_id, send_queue_id, kind) DO NOTHING
                RETURNING id
                """
            ),
            {"cid": customer_id, "sqid": body.send_queue_id, "kind": body.kind, "note": body.note},
        ).first()

        if inserted is None:
            # مُسجَّلة أصلًا (idempotent) — لا إعادة استبعاد، لا خطأ.
            return {"ok": True, "already_recorded": True}

        excluded_company_id = None
        if body.kind == "thumbs_down" and sq_row.get("job_id") is not None:
            company_row = conn.execute(
                text("SELECT company_id FROM jobs WHERE id = :id"), {"id": sq_row["job_id"]}
            ).first()
            company_id = company_row[0] if company_row else None
            if company_id:
                conn.execute(
                    text(
                        """
                        INSERT INTO customer_company_exclusions (customer_id, company_id, reason, created_at)
                        VALUES (:cid, :company_id, 'customer_feedback_thumbs_down', now())
                        ON CONFLICT (customer_id, company_id) DO NOTHING
                        """
                    ),
                    {"cid": customer_id, "company_id": company_id},
                )
                excluded_company_id = company_id

    return {
        "ok": True,
        "already_recorded": False,
        "feedback_id": inserted[0],
        "company_excluded_id": excluded_company_id,
    }
