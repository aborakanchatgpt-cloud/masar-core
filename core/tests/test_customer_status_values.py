"""اختبار وحدة بسيط بلا قاعدة بيانات — B9/B0: CUSTOMER_STATUS_ALLOWED_VALUES
أضافت 'pending' (التفعيل اليدوي pending→active)، وتبقى 'expired' خارج
المجموعة عمدًا (انتقالها منطق منفصل بـguarantee.py، لم يتغيّر)."""
from __future__ import annotations

from app import customers_api


def test_customer_status_allowed_values_includes_pending_not_expired():
    assert customers_api.CUSTOMER_STATUS_ALLOWED_VALUES == {"pending", "active", "paused"}
    assert "expired" not in customers_api.CUSTOMER_STATUS_ALLOWED_VALUES
