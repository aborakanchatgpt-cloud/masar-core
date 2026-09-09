# B4 — تصحيحات المراجعة الحيّة (B3B4-live-review.md)، منفّذة ومنشورة

**التاريخ:** 2026-09-08/09 (تنفيذ + نشر + تحقّق حي)
**المنفّذ:** Sonnet subagent (بيئة sandbox معزولة — clone + venv + Postgres 16 محلي، دفع عبر Masar MCP bridge فقط، بلا وصول SSH)
**النطاق:** الفحوصات الثلاثة من `docs/reports/B3B4-live-review.md` (F1 عالٍ، F2 متوسط 2، F3 متوسط 1).

---

## الإصلاحات

### F1 (عالٍ) — `create_mail_link` يفرض SMTP/IMAP حقيقيين بمعزل عن DRY_RUN
**الملفات:** `migrations/versions/0007_mail_link_skip_verify.py` (جديد)، `core/app/mail_api.py`، `core/app/sender.py`.

- عمود جديد `mail_links.verified_via` (nullable، `VARCHAR(50)`) — `NULL` = تحقّق SMTP/IMAP حقيقي طبيعي، `'skipped-dry-run'` = تجاوز إداري صريح.
- `MailLinkCreateRequest.skip_verify: bool = False` (حقل اختياري جديد).
- `sender.is_dry_run()` (دالة مساعدة جديدة، مصدر حقيقة واحد): `DRY_RUN فعّال ⇔ MAIL_LIVE != "true"`.
- في `create_mail_link`: إن `skip_verify=true` و`not is_dry_run()` → **400** فورًا (قبل أي وصول لقاعدة البيانات) برسالة واضحة. إن `skip_verify=true` و`is_dry_run()` → يتخطّى `_test_smtp`/`_test_imap` كليًا، يضبط `status='ok'`, `error=None`, `verified_via='skipped-dry-run'` مباشرة. المسار الافتراضي (`skip_verify=false`) غير مُغيَّر إطلاقًا — يستدعي الاختبار الحقيقي كالسابق.
- `GET /mail-link/{id}` يُرجع الآن `verified_via` أيضًا (شفافية تشخيصية).

### F2 (متوسط، مخاطرة إنتاج) — `send_tick` يتجاوز نافذة الإرسال متى `MAIL_SINK_SMTP` معرّف
**الملف:** `core/app/sender.py`.

- فحص النافذة أصبح **غير مشروط** بوجود `MAIL_SINK_SMTP` — يستخدم الآن `pacing.is_in_window` بلا شرط `sink_active`، بنفس الطريقة التي يستخدمها `scheduler_main.run_queue_builder_job` تمامًا (نفس الهدف المذكور بالتكليف: "reuse the queue_builder helper").
- `sender._send_window_override_active()` (جديدة): تجاوز تطويري وحيد — `MAIL_IGNORE_SEND_WINDOW=true` **و** `is_dry_run()` معًا. بوضع `MAIL_LIVE=true` لا يعمل التجاوز إطلاقًا مهما كانت قيمة `MAIL_IGNORE_SEND_WINDOW`.
- `ignore_window=True` الصريح (يُستخدم فقط من `/admin/mail/send-now`) يبقى يعمل بلا تغيير.
- `docker-compose.yml`: تمرير `MAIL_IGNORE_SEND_WINDOW: ${MAIL_IGNORE_SEND_WINDOW:-false}` لكلتا `core`/`core-scheduler`.
- **ملاحظة:** توثيق `MAIL_IGNORE_SEND_WINDOW` في `.env.example` **لم يُنشر** — مسار `.env.example` مرفوض صراحة عبر `repo_write` (رسالة الخادم: "مسار غير مسموح به")، على الأرجح حارس أمان مرتبط بحظر `.env` نفسه. المتغيّر موثّق بالكامل داخل docstring `sender.py` (`_send_window_override_active`) وبمحتوى الريبو المحلي (لم يُدفَع)، ويحتاج المالك إضافته يدويًا لـ.env.example عند الاطّلاع على هذا التقرير أو عبر جلسة بصلاحية أوسع.

### F3 (منخفض) — `core-scheduler` يظهر "unhealthy" بسبب healthcheck HTTP موروث
**الملف:** `docker-compose.yml`.

- أُضيف `healthcheck: { disable: true }` لخدمة `core-scheduler` (تُبنى من نفس `core/Dockerfile` مثل `core` فترث `HEALTHCHECK` الذي يفحص `http://localhost:8000/health`، لكنها عملية خلفية فقط بلا منفذ HTTP). لا تغيير آخر بالملف.

---

## بوابات الجودة (محليًا، قبل كل دفعة)

