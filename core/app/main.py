"""
Masar Core — الخدمة الأساسية.

المرحلة 1: هيكل + /health. المرحلة 2 (B2): وحدة discovery الحقيقية عبر
discovery_api.py (نقاط /admin/sources, /admin/stats, /admin/discovery/*,
/admin/quality-sample) — انظر core/app/discovery.py للمنطق الكامل.

B3 (المرحلة 3): وحدة العملاء/الملف الشخصي/المحفظة/المطابقة/الخطة عبر
customers_api.py (core/app/matching.py + core/app/planner.py للمنطق الكامل).

الوحدات الأخرى (sending, inbox — B4) تُبنى بالتوازي على فروع/ملفات منفصلة
(core/app/mail_api.py، core/app/inbox_api.py، core/app/mail*، core/app/collectors/
اللاحقة) ولا تُلمَس من هنا؛ تُضمّن أدناه بـimport محروس (try/except) حتى
يستطيع منفّذ B4 دفع ملفاته لاحقًا بلا الحاجة لتعديل main.py نفسه إطلاقًا —
غياب الملفات الآن أمر متوقّع وطبيعي (لا يعطّل main.py: كل مسار غير موجود
يُتخطّى بصمت بالسجلّ فقط).

B1b: أُضيف جسر MCP (app/mcp_bridge.py) — يجعل جلسات Claude مستقلة عن n8n
Cloud لعمليات القراءة/الكتابة بالمستودع وتشغيل أوامر المضيف.

B8: إزالة n8n نهائيًا من تفاعل تيليجرام بالكامل (تيّاران دُمجا هنا):
    - الشقّ الوارد: app/telegram_api.py (راوتر POST /telegram/webhook/{token})
      يستبدل بالكامل تدفّقات n8n القديمة لبوت العملاء "مسار" وبوت الأدمن
      الخاص (app/telegram_onboarding.py وapp/telegram_admin.py على
      التوالي، فوق app/telegram_client.py). مُدرج ضمن حلقة الاستيراد
      المحروسة أدناه (نفس بقية وحدات B4+) لأنه يعتمد على app.reports_api/
      overview_api/guarantee_api/link_api/feedback_api التي قد لا تكون
      موجودة بعد بأي فرع مبكر — غيابه الآن ImportError متوقع، يُسجّل
      معلوماتيًا فقط.
    - الشقّ الصادر: app/telegram_notify_admin.py.notify_admin — تنبيه أحمد
      فورًا عبر تيليجرام مباشرة عند فشل تحميل أي راوتر بخطأ غير متوقّع عند
      الإقلاع (بديل مباشر لجسر core-notify-admin بـn8n، راجع تعليق الحلقة
      أدناه — سيناريو "B4 hotfix" الموثّق).
"""
import logging
import os
from datetime import datetime, timezone

from fastapi import Depends, FastAPI
from pydantic import BaseModel

from app.auth import require_admin_token
from app.customers_api import router as customers_router
from app.discovery_api import router as discovery_router
from app.mcp_bridge import router as mcp_router
from app.ops import router as ops_router
from app.telegram_notify_admin import notify_admin

logger = logging.getLogger("masar.main")

app = FastAPI(
    title="Masar Core",
    description="الخدمة الأساسية الجديدة لنظام مسار — تحل تدريجيًا محل منطق n8n/Claude Code Remote",
    version="0.3.0",
)

app.include_router(ops_router)
app.include_router(mcp_router)
app.include_router(discovery_router)
app.include_router(customers_router)

# B4 (يعمل بالتوازي على core/app/mail_api.pyوcore/app/inbox_api.py — منفّذ
# آخر يملك هذين الملفين، لا نلمسهما من هنا): إدراج محروس بحيث يبدأ عملهما
# فور دفع ملفاتهما بلا أي تعديل إضافي بـmain.py — غيابهما الآن ImportError
# متوقع، يُسجّل معلوماتيًا فقط ولا يوقف إقلاع الخدمة.
#
# تصحيح Medium 3 بمراجعة B4 الأوفلاين (docs/reports/B4-offline-review.md):
# `except ImportError` وحدها لا تلتقط SyntaxError ولا أي استثناء آخر يحدث
# أثناء تحميل هذه الوحدات أو أي وحدة تستوردها (هذا حدث فعليًا بالإنتاج —
# commit "B4 hotfix: comment out stray line in mail_api.py" — أسقط main.py
# بالكامل رغم أن نية العزل كانت واضحة). `except Exception` هنا تحمي /health
# وكل نقاط B1/B2/B3 من أي عطل بملف B4 وحده مهما كان نوعه، مع تسجيل كامل
# (traceback) بدل الصمت.
for mod_name in (
    "app.mail_api",
    "app.inbox_api",
    "app.reports_api",
    "app.feedback_api",
    "app.guarantee_api",
    "app.overview_api",
    "app.link_api",
    "app.send_stats_api",
    "app.catalog",
    "app.telegram_api",
):
    try:
        module = __import__(mod_name, fromlist=["router"])
        app.include_router(module.router)
        logger.info("تم تحميل راوتر %s", mod_name)
    except ImportError:
        logger.info("راوتر %s غير موجود بعد (متوقّع قبل اكتمال B4) — تخطّي", mod_name)
    except Exception:  # noqa: BLE001 — أي عطل آخر (SyntaxError إلخ) يجب ألا يُسقط main.py
        logger.exception("تعذّر تحميل راوتر %s بخطأ غير متوقع (غير ImportError) — تخطّي وإبقاء بقية الخدمة حية", mod_name)
        # B8 (إزالة n8n): تنبيه أحمد فورًا عبر تيليجرام مباشرة — بديل مباشر
        # لما كان يُفترض أن يمرّ عبر جسر core-notify-admin (n8n)، ونفس
        # سيناريو "B4 hotfix" الموثّق أعلى هذا الملف حرفيًا (SyntaxError
        # بوحدة B4 كاد يُسقط main.py بالكامل قبل تعديل except إلى Exception).
        # notify_admin دالة best-effort لا ترفع استثناءً أبدًا مهما فشل
        # الإرسال نفسه (توكن غير معرّف، شبكة إلخ) — لا خطر إضافي على إقلاع
        # الخدمة حتى لو تيليجرام نفسه غير متاح الآن.
        notify_admin(f"🚨 فشل تحميل راوتر {mod_name} بخطأ غير متوقع عند إقلاع masar-core — راجع السجلّ فورًا.")


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    timestamp: str


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """نقطة فحص الصحّة — تُستخدم من UptimeRobot ومن n8n (Core - Call) للتأكد أن الخدمة حية."""
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
    """أول نقطة نهاية محمية بـCORE_ADMIN_TOKEN — تُستخدم للتحقق من صحة
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
