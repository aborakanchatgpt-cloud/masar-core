"""
Masar Core — الخدمة الأساسية الجديدة (المرحلة 1: هيكل فقط + /health)

هذا الملف عمدًا بسيط بالمرحلة 1 — الهدف هو إثبات أن الخادم يعمل ويمكن الوصول
له عبر HTTPS، وأن n8n يقدر يستدعيه بنجاح. الوحدات الفعلية (identity, billing,
profile, discovery, taxonomy, matching, planning, sending, inbox, reporting,
bridge) تُبنى بالمراحل 2-6 حسب دليل مسار v5.

B1b: أُضيف جسر MCP (app/mcp_bridge.py) — يجعل جلسات Claude مستقلة عن n8n
Cloud لعمليات القراءة/الكتابة بالمستودع وتشغيل أوامر المضيف.
"""
import os
from datetime import datetime, timezone

from fastapi import Depends, FastAPI
from pydantic import BaseModel

from app.auth import require_admin_token
from app.mcp_bridge import router as mcp_router
from app.ops import router as ops_router

app = FastAPI(
    title="Masar Core",
    description="الخدمة الأساسية الجديدة لنظام مسار — تحل تدريجيًا محل منطق n8n/Claude Code Remote",
    version="0.1.0",
)

app.include_router(ops_router)
app.include_router(mcp_router)


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    timestamp: str


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """نقطة فحص الصحة — تُستخدم من UptimeRobot ومن n8n (Core - Call) للتأكد أن الخدمة حية."""
    return HealthResponse(
        status="ok",
        service="masar-core",
        version=app.version,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


@app.get("/")
async def root() -> dict:
    return {"service": "masar-core", "docs": "/docs", "health": "/health"}


@app.get("/admin/ping", dependencies=[Depends(require_admin_token)])
async def admin_ping() -> dict:
    """أول نقطة نهاية محمية بـ CORE_ADMIN_TOKEN — تُستخدم للتحقق من صحة
    الإعداد فور النشر (curl -H "Authorization: Bearer <token>" .../admin/ping)
    قبل بناء نقاط الجسر الفعلية بالمراحل القادمة."""
    return {"ok": True, "service": "masar-core"}


@app.get("/admin/mcp-url", dependencies=[Depends(require_admin_token)])
async def admin_mcp_url() -> dict:
    """يُرجع رابط جسر MCP الكامل (مع التوكن) لتسهيل إعداد Custom Connector في
    Claude — أو {"enabled": false} إن لم يُولّد MCP_BRIDGE_TOKEN بعد على المضيف
    (انظر deploy/autodeploy.sh)."""
    token = os.environ.get("MCP_BRIDGE_TOKEN", "")
    if not token:
        return {"enabled": False}
    domain = os.environ.get("MASAR_DOMAIN", "")
    return {"url": f"https://{domain}/mcp/{token}"}
