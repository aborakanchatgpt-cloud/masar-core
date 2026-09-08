# B4 — تقرير المنفّذ: تصحيحات مراجعة B4 الأوفلاين

**التاريخ:** 2026-09-08
**المنفّذ:** Claude (Sonnet 5، جلسة Cowork فرعية)
**المرجع:** `docs/reports/B4-offline-review.md` (حكم: REJECT) + `PLAN.md` قسم B4 وبروتوكول القسم 0.
**النطاق:** كل التصحيحات Critical/High بالتقرير + Medium (المطلوبة صراحة بالتكليف) + Low 2/3 (بسيطة وآمنة). لم يُنشَر على `masar-core-1` الحي بعد — تحقّق محلي فقط (compileall/imports/pytest/migrations/compose config).

---

## 1. ما تغيّر (حسب رقم النتيجة بتقرير المراجعة)

### Critical 1 — `MAIL_FERNET_KEY` لا يصل للحاوية
**`docker-compose.yml`**: أُضيف `MAIL_FERNET_KEY: ${MAIL_FERNET_KEY:-}` (وبقية متغيرات البريد أدناه) لقسم `environment:` بكلتا خدمتي `core` و`core-scheduler`. `deploy/autodeploy.sh` كان يولّد المفتاح بـ`.env` على المضيف أصلًا (لم يُلمَس) — الفجوة كانت فقط بعدم تمريره لبيئة الحاوية.

### Critical 2 — لا خدمة `mailpit`
**`docker-compose.yml`**: خدمة `mailpit` جديدة (صورة `axllent/mailpit`)، **شبكة `masar_internal` فقط** (لا `masar_public`، لا `ports:` — فقط `expose: [1025, 8025]` داخل الشبكة الداخلية، لا نشر علني إطلاقًا). `core`/`core-scheduler` يحصلان أيضًا على:
- `MAIL_SINK_SMTP: ${MAIL_SINK_SMTP:-mailpit:1025}` (افتراضي يستهدف mailpit تلقائيًا — "عبر env defaults" كما بالتكليف)
- `MAIL_SINK_API_URL: ${MAIL_SINK_API_URL:-http://mailpit:8025}`
- `DRY_RUN_TO: ${DRY_RUN_TO:-}`
- `MAIL_LIVE: ${MAIL_LIVE:-false}`

نقطة القراءة الإدارية لعدّ/عرض رسائل sink كانت **موجودة أصلًا** بـ`core/app/mail_api.py` (`GET /admin/mail/sink/messages`، تُنادي واجهة mailpit HTTP API `GET /api/v1/messages` عبر `httpx` من داخل الشبكة، خلف توكن الإدارة) — لم تكن بحاجة لإضافة، فقط لخدمة `mailpit` نفسها والمتغيرات لتفعيلها.

### Medium 2 — لا تخزين دائم/مشترك لملفات CV
**`docker-compose.yml`**: volume مُسمّى جديد `cv_data`، مُركَّب على `/data/cv` بكلتا `core`/`core-scheduler`، مع `CV_DATA_DIR: /data/cv` صريحًا بالبيئة. يحلّ فقدان مرفق PDF بصمت بين `queue-now` اليدوي (حاوية core) وإرسال تلقائي لاحق (core-scheduler) عبر إعادة نشر بينهما.

### High 1 (F1) — نافذة ازدواج إرسال حقيقية
**`core/app/sender.py`**:
1. `_claim_due_batch`: الشرط أصبح `status IN ('queued', 'sending')` بدل `status = 'queued'` وحدها — يُفعِّل فعليًا "شبكة الأمان" التي يصفها تعليق الدالة أصلًا (كانت معطَّلة فعليًا: صفّ عالق بحالة `'sending'` بعد انهيار عملية لم يكن يُستعاد أبدًا، فقط "يُفقَد" لا "يُزدوَج").
2. دالة جديدة `_existing_application(conn, row)`: قبل أي اتصال SMTP جديد لصفّ مُستعاد، تتحقق من وجود صفّ `applications` ناجح أصلًا لنفس `(customer_id, job_id, opportunity_id)` — إن وُجد (يعني: الرسالة خرجت فعليًا بمحاولة سابقة قبل الانهيار)، `_mark_already_sent` تُعلِّم `send_queue`/`opportunities` كـ`sent` مباشرة **بلا** اتصال SMTP جديد ولا إدراج `applications`/`company_cooldowns` مكرَّر. `SKIP LOCKED` لم يُلمَس (كما طلب التكليف).
3. فهرس داعم جديد `ix_applications_customer_job_opportunity` (migration `0006_b4_fixes`) — الاستعلام الجديد ساخن (يُنادى لكل صفّ مُستعاد).