| البوابة | قبل | بعد |
|---|---|---|
| `python -m compileall -q core` | نظيف | نظيف |
| `import app.main, app.scheduler_main` | ناجح | ناجح |
| `alembic upgrade head` (0001→0006 ثم →0007) | — | نظيف؛ `downgrade -1`/`upgrade head` round-trip نظيف أيضًا |
| `pytest -q` (Postgres 16 محلي حقيقي، لا SQLite) | **194 passed** | **214 passed, 0 failed** (+20: `test_sender_window.py` ×16، `test_mail_link_skip_verify.py` ×4) |
| `docker compose config -q` | — | exit 0 (`POSTGRES_PASSWORD`/`CORE_ADMIN_TOKEN`/`MASAR_DOMAIN` وهمية للتحقق فقط) |

اختبارات جديدة: `core/tests/test_sender_window.py` (وحدة بحتة، بلا DB — تموّه `_claim_due_batch`/`pacing.to_riyadh_naive`) و`core/tests/test_mail_link_skip_verify.py` (Postgres حقيقي، نفس نمط `test_sender_idempotency.py` — يُتخطّى تلقائيًا بلا DB).

---

## تسلسل الدفع والنشر (عبر Masar MCP bridge، 6 commits متتالية)

كل commit ترك `main` قابلًا للاستيراد وخضراء الاختبارات (الترحيل قبل الكود الذي يحتاجه، الاختبارات أخيرًا، تغيير compose منفردًا):

| # | الملف | commit sha |
|---|---|---|
| 1 | `migrations/versions/0007_mail_link_skip_verify.py` | `8d32be25f67a8a8047e9491578fe794d2f0918f5` |
| 2 | `core/app/sender.py` (F2 + `is_dry_run()`) | `a3ad1d0a0971d1f1ae0b01f17b2df8fc08f7f33a` |
| 3 | `core/app/mail_api.py` (F1) | `597546e3510df6aa9fa7289170907500947e395b` |
| 4 | `core/tests/test_sender_window.py` | `cae66f2a9c8f7a14a416f8e2f8909f8ba1c0a4a9` |
| 5 | `core/tests/test_mail_link_skip_verify.py` | `34f080942e9236d735c49871db6dd1bcd6dd87f8` |
| 6 | `docker-compose.yml` (F3 + `MAIL_IGNORE_SEND_WINDOW`) | `2f3e6c422fc1e0e863a46f41292bae535343cf18` |

بين commit #1 و#3 جرى تحقّق حيّ عبر `ops psql` أن `alembic_version = 0007_mail_link_skip_verify` قبل دفع الكود المعتمد على العمود الجديد (autodeploy يشغّل `alembic upgrade head` تلقائيًا مع كل نشر).

---

## تحقّق النشر الحي (بعد آخر commit)

- `GET /health` → `200 {"status":"ok","service":"masar-core","version":"0.3.0"}` (`00:05:30Z`).
- `ops ps`: `core` **Up (healthy)**؛ `core-scheduler` **Up** (بلا لاحقة `(unhealthy)` — الإصلاح F3 يعمل)؛ `postgres`/`mailpit` healthy؛ لا تغيير على بقية الخدمات.
- `ops deploy-log 40` يُظهر تسلسل النشر الأخير (يقابل commit #6، دفعة docker-compose.yml): إيقاف `core-scheduler` → `alembic upgrade head` → إعادة تشغيله → **"تم النشر بنجاح"** (`2026-09-09T00:00:32Z`).

---

## غير منجَز / يحتاج متابعة المالك

1. **`.env.example` لم يُحدّث على الخادم** — التوثيق (`MAIL_IGNORE_SEND_WINDOW=false`) موجود محليًا فقط بهذه الجلسة المعزولة؛ `repo_write` رفض المسار صراحة. لا أثر وظيفي (المتغيّر يعمل عبر `docker-compose.yml` بصرف النظر عن `.env.example`)، لكن يستحق إضافة سطر يدوي لاحقًا.
2. الاستنتاج عالٍ 1 الأصلي بالمراجعة الحيّة أشار أيضًا إلى أن اختبار المسار الكامل الحقيقي (send_builder→sender→mailpit **بـ`mail_link.status='ok'` عبر `skip_verify`**، بما فيه CC الفعلي وقارئ الوارد) لم يُنفّذ حيًّا بعد بهذه الجلسة — F1 يفتح المسار تقنيًا (`skip_verify=true` بوضع DRY_RUN)، لكن التحقّق الحيّ الفعلي عبره متروك لمراجعة تالية (خارج نطاق "تنفيذ الإصلاحات الثلاثة" المُكلّف به هنا).
3. لم تُلمَس `DRY_RUN`/`MAIL_LIVE` على الخادم الحي إطلاقًا — يبقى بوضع dry-run/sink كما كان.
