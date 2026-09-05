"""
حماية بسيطة لنقاط النهاية الإدارية/الجسر (Bridge) عبر توكن ثابت واحد
(CORE_ADMIN_TOKEN) يُولَّد عشوائيًا مرة واحدة أثناء bootstrap.sh ويُحفظ محليًا
بملف .env على الخادم فقط — هذا ليس نظام مستخدمين (يُبنى لاحقًا بوحدة identity
بالمرحلة 3)، فقط قفل بسيط يمنع أي طرف عام من استدعاء أي نقطة غير /health.

الاستخدام من n8n (وركفلو Core - Call): يُضاف الترويسة
Authorization: Bearer <CORE_ADMIN_TOKEN> عبر اعتماد HTTP Header Auth باسم
"Masar Core Admin Token" يُنشئه أحمد بنفسه من واجهة n8n (Settings → Credentials)
بعد لصق القيمة التي يطبعها bootstrap.sh — القيمة لا تمر عبر أي جلسة Claude.
"""
from __future__ import annotations

import os

from fastapi import Header, HTTPException, status


def require_admin_token(authorization: str | None = Header(default=None)) -> None:
    """يتحقق من ترويسة Authorization: Bearer <token> مقابل CORE_ADMIN_TOKEN.

    فشل مغلق (fail-closed) عمدًا: إن لم يكن CORE_ADMIN_TOKEN مُعرَّفًا بالبيئة
    (مثلًا نسيان إعداده)، تُرفض كل الطلبات بدل السماح بها بالخطأ.
    """
    expected = os.environ.get("CORE_ADMIN_TOKEN")
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="CORE_ADMIN_TOKEN غير معرّف على الخادم — لا يمكن التحقق من الصلاحية",
        )

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authorization header مفقود")

    provided = authorization.removeprefix("Bearer ").strip()
    if provided != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="توكن غير صحيح")
