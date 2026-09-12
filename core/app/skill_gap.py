"""
Masar Core — تحليل الفجوة المعرفية الأسبوعي (نصيحة الجمعة) — B4/v2-B6 (12
سبتمبر 2026): إعادة كتابة كاملة **بديل إحصائي مجاني بالكامل**، بلا أي
اعتماد على ANTHROPIC_API_KEY (قرار أحمد الصريح: "طريقة مجانية" — راجع
`claude/masar_build_brief_v2_2026-09-11.md` §B6). النسخة السابقة (كانت
تستدعي Anthropic Messages API مباشرة) أُزيلت بالكامل — لا كود متبقٍّ منها
هنا، ولا أي فحص `ANTHROPIC_API_KEY` (أصبح غير ذي صلة إطلاقًا بهذا الملف).

**الفكرة الجديدة (إحصائية بحتة، بلا أي طلب شبكي خارجي):**
    1. **المهارات:** لكل عميل نشط له مجالات مهنية مختارة، نجمع
       `jobs.skills` لكل الوظائف المُكتشفة آخر 30 يومًا **ضمن مجالاته**
       (`jobs.family = ANY(customer.families)`) و**ضمن مدنه** (بإعادة
       استخدام `app.matching.city_allowed`/`normalized_city_set` نفسها —
       لا منطق مطابقة مدن جديد هنا، نفس محرّك التوجيه الفعلي). نرتّب
       المهارات بتكرارها تنازليًا، نستبعد ما يملكه العميل أصلًا
       (`profiles.skills`، مطابقة مُطبّعة بسيطة lower+strip — لا حاجة
       لمطابقة نصية بـ`cv_text` الخام طالما `profiles.skills` مُستخرَجة
       أصلًا منه)، ونأخذ أعلى 5.
    2. **الشهادات:** ملف بيانات ثابت جديد `data/certifications_by_family.yaml`
       (2-3 شهادات احترافية معروفة فعليًا بالسوق السعودي/الخليجي لكل عائلة
       من الـ21، مع سبب موجز لكل واحدة، ونصيحة مسيرة مهنية ثابتة بجملتين-
       ثلاث) — نستبعد ما يملكه العميل أصلًا (`profiles.certs`، نفس منطق
       المطابقة المُطبّعة) ونأخذ أعلى شهادتين غير مكرّرتين.
    3. **بوابة كفاية البيانات:** أقل من `MIN_JOBS_SAMPLE` (10) وظيفة مطابقة
       آخر 30 يومًا لعميل معيّن → تخطٍّ صامت كامل لذلك العميل (لا رسالة
       "لا بيانات كافية" تصله — القرار: انتظار بيانات أكثر أفضل من نصيحة
       ضعيفة الجودة إحصائيًا).

رسالة تيليجرام النهائية (`_build_skill_gap_message`) تحتفظ بنفس الشكل/النبرة
حرفيًا من النسخة السابقة (عنوان، شهادتان، 5 مهارات، نصيحة، خاتمة دعاء) —
فقط مصدر المحتوى تغيّر، لا الصياغة النهائية المرسَلة للعميل."""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path

import yaml
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.discovery import get_engine
from app.matching import city_allowed
from app.telegram_notify import send_message, split_message

logger = logging.getLogger("masar.skill_gap")

# نفس هامش الأمان تحت حد تيليجرام المستخدَم ببقية الوحدات الجديدة.
MAX_MESSAGE_CHARS = 3900

# نافذة عيّنة الوظائف الإحصائية — آخر 30 يومًا (قرار التصميم بالتكليف).
JOB_SAMPLE_WINDOW_DAYS = 30

# أقل عدد وظائف مطابقة لازم لاعتبار الإحصاء ذا دلالة كافية — أقل من هذا
# يعني تخطٍّ صامت كامل للعميل (راجع docstring الرأس، البند 3).
MIN_JOBS_SAMPLE = 10

TOP_SKILLS_COUNT = 5
TOP_CERTIFICATIONS_COUNT = 2


def _data_dir() -> Path:
    # نفس دالة app.discovery._data_dir() حرفيًا (DATA_DIR بيئي، افتراضي
    # /app/data داخل الحاوية) — مكررة هنا بسطر واحد بدل استيراد دالة
    # خاصة (`_`) من وحدة أخرى.
    return Path(os.environ.get("DATA_DIR", "/app/data"))


@lru_cache(maxsize=1)
def _load_certifications_by_family() -> dict:
    """يقرأ `data/certifications_by_family.yaml` مرة واحدة لكل عملية (نفس
    فلسفة `discovery.classify_family`: البيانات ثابتة أثناء تشغيل العملية،
    التغيير يحتاج إعادة تشغيل — لا حاجة لإعادة قراءة الملف بكل جولة أسبوعية).
    ملف مفقود/فاسد → قاموس فارغ (كل عميل يُعامَل حينها كمن لا شهادات
    مرشّحة له بلا استثناء، بدل إسقاط الجولة الأسبوعية بالكامل)."""
    path = _data_dir() / "certifications_by_family.yaml"
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except OSError:
        logger.warning("تعذّرت قراءة certifications_by_family.yaml من %s", path)
        return {}
    return data if isinstance(data, dict) else {}