### High 2 — `opportunities.status='sent'` يُضبط وقت البناء لا الإرسال
**`core/app/send_builder.py`**: `UPDATE opportunities SET status = 'sent'` → `status = 'queued'`.
**`core/app/sender.py`**: `_mark_success` تضبط `opportunities.status='sent'` فقط بعد نجاح SMTP فعليًا؛ `_mark_failure` (فرع `MAX_ATTEMPTS`) تضبطها `'skipped'` عند فشل نهائي.
**`core/app/planner.py`**: `_fetch_existing_planned` (يستبعد وظائف/شركات مخطَّطة اليوم من مرشّحي التخطيط اللاحق) وسّعت الفلتر من `('planned','sent')` إلى `('planned','queued','sent')` — بلا هذا التوسيع، فرصة بحالة `queued` (بالطابور، لم تُرسَل بعد) كانت ستُعتبَر "غير موجودة" وقد تُعاد كمرشّح مكرَّر بدورة تخطيط لاحقة بنفس اليوم.
**`migrations/versions/0006_b4_fixes.py`**: يضيف `'queued'` لقيد `ck_opportunities_status` (كان يرفض القيمة سابقًا — بلا هذا الترحيل، `send_builder.py` الجديد كان سيفشل بكل إدراج فعليًا).

### Medium 3 — حارس تحميل الراوترات يلتقط `ImportError` فقط
**`core/app/main.py`**: أُضيف فرع `except Exception` (بعد `except ImportError`) مع `logger.exception` — يحمي `/health` وكل نقاط B1/B2/B3 من أي عطل آخر (SyntaxError إلخ) بملف B4 وحده. هذا **حدث فعليًا بالإنتاج سابقًا** (commit موثَّق بالتقرير: "B4 hotfix: comment out stray line in mail_api.py — أسقط كامل الخدمة").

### Medium 1 — صفر اختبارات B4 رغم توثيق داخلي يدّعي وجودها
ثلاثة ملفات جديدة (تفاصيل العدد بقسم 2 أدناه):
- `core/tests/test_pacing.py` — تحويل التوقيت ذهابًا وإيابًا، حدود نافذة الإرسال (شاملة عند 08:00/16:30 بالضبط)، الأحد-الخميس صراحة + جمعة صراحة (حتى مع ساعات ضمن النطاق)، حدود وتيرة الإحماء 6/12/16/22 بالضبط عند `age_days` الحدّية، الهدف اليومي 17-22، `next_slot`/`deterministic_rng`.
- `core/tests/test_inbox_classifier.py` — `is_bounce`/`is_interview_mention`/`classify_kind` بعيّنات عربية وإنجليزية، أولوية `bounce > interview > reply > other` صراحة (بما فيها حالة جسم DSN يتضمّن كلمة "مقابلة")، `extract_original_message_id` (آخر تطابق لرسالة متداخلة).
- `core/tests/test_sender_idempotency.py` — **ضد Postgres 16 حقيقي محلي** (لا SQLite/تزييف — `SELECT...FOR UPDATE SKIP LOCKED` لا يمكن اختباره بصدق بمحاكٍ): يتخطّى (`skip`، لا فشل) بأمان إن تعذّر الاتصال. يغطي: مسار النجاح الأساسي (تطبيق واحد + `opportunities='sent'`)، سيناريو F1 كاملًا (تطبيق ناجح موجود أصلًا لصفّ مُستعاد → `_smtp_send` لا يُستدعى إطلاقًا، يُتحقَّق بـ`monkeypatch` يرفع `AssertionError` لو استُدعيت)، استرداد `_claim_due_batch` الفعلي لصفّ `'sending'` عالق (وعدم استرداد صفّ بقفل لا يزال ساري المفعول)، وفشل نهائي يضبط `opportunities='skipped'`.

### Low 2 (فهرس applications.message_id) و Low 3 (بلا LIMIT بمرشّحي البناء)
أُصلحا معًا كتصحيحات آمنة زهيدة التكلفة (ليسا قراري منتج، ولم يستدعيا نقاشًا):
- `migrations/versions/0006_b4_fixes.py`: فهرس `ix_applications_customer_message` (`customer_id, message_id`) — يخدم استعلام ارتداد `inbox.py` الحالي.
- `core/app/send_builder.py`: `MAX_CANDIDATES_PER_CUSTOMER = 300` + `LIMIT` على استعلام `_fetch_candidate_opportunities` — الترتيب `score DESC` أصلًا يضمن مرور أفضل المرشّحين أولًا، فلا يتأثر السلوك الفعلي، فقط الحدّ الأقصى لاستهلاك الذاكرة لعميل واحد متراكم.

---

## 2. ما لم يُصلَح عمدًا (مع السبب)

