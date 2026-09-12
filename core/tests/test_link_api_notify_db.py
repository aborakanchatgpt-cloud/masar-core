"""B4 (دفعة ثانية) — إشعار العميل الفوري عبر بوت العميل بنتيجة ربط بريده
(نجاح/فشل) من `link_api.submit_link_form` → `_notify_customer_mail_link_result`
(يعيد استخدام `app.telegram_notify_admin.notify_customer` و
`app.reports.fetch_customer_chat_id` الموجودتين مسبقًا — لا منطق تصنيف
نجاح/فشل جديد هنا). نفس نمط test_telegram_admin_commands_notify_db.py.

يحتاج قاعدة بيانات Postgres حقيقية وMAIL_FERNET_KEY صالحًا — يُتخطى
تلقائيًا (skip) إن تعذّرا، نفس نمط test_link_api.py.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from starlette.requests import Request

from app import link_api, mail_api, telegram_notify_admin

DEFAULT_TEST_DB_URL = "postgresql+psycopg://masar_test:masar_test@localhost:5432/masar_test"


def _make_engine() -> Engine | None:
    url = os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DB_URL)
    try:
        engine = create_engine(url, pool_pre_ping=True)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return engine
    except Exception:  # noqa: BLE001
        return None


_ENGINE = _make_engine()

if _ENGINE is None:
    pytest.skip(
        "لا اتصال بقاعدة بيانات Postgres محلية للاختبار — يُتخطى test_link_api_notify_db.py كليًا.",
        allow_module_level=True,
    )

if not os.environ.get("MAIL_FERNET_KEY"):
    os.environ["MAIL_FERNET_KEY"] = Fernet.generate_key().decode("ascii")


def _run(coro):
    return asyncio.run(coro)


def _make_form_request(form_dict: dict) -> Request:
    body = "&".join(f"{k}={v}" for k, v in form_dict.items()).encode("utf-8")

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {
        "type": "http",
        "method": "POST",
        "headers": [(b"content-type", b"application/x-www-form-urlencoded")],
    }
    return Request(scope, receive)


@pytest.fixture()
def engine() -> Engine:
    return _ENGINE


@pytest.fixture(autouse=True)
def _patch_engine(monkeypatch, engine):
    monkeypatch.setattr(link_api, "get_engine", lambda: engine)
    monkeypatch.setattr(mail_api, "get_engine", lambda: engine)
    monkeypatch.setattr(telegram_notify_admin, "get_engine", lambda: engine)


@pytest.fixture()
def customer_with_chat(engine):
    tag = uuid.uuid4().hex[:10]
    chat_id = int(uuid.uuid4().int % 900000000) + 10**9
    with engine.begin() as conn:
        cid = conn.execute(
            text(
                "INSERT INTO customers (name, email_service, status, target_daily, telegram_chat_id) "
                "VALUES (:n, :e, 'active', 17, :tg) RETURNING id"
            ),
            {"n": f"LinkNotify {tag}", "e": f"linknotify-{tag}@masar.invalid", "tg": chat_id},
        ).scalar_one()
    yield cid, chat_id
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM link_tokens WHERE customer_id = :cid"), {"cid": cid})
        conn.execute(text("DELETE FROM mail_links WHERE customer_id = :cid"), {"cid": cid})
        conn.execute(text("DELETE FROM customers WHERE id = :cid"), {"cid": cid})


def test_submit_success_notifies_customer_of_successful_link(engine, customer_with_chat, monkeypatch):
    customer_id, chat_id = customer_with_chat
    monkeypatch.setenv("MAIL_LIVE", "false")
    sent_calls: list[tuple] = []
    monkeypatch.setattr(telegram_notify_admin, "send_message", lambda cid, txt, **kw: sent_calls.append((cid, txt)))

    created = _run(link_api.create_link_token(customer_id))
    token = created["path"].removeprefix("/link/")
    request = _make_form_request(
        {"gmail_address": "test.career%40gmail.com", "app_password": "abcd1234abcd1234", "consent": "on"}
    )
    response = _run(link_api.submit_link_form(token, request, skip_verify=1))
    body = response.body.decode("utf-8")

    assert "تم الربط بنجاح" in body
    assert len(sent_calls) == 1
    assert sent_calls[0][0] == chat_id
    assert "تم ربط بريدك بنجاح" in sent_calls[0][1]


def test_submit_failure_notifies_customer_of_failed_link(engine, customer_with_chat, monkeypatch):
    customer_id, chat_id = customer_with_chat
    monkeypatch.setenv("MAIL_LIVE", "false")
    sent_calls: list[tuple] = []
    monkeypatch.setattr(telegram_notify_admin, "send_message", lambda cid, txt, **kw: sent_calls.append((cid, txt)))

    async def fake_create_mail_link(request):
        return {"ok": False}

    monkeypatch.setattr(mail_api, "create_mail_link", fake_create_mail_link)

    created = _run(link_api.create_link_token(customer_id))
    token = created["path"].removeprefix("/link/")
    request = _make_form_request(
        {"gmail_address": "test.career%40gmail.com", "app_password": "abcd1234abcd1234", "consent": "on"}
    )
    response = _run(link_api.submit_link_form(token, request, skip_verify=1))
    body = response.body.decode("utf-8")

    assert "تعذّر الربط" in body
    assert len(sent_calls) == 1
    assert sent_calls[0][0] == chat_id
    assert "تعذّر ربط بريدك" in sent_calls[0][1]

    with engine.connect() as conn:
        exists = conn.execute(text("SELECT id FROM mail_links WHERE customer_id = :cid"), {"cid": customer_id}).first()
    assert exists is None