def _normalize(value: str) -> str:
    return (value or "").strip().lower()


def _extract_title_strings(titles_json: list) -> list[str]:
    """profiles.titles مخزّن كـ[{"title": str, "weight": float}, ...] —
    نفس تنسيق core/app/matching.py (راجع `for entry in profile.titles` هناك).
    محفوظة من النسخة السابقة (لا تزال مستخدَمة اختياريًا مستقبلًا، ولا ضرر
    ببقائها — اختبارات الوحدة القديمة لهذه الدالة تحديدًا تبقى صالحة)."""
    out = []
    for entry in titles_json or []:
        if isinstance(entry, dict):
            t = entry.get("title")
            if t:
                out.append(t)
    return out


def _fetch_active_customers_with_profile(conn) -> list[dict]:
    rows = conn.execute(
        text(
            """
            SELECT c.id, c.name, c.telegram_chat_id, c.families, c.cities,
                   p.skills AS profile_skills, p.certs AS profile_certs
            FROM customers c JOIN profiles p ON p.customer_id = c.id
            WHERE c.status = 'active'
            """
        )
    ).mappings().all()
    return [dict(r) for r in rows]


def _fetch_recent_jobs_by_family(conn, families: list[str], window_days: int) -> list[dict]:
    """كل الوظائف بأي من `families` المكتشَفة آخر `window_days` يومًا —
    **بلا** فلترة مدينة بمستوى SQL (تقع بايثون عبر `city_allowed`، نفس
    استدعاء محرّك المطابقة الفعلي، راجع docstring الرأس)."""
    if not families:
        return []
    rows = conn.execute(
        text(
            """
            SELECT city, skills FROM jobs
            WHERE family = ANY(:families) AND first_seen_at >= now() - make_interval(days => :days)
            """
        ),
        {"families": families, "days": window_days},
    ).mappings().all()
    return [dict(r) for r in rows]


def _filter_jobs_by_city(jobs: list[dict], customer_cities: list[str]) -> list[dict]:
    """يعيد استخدام `app.matching.city_allowed` حرفيًا — نفس محرّك التوجيه
    الفعلي، لا منطق مطابقة مدن جديد أو مختلف هنا (راجع docstring الرأس)."""
    return [j for j in jobs if city_allowed(j.get("city"), customer_cities)]


def _aggregate_top_skills(jobs: list[dict], exclude_normalized: set[str], top_n: int) -> list[str]:
    """يرتّب مهارات `jobs.skills` (قائمة نصوص لكل وظيفة) بتكرارها تنازليًا
    عبر كل الوظائف المُمرّرة، يستبعد ما بـ`exclude_normalized` (مُطبّعة
    مسبقًا)، ويرجع أعلى `top_n` بأسمائها الأصلية (أول ظهور غير مُطبّع لكل
    مهارة — لا لوغاريتم ترجيح إضافي، تكرار بسيط يكفي لهذا الغرض الإحصائي)."""
    counts: dict[str, int] = {}
    original_casing: dict[str, str] = {}
    for job in jobs:
        for skill in job.get("skills") or []:
            if not isinstance(skill, str) or not skill.strip():
                continue
            key = _normalize(skill)
            if key in exclude_normalized:
                continue
            counts[key] = counts.get(key, 0) + 1
            original_casing.setdefault(key, skill.strip())

    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [original_casing[key] for key, _ in ranked[:top_n]]


def _pick_certifications(
    families: list[str], exclude_normalized: set[str], certs_by_family: dict, top_n: int
) -> list[dict]:
    """يجمع شهادات كل عائلات العميل بترتيبها بملف YAML (عائلة العميل
    الأولى أولاً)، يستبعد ما يملكه العميل أصلًا (`profiles.certs` مُطبّعة)،
    ويزيل التكرار (شهادة واحدة قد تظهر بأكثر من عائلة)، ويرجع أعلى
    `top_n` كقواميس {"name", "reason"} (نفس شكل مخرجات النسخة السابقة —
    يبقي `_build_skill_gap_message` بلا أي تعديل)."""
    picked: list[dict] = []
    seen_normalized: set[str] = set()
    for family in families:
        family_certs = (certs_by_family.get(family) or {}).get("certifications") or []
        for cert in family_certs:
            name = cert.get("name") if isinstance(cert, dict) else None
            if not name:
                continue
            key = _normalize(name)
            if key in exclude_normalized or key in seen_normalized:
                continue
            seen_normalized.add(key)
            picked.append({"name": name, "reason": cert.get("reason", "")})
            if len(picked) >= top_n:
                return picked
    return picked