| البند | لماذا تُرك |
|---|---|
| **Low 1** — استدعاءات إدارية متزامنة (blocking) داخل `async def` بـ`mail_api.py` (`queue_now`/`send_now`) | المراجعة نفسها توصي بمعالجة موحّدة لاحقًا (نفس النمط موجود مسبقًا بـ`customers_api.py:/plan/run-now` منذ B3) — ليس عيبًا مستحدَثًا خاصًا بـB4، وإصلاح جزئي هنا وحده يترك التناقض قائمًا مع بقية الكود. يحتاج قرار تصميم موحَّد (`run_in_threadpool` بكل نقاط `/admin/*/run-now`/`queue-now`/`send-now` معًا). |
| Live checks (7 بنود بتقرير المراجعة، §"فحوصات لا تزال تحتاج خادمًا حيًّا") | خارج نطاق قابلية التنفيذ بجلسة أوفلاين محليًا بالكامل: `mail-link` بـGmail App Password حقيقي، معدّل تصريف تحت حمل فعلي، فحص بصري لرسالة mailpit كاملة (مرفق PDF)، سلوك F1 عبر دورات autodeploy حقيقية متتالية، IMAP على 1,500 صندوق فعلي، تصنيف Gmail لحجم الإرسال، وفكّ تشفير أسرار مخزَّنة سابقًا بعد إضافة `MAIL_FERNET_KEY`. **تتطلب نشرًا فعليًا على `masar-core-1` ومراجعًا بوصول حي.** |

---

## 3. أعداد الاختبار

| المرحلة | العدد | ملاحظة |
|---|---|---|
| قبل (خط الأساس، من تقرير المراجعة) | 108 نجحت / 108 | `test_matching.py`، `test_normalizer.py`، `test_discovery_grouping.py` فقط — صفر لـB4. |
| بعد | **194 نجحت / 194** | +45 (`test_pacing.py`) +36 (`test_inbox_classifier.py`) +5 (`test_sender_idempotency.py`، ضد Postgres 16 حقيقي محلي). |

---

## 4. بوابات الجودة (كلها خضراء محليًا)

- `python -m compileall -q core` → 0 أخطاء.
- `cd core && python -c "import app.main, app.scheduler_main"` → استيراد نظيف.
- `cd core && python -m pytest -q` → **194 passed**.
- سلسلة الترحيل الكاملة (Postgres 16 محلي، `masar_test` role/db أُنشئا لهذا الغرض): `0001→0006` نجحت، `alembic downgrade -1` ثم `upgrade head` نظيفان بلا أخطاء (يُعيد `0006_b4_fixes` صفوف `'queued'` لـ`'planned'` قبل إعادة القيد القديم، ثم يعيدها التطبيق التالي).
- `docker-compose.yml`: تحقّق Python/PyYAML (كل خدمة/`depends_on`/مرجع volume موجود فعليًا) **و** `docker compose config --quiet` (Docker Compose v5.1.3 متاح فعليًا بهذه البيئة) → exit 0 بلا أخطاء بعد تمرير `POSTGRES_PASSWORD`/`CORE_ADMIN_TOKEN`/`MASAR_DOMAIN` كمتغيرات بيئة وهمية (نفس ما يتطلبه الملف أصلًا بـ`.env` الحقيقي).

---

## 5. ملفات جديدة/معدَّلة (هذا الالتزام)

- `docker-compose.yml` (معدَّل) — mailpit + متغيرات البريد + `cv_data` volume.
- `.env.example` (معدَّل) — توثيق المتغيرات الجديدة (معلَّقة، القيم الفعلية بـ`.env` على الخادم فقط).
- `core/app/sender.py` (معدَّل) — idempotency + `_claim_due_batch` + تحديثات `opportunities.status`.
- `core/app/send_builder.py` (معدَّل) — `status='queued'` + `LIMIT` مرشّحين.
- `core/app/planner.py` (معدَّل) — فلتر `_fetch_existing_planned` يشمل `'queued'`.
- `core/app/main.py` (معدَّل) — `except Exception` بحارس تحميل الراوترات.
- `migrations/versions/0006_b4_fixes.py` (جديد) — قيد `opportunities.status` + فهرسا `applications`.
- `core/tests/test_pacing.py` (جديد، 45 اختبارًا).
- `core/tests/test_inbox_classifier.py` (جديد، 36 اختبارًا).
- `core/tests/test_sender_idempotency.py` (جديد، 5 اختبارات، Postgres حقيقي).
- `docs/reports/B4-offline-review.md` (مضاف للالتزام — كان untracked).
- `PLAN.md` (معدَّل) — سطر حالة B4 (WIP بدل TODO) + سطرا سجلّ بالقسم 3.

---

## 6. الخطوة التالية (لا تخص هذا الالتزام)

نشر فعلي على `masar-core-1` (commit عبر جسر MCP/n8n → `docker compose up -d --build` يلتقط `mailpit`/`cv_data`/متغيرات البريد الجديدة تلقائيًا عبر autodeploy الحالي) ثم `alembic upgrade head` على قاعدة الإنتاج، فمراجعة حيّة تتحقق من بنود §"فحوصات لا تزال تحتاج خادمًا حيًّا" بتقرير `B4-offline-review.md` قبل تعليم B4 كـ`DONE` بـ`PLAN.md` (بروتوكول القسم 0، بند 6: "لا يُعلَّم DONE إلا بدليل").
