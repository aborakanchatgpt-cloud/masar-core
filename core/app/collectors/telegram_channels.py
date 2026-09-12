"""
Masar Core — مجمّع قنوات تيليجرام للوظائف (B12.1، راجع
claude/masar_build_brief_v4_2026-09-12.md القسم 2).

قنوات تيليجرام عامة سعودية تنشر إعلانات وظائف (`data/telegram_channels.yaml`
— كل قناة تحقّقت من وجودها فعليًا عبر WebFetch على `https://t.me/s/<name>`
قبل إدراجها، لا اختلاق). لكل رسالة: نستخرج بريد تقديم صالح (عبر
`app.apply_email`)، ونتجاهل أي رسالة بلا بريد — القنوات غالبًا تخلط
إعلانات بنماذج خارجية/واتساب لا يمكننا إرسال بريد نيابةً عنها، فتجاهل
الرسائل بلا بريد هو الفلتر الصحيح الوحيد الممكن هنا (لا نموذج تصنيف
 apply_mode مثل discovery.py العادية — قناة تيليجرام ليست ATS رسميًا).

**Telethon (لا Bot API):** يتطلّب حساب Telegram شخصي (لا بوت) للقراءة —
البوتات لا تستطيع قراءة رسائل قنوات لم تُضاف كأدمن فيها. `StringSession`
تُولّد مرة واحدة محليًا عبر `scripts/telegram_reader_login.py` (تفاعلي،
يشغّله أحمد بنفسه — لا تُخزّن الجلسة بالمستودع أبدًا). الاستيراد محروس
(`try/except ImportError`) — غياب حزمة `telethon` لا يجب أن يُسقط
core-scheduler بأكمله (المجدوِل يشغّل جولات أخرى حرجة: الإرسال/الوارد/
التخطيط).

**Fail-closed صارم:** غياب أي من `TELEGRAM_READER_API_ID`/
`TELEGRAM_READER_API_HASH`/`TELEGRAM_READER_SESSION` = المجمّع معطّل
بالكامل (سطر سجل معلوماتي واحد، لا استثناء) — بلا استثناء ولا محاولة
اتصال جزئي. نفس فلسفة `TELEGRAM_WEBHOOK_SECRET`/`MCP_BRIDGE_TOKEN`
الموثّقة بالمستودع.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import yaml
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from app.apply_email import classify_email, extract_emails
from app.collectors.field_extractor import (
    compute_region,
    extract_cities,
    extract_seniority,
    extract_skills,
    extract_years_required,
)

logger = logging.getLogger("masar.collectors.telegram_channels")

# استيراد محروس — راجع docstring أعلى الملف. غياب الحزمة يُعامَل مطابقًا
# تمامًا لغياب متغيرات البيئة (المجمّع معطّل، بلا استثناء).
try:
    from telethon.sessions import StringSession
    from telethon.sync import TelegramClient
except ImportError:  # pragma: no cover - بيئة لم تُثبّت فيها telethon بعد
    TelegramClient = None  # type: ignore[assignment,misc]
    StringSession = None  # type: ignore[assignment,misc]

REQUIRED_ENV_VARS = ("TELEGRAM_READER_API_ID", "TELEGRAM_READER_API_HASH", "TELEGRAM_READER_SESSION")
MESSAGES_PER_CHANNEL = 50
MIN_MESSAGE_TEXT_CHARS = 20


def _data_dir() -> Path:
    return Path(os.environ.get("DATA_DIR", "/app/data"))


def is_enabled() -> bool:
    """fail-closed: يتطلّب الحزمة مثبّتة *و* كل متغيرات البيئة الثلاثة معًا."""
    if TelegramClient is None:
        return False
    return all(os.environ.get(v, "").strip() for v in REQUIRED_ENV_VARS)


def _load_channels() -> list[str]:
    path = _data_dir() / "telegram_channels.yaml"
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except OSError:
        logger.warning("تعذّرت قراءة telegram_channels.yaml من %s", path)
        return []
    channels = data.get("channels", []) or []
    return [str(c).strip().lstrip("@") for c in channels if str(c).strip()]


# ---------------------------------------------------------------------------
# companies/sources — صفّ وحيد لكل قناة (idempotent)
# ---------------------------------------------------------------------------


def _ensure_channel_source(conn: Connection, channel: str) -> tuple[int, int]:
    company_name = f"قناة تيليجرام: {channel}"
    company_row = conn.execute(
        text(
            """
            INSERT INTO companies (name, status) VALUES (:name, 'active')
            ON CONFLICT (name) DO UPDATE SET status = 'active'
            RETURNING id
            """
        ),
        {"name": company_name[:255]},
    ).first()
    company_id = company_row[0]

    # enabled=false عمدًا: discovery.run_round العادية تتخطّى هذا المصدر
    # تمامًا (لا يوجد 'telegram_channel' بـdiscovery.DISPATCH، ولا يجب أن
    # يكون — القراءة هنا Telethon بجلسة مستمرة، لا استدعاء HTTP بسيط لكل
    # جولة كبقية المصادر). الصفّ موجود فقط لأن jobs.source_id غير قابل لNULL.
    source_row = conn.execute(
        text(
            """
            INSERT INTO sources (company_id, source_type, source_url, enabled, region_filter)
            VALUES (:company_id, 'telegram_channel', :url, false, 'gcc')
            ON CONFLICT (source_url) DO UPDATE SET company_id = EXCLUDED.company_id
            RETURNING id
            """
        ),
        {"company_id": company_id, "url": f"https://t.me/{channel}"},
    ).first()
    return company_id, source_row[0]


# ---------------------------------------------------------------------------
# معالجة رسالة واحدة → صفّ jobs (أو تجاهل بصمت إن بلا بريد صالح)
# ---------------------------------------------------------------------------


def message_dedup_key(channel: str, message_id: int) -> str:
    return hashlib.sha1(f"tg_channel|{channel}|{message_id}".encode("utf-8")).hexdigest()


def extract_apply_email_from_message(text_content: str) -> str | None:
    """أول بريد غير محظور (راجع app.apply_email.classify_email) مذكور
    صراحة بنص الرسالة — لا نميّز بين careers/posted/generic هنا (القناة
    نفسها مصدر عام، لا صفحة توظيف رسمية) طالما ليس محظورًا صراحة."""
    for addr in extract_emails(text_content):
        if classify_email(addr) != "banned":
            return addr
    return None


def process_message(
    conn: Connection,
    *,
    channel: str,
    company_id: int,
    source_id: int,
    message_id: int,
    text_content: str,
    sent_at: datetime,
    classify_family_fn,
) -> bool:
    """يُدرج صفّ jobs لهذه الرسالة إن حملت بريد تقديم صالحًا ونصًا كافيًا،
    ويُرجع True إن كان إدراجًا جديدًا فعليًا (لا تحديث last_seen_at فقط).
    `classify_family_fn` تُمرّر من المستدعي (discovery.classify_family) —
    لا استيراد مباشر لـdiscovery هنا لتفادي استيراد دائري نظري (discovery.py
    لا يستورد هذا الملف حاليًا، لكن الفصل أنظف ويطابق نمط دوال field_extractor
    الأخرى بهذا الملف التي تُمرّر جاهزة لا تُستورَد من وحدات ذات دولة)."""
    if not text_content or len(text_content.strip()) < MIN_MESSAGE_TEXT_CHARS:
        return False

    apply_email = extract_apply_email_from_message(text_content)
    if not apply_email:
        return False

    lines = [ln.strip() for ln in text_content.strip().splitlines() if ln.strip()]
    title = (lines[0] if lines else "وظيفة عبر تيليجرام")[:255]

    cities = extract_cities(text_content)
    city = cities[0] if cities else None
    country_code, out_of_region = compute_region(city, text_content[:500])
    if out_of_region:
        # القنوات مُتحقَّق من عمومها السعودي مسبقًا (راجع data/telegram_channels.yaml)
        # لكن رسالة فردية قد تعلن صراحةً وظيفة بدولة أخرى — تُتجاهل بصمت.
        return False

    family = classify_family_fn(title, text_content)
    years_min, _years_max = extract_years_required(text_content)
    seniority = extract_seniority(text_content, title=title)
    skills = extract_skills(text_content)
    dedup_key = message_dedup_key(channel, message_id)

    inserted = conn.execute(
        text(
            """
            INSERT INTO jobs (
                source_id, company_id, external_id, title, location, family, dedup_key,
                raw_json, company_name, city, years_min, seniority, saudi_only, skills,
                apply_mode, description_snippet, country_code, out_of_region,
                first_seen_at, last_seen_at
            ) VALUES (
                :source_id, :company_id, :external_id, :title, :city, :family, :dedup_key,
                :raw_json, :company_name, :city, :years_min, :seniority, false, :skills,
                'email', :description_snippet, :country_code, false, :sent_at, now()
            )
            ON CONFLICT (dedup_key) DO UPDATE SET last_seen_at = now()
            RETURNING (xmax = 0) AS was_insert
            """
        ),
        {
            "source_id": source_id,
            "company_id": company_id,
            "external_id": str(message_id),
            "title": title,
            "family": family,
            "dedup_key": dedup_key,
            "raw_json": json.dumps({"apply_email": apply_email}, ensure_ascii=False),
            "company_name": f"قناة تيليجرام: {channel}"[:255],
            "city": city[:120] if city else None,
            "years_min": years_min,
            "seniority": seniority,
            "skills": json.dumps(skills, ensure_ascii=False),
            "description_snippet": text_content[:2000],
            "country_code": country_code,
            "sent_at": sent_at,
        },
    ).first()
    return bool(inserted and inserted[0])


# ---------------------------------------------------------------------------
# جولة كاملة — تُستدعى من scheduler_main.py كل 30 دقيقة
# ---------------------------------------------------------------------------


def run_telegram_channel_round(engine: Engine | None = None) -> dict:
    if not is_enabled():
        if TelegramClient is None:
            logger.info("مجمّع قنوات تيليجرام معطّل: حزمة telethon غير مثبّتة بهذه البيئة")
        else:
            missing = [v for v in REQUIRED_ENV_VARS if not os.environ.get(v, "").strip()]
            logger.info("مجمّع قنوات تيليجرام معطّل: متغيرات بيئة ناقصة: %s", ", ".join(missing))
        return {"ok": True, "enabled": False}

    from app.discovery import classify_family, get_engine

    engine = engine or get_engine()
    channels = _load_channels()
    if not channels:
        logger.warning("data/telegram_channels.yaml فارغ أو غير موجود — لا قنوات لقراءتها")
        return {"ok": True, "enabled": True, "channels_processed": 0, "jobs_new": 0}

    api_id = int(os.environ["TELEGRAM_READER_API_ID"])
    api_hash = os.environ["TELEGRAM_READER_API_HASH"]
    session_string = os.environ["TELEGRAM_READER_SESSION"]

    channels_processed = 0
    messages_seen = 0
    jobs_new = 0
    channel_errors = 0

    try:
        with TelegramClient(StringSession(session_string), api_id, api_hash) as client:
            for channel in channels:
                try:
                    with engine.begin() as conn:
                        company_id, source_id = _ensure_channel_source(conn, channel)
                    messages = client.get_messages(channel, limit=MESSAGES_PER_CHANNEL)
                    channels_processed += 1
                    with engine.begin() as conn:
                        for msg in messages:
                            msg_id = getattr(msg, "id", None)
                            if msg_id is None:
                                continue
                            messages_seen += 1
                            msg_text = getattr(msg, "message", "") or ""
                            sent_at = getattr(msg, "date", None) or datetime.now(timezone.utc)
                            if process_message(
                                conn,
                                channel=channel,
                                company_id=company_id,
                                source_id=source_id,
                                message_id=msg_id,
                                text_content=msg_text,
                                sent_at=sent_at,
                                classify_family_fn=classify_family,
                            ):
                                jobs_new += 1
                except Exception:  # noqa: BLE001 — عزل خطأ قناة واحدة عن بقية الجولة
                    logger.warning("فشل جلب/معالجة قناة تيليجرام %s", channel, exc_info=True)
                    channel_errors += 1
                    continue
    except Exception:  # noqa: BLE001 — فشل الاتصال الأساسي (جلسة منتهية/شبكة) لا يجب أن يُسقط المجدول
        logger.exception("فشل اتصال Telethon الأساسي بمجمّع قنوات تيليجرام — تخطي هذه الجولة")
        return {"ok": False, "enabled": True, "error": "connect_failed"}

    result = {
        "ok": True,
        "enabled": True,
        "channels_total": len(channels),
        "channels_processed": channels_processed,
        "channel_errors": channel_errors,
        "messages_seen": messages_seen,
        "jobs_new": jobs_new,
    }
    logger.info("جولة مجمّع قنوات تيليجرام انتهت: %s", result)
    return result