def _pick_advice(families: list[str], certs_by_family: dict) -> str:
    """نصيحة ثابتة حسب عائلة العميل **الأولى** (المجال الأساسي المختار
    أولاً بـonboarding — راجع تعليق التصميم بالتكليف: "نصيحة ثابتة حسب
    العائلة"). عميل بلا عائلات معروفة بملف YAML → سلسلة فارغة (القسم
    ببساطة لا يظهر، نفس سلوك النسخة السابقة مع رد Anthropic فارغ)."""
    for family in families:
        advice = (certs_by_family.get(family) or {}).get("advice")
        if advice:
            return advice
    return ""


def _build_skill_gap_message(parsed: dict) -> str:
    """نفس نص/بنية الرسالة النهائية حرفيًا من النسخة السابقة (كانت تُبنى
    من رد Anthropic المُحلّل؛ الآن تُبنى من الاختيار الإحصائي أعلاه —
    الشكل النهائي للعميل بلا أي تغيير)."""
    lines = [
        "🌟 نصيحة الجمعة لتطوير مسارك المهني",
        "",
        "قعدنا نراجع سيرتك الذاتية ووضعك في السوق، وهذي أبرز الأشياء اللي نشوف إنها ترفع فرصك:",
    ]

    certifications = parsed.get("certifications") or []
    if certifications:
        lines.append("")
        lines.append("🎓 أهم شهادتين نرشحهما لك:")
        for i, cert in enumerate(certifications, start=1):
            lines.append(f"{i}. {cert.get('name', '')}")
            if cert.get("reason"):
                lines.append(f"💡 {cert['reason']}")

    skills = parsed.get("skills") or []
    if skills:
        lines.append("")
        lines.append("🛠️ أهم 5 مهارات نرشحها لك تطويرها:")
        for i, skill in enumerate(skills, start=1):
            lines.append(f"{i}. {skill.get('name', '')}")
            if skill.get("reason"):
                lines.append(f"💡 {skill['reason']}")

    if parsed.get("advice"):
        lines.append("")
        lines.append("📌 نصيحتنا لك:")
        lines.append(parsed["advice"])

    lines.append("")
    lines.append("استمر بالتطور، والله يوفقك ويرزقك 🤍")
    return "\n".join(lines)


def run_skill_gap_round(engine: Engine | None = None) -> dict:
    """نقطة الدخول الرئيسية — تُستدعى أسبوعيًا كل جمعة من scheduler_main.py
    (2 ظهرًا الرياض، نفس توقيت n8n السابق حرفيًا). **لا بوابة API مفتاح
    بعد الآن** (البديل الإحصائي بلا اعتماد خارجي — راجع docstring الرأس). idempotent
    بالمعنى الضعيف فقط (لا جدول تتبّع "أُرسلت هذا الأسبوع"، نفس
    القرار الموثّق بالنسخة السابقة — خارج نطاق هذه الدفعة)."""
    engine = engine or get_engine()
    certs_by_family = _load_certifications_by_family()

    with engine.connect() as conn:
        customers = _fetch_active_customers_with_profile(conn)

    sent = 0
    skipped_no_chat = 0
    skipped_no_family = 0
    skipped_insufficient_data = 0
    errors = 0

    for customer in customers:
        chat_id = customer.get("telegram_chat_id")
        if not chat_id:
            skipped_no_chat += 1
            continue

        families = customer.get("families") or []
        if not families:
            skipped_no_family += 1
            continue

        try:
            with engine.connect() as conn:
                jobs_by_family = _fetch_recent_jobs_by_family(conn, families, JOB_SAMPLE_WINDOW_DAYS)
            jobs = _filter_jobs_by_city(jobs_by_family, customer.get("cities") or [])

            if len(jobs) < MIN_JOBS_SAMPLE:
                skipped_insufficient_data += 1
                continue

            profile_skills_normalized = {_normalize(s) for s in (customer.get("profile_skills") or []) if s}
            profile_certs_normalized = {_normalize(c) for c in (customer.get("profile_certs") or []) if c}

            top_skills = _aggregate_top_skills(jobs, profile_skills_normalized, TOP_SKILLS_COUNT)
            certifications = _pick_certifications(
                families, profile_certs_normalized, certs_by_family, TOP_CERTIFICATIONS_COUNT
            )
            advice = _pick_advice(families, certs_by_family)

            parsed = {
                "certifications": certifications,
                "skills": [{"name": s} for s in top_skills],
                "advice": advice,
            }
            message = _build_skill_gap_message(parsed)
            for chunk in split_message(message, MAX_MESSAGE_CHARS):
                send_message(chat_id, chunk)
            sent += 1
        except Exception:  # noqa: BLE001 — عزل خطأ عميل واحد عن بقية الجولة
            logger.exception("فشلت نصيحة الجمعة الإحصائية للعميل %s", customer.get("id"))
            errors += 1

    result = {
        "ok": True,
        "customers_active_with_profile": len(customers),
        "sent": sent,
        "skipped_no_chat": skipped_no_chat,
        "skipped_no_family": skipped_no_family,
        "skipped_insufficient_data": skipped_insufficient_data,
        "errors": errors,
    }
    logger.info("جولة تحليل الفجوة المعرفية الأسبوعية (إحصائية) انتهت: %s", result)
    return result
