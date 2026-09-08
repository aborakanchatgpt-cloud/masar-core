# B3/B4 — مراجعة حيّة (Live Review) على masar-core-1

**التاريخ:** 2026-09-08 (فحوصات 22:16Z–22:44Z UTC)
**المراجع:** Fable (جلسة فرعية Sonnet 5، وصول حيّ عبر Masar MCP bridge — بلا SSH)
**النطاق:** تنفيذ فحوصات §"لا تزال تحتاج خادمًا حيًّا" بتقرير `docs/reports/B4-offline-review.md`،
بعد نشر `docs/reports/B4-executor-fixes.md` (PR #1 "B4 fixes"، commit `ca0b391`).
**معيار القبول المرجعي:** `PLAN.md` بنود B3/B4 + `docs/EXECUTION_GUIDE.md`.

---

## الحكم: **ACCEPT-WITH-FIXES**

كل تصحيحات Critical/High من المراجعة الأوفلاين (`MAIL_FERNET_KEY`، خدمة `mailpit`،
idempotency، `opportunities.status`) **منشورة فعليًا ومؤكَّدة حيًّا** — التسلسل
`مسار الإرسال → mailpit` يعمل فعليًا (12/12 رسالة وصلت، بمرفق PDF، بلا ازدواج
عند إعادة المحاولة). لا يوجد عطل حي يمنع الاعتماد. **لكن**: التحقق الحيّ الكامل
للمسار الحقيقي (عميل حقيقي → `mail_links.status='ok'` → `send_builder` →
`sender` → mailpit، بما فيه CC الفعلي) **تعذّر فعليًا** في هذه البيئة لأن بوابة
التحقق الحقيقي لـSMTP/IMAP (`mail_api.create_mail_link`) لا يمكن اجتيازها بلا
حساب Gmail اختباري حقيقي — `mailpit` لا يوفّر IMAP إطلاقًا، وأي محاولة SMTP/IMAP
حقيقية بكلمة مرور وهمية تفشل بتصميم fail-closed صحيح. هذا **ليس عطلاً بالكود**
بل فجوة اختبار بيئية حقيقية (راجع الاستنتاج 1 أدناه) — تحتاج NEEDS-OWNER. بقية
النتائج إيجابية بوضوح، لذا الحكم ACCEPT-WITH-FIXES لا REJECT.

---

## جدول الفحوصات الحيّة

| # | الفحص | النتيجة | الدليل |
|---|---|---|---|
| 1.1 | `/health` | **PASS** | `200 {"status":"ok","service":"masar-core","version":"0.3.0"}` (22:25:32Z) |
| 1.2 | حاويات compose (`ops ps`) | **PASS مع ملاحظة** | `core` (healthy)، `mailpit` (healthy)، `postgres` (healthy)، `core-scheduler` **(unhealthy)** — جذر السبب مؤكَّد بقراءة كود (انظر الاستنتاج 2)، لا عطل فعلي (سجلّات نظيفة، تكات مستمرة بلا كسر). |
| 1.3 | إصدار alembic | **PASS** | `psql "SELECT version_num FROM alembic_version"` → `0006_b4_fixes` |
| 1.4 | `/admin/mail/stats` | **PASS** | `200` يرجع بنية صحيحة (فارغة الآن بعد التنظيف — راجع "بيانات اختبار متروكة") |
| 1.5 | `MAIL_FERNET_KEY` يصل للحاوية | **PASS** | `ops env-keys` يُدرج `MAIL_FERNET_KEY=` ضمن مفاتيح البيئة المعرَّفة بحاوية core (لا قيمة تُطبع، فقط اسم المفتاح — امتثال لقاعدة عدم طباعة الأسرار) |
| 1.6 | خدمة `mailpit` معرَّفة وتعمل | **PASS** | `axllent/mailpit` بحالة healthy، شبكة `masar_internal` فقط، `/admin/mail/sink/messages` يستجيب فعليًا (راجع 3.x) |
| 2.1 | بيانات B3 حقيقية (customers/jobs/opportunities/profiles/wallets/companies) | **PASS** | `customers=1، jobs=11091، opportunities=14، profiles=1، wallets=1، companies=104` (عميل اختبار متبقٍّ من B3: `id=1 "B3 QA Test - Process Engineer"`, `status=active`, `wallet_balance=100`, 14 فرصة `planned` بتاريخ `2026-09-08`) |
| 2.2 | `/admin/stats` (اكتشاف B2 حيّ) | **PASS** | `jobs_in_region=1328، jobs_sa=978، companies_total=104، sources_active=48`، آخر جولة اكتشاف ناجحة (`sources_ok=49/49`, `jobs_fetched=6344`) — البنية التحتية للمطابقة سليمة وتُغذّى بيانات حقيقية باستمرار |
| 2.3 | `GET /plan/{id}/today` (نقطة المخطِّط) | **PASS** | `GET /plan/1/today?day=2026-09-08` → `200`، 14 فرصة بدرجات/طبقات/أسباب كاملة (مثال: `job_id=1101, score=0.6914, tier=B, title="Chemical Engineer"`) — النقطة تعمل فعليًا على بيانات حقيقية، لا بيانات وهمية |
| 3.1 | اتصال mailpit عبر `/admin/mail/sink/messages` | **PASS** | `200` بنية `{"total":0,...}` قبل الاختبار — الاتصال الداخلي core→mailpit:8025 يعمل |
| 3.2 | إنشاء `mail-link` باختبار SMTP/IMAP حقيقي (كلمة مرور وهمية) | **PASS (سلوك متوقَّع fail-closed)** | `POST /mail-link` لعميل `id=1` بعنوان `b3-qa-test@example.invalid` وكلمة مرور وهمية ضد `smtp.gmail.com`/`imap.gmail.com` الحقيقيين → `200 {"ok":false,"status":"failed","error":"فشل تسجيل الدخول SMTP — تحقق من العنوان/كلمة مرور التطبيق"}` — رسالة عامة بلا أي أثر لكلمة المرور، **مطابق تمامًا للتصميم الموثَّق** بالمراجعة الأوفلاين. لكن هذا يعني `mail_links.status` يبقى `'failed'` لا `'ok'` (راجع الاستنتاج 1 — فجوة اختبار حقيقية، لا عطل). |
| 3.3 | بناء طابور الإرسال الحقيقي (`POST /admin/mail/queue-now?customer_id=1`) | **BLOCKED (بيئي، غير عطل كود)** | `200 {"customers_processed":0,"queued":0}` — العميل `id=1` مستبعد كليًا من `send_builder._fetch_active_customers_with_mail` لأن الاستعلام يشترط `ml.status='ok'` صراحة (`core/app/send_builder.py`, الدالة نفسها) — لا مسار لتجاوزه بلا `mail_link` حقيقي ناجح. **لا يمكن اختبار CC/المرفق الحقيقي عبر المسار الكامل (فرصة→طابور→إرسال) بهذه البيئة دون حساب Gmail اختباري حقيقي.** |
| 3.4 | تدفّق sink الكامل عبر `/admin/mail/load-test` + `/admin/mail/send-now` (بديل يختبر آلية النقل نفسها بلا حاجة لـ`mail_link`) | **PASS** | `POST /admin/mail/load-test {"customers":1,"per_customer":12}` → `{"customers_created":1,"rows_queued":12}` (عميل اختبار جديد `id=2 "Load Test ef320c66 #0"`, `status=paused`). ثم `POST /admin/mail/send-now?limit=50` → `{"claimed":12,"sent":12,"failed":0}` |
| 3.5 | وصول ≥10 رسائل لـmailpit بمرفق PDF | **PASS** | `GET /admin/mail/sink/messages` بعد الإرسال → `total=12` رسالة، كل واحدة `"Attachments":1` (PDF placeholder، `%PDF-1.4` حقيقي البنية — راجع `mail_api.py:load_test`)، `Message-ID` فريد لكل صف (`masar-1@masar.local` … `masar-12@masar.local`)، `From: customer-2@masar.local` (`resolve_transport` بنمط sink يعمل بلا `mail_link` حقيقي كما هو موثَّق) |
| 3.6 | CC للعميل على كل رسالة | **NOT LIVE-VERIFIED عبر mailpit / PASS عبر قراءة الكود** | صفوف `load-test` تُدرَج بـ`cc_email=None` عمدًا (تصميم الأداة — تقيس معدّل تصريف فقط، لا منطق تركيب رسالة حقيقي). التحقق الفعلي من CC يتطلّب المسار الحقيقي المحظور بالبند 3.3. **تحقّق بالكود بدلًا من ذلك**: `core/app/send_builder.py` بدالة `build_queue_for_customer`، سطر إدراج `send_queue`: `"cc_email": customer.get("mail_address")` (يُشتق من `ml.address AS mail_address` بالاستعلام) — و`sender.py::_process_row`: `recipients = [actual_recipient] + ([cc_email] if cc_email else [])` **دومًا** بصرف النظر عن وضع النقل (sink/dry-run/حقيقي). المنطق سليم بقراءة الكود، غير مُتحقَّق حيًّا بمثال رسالة فعلية بهذه الجلسة. |
| 3.7 | Idempotency: إعادة تشغيل send-now بلا ازدواج | **PASS** | استدعاء ثانٍ لـ`POST /admin/mail/send-now?limit=50` بعد نجاح الأول → `{"claimed":0,"sent":0,"failed":0}` — لا شيء يُطالَب به مجددًا (كل الصفوف `status='sent'` أصلًا). `psql`: كل الـ12 صفًا `status=sent, attempts=0, message_id` فريد — لا صفوف مكرَّرة، لا رسائل إضافية بmailpit (يبقى `total=12`، لم أُعِد فحصه بعد الاستدعاء الثاني لعدم وجود إرسال جديد أصلًا). |
| 3.8 | انتقالات حالة `send_queue` (queued→sent) | **PASS** | `psql` قبل الإرسال: 12 صفًا `status='queued'`؛ بعده: 12 صفًا `status='sent'`, `attempts=0` (نجاح من أول محاولة) |
| 3.9 | سلوك نافذة الإرسال أحد-خميس 08:00-16:30 الرياض (خارج النافذة حاليًا فعليًا: 22:44Z UTC = 01:44 الرياض) | **PASS جزئي مع ملاحظة تصميم مهمة** | **بناء الطابور** (`run_queue_builder_job`، كل 10د) محكوم بالنافذة فعليًا ومؤكَّد بالسجلّات الحيّة: `"queue_builder: خارج نافذة الإرسال — تخطّي هذه الدورة"` (سجّل مرتين، 22:12Z و22:26Z). **الإرسال الفعلي** (`sender.send_tick`) **يتجاوز فحص النافذة كليًا** طالما `MAIL_SINK_SMTP` معرَّف (`sender.py::send_tick`: `sink_active = bool(MAIL_SINK_SMTP); if not sink_active and not is_in_window(...): return`) — وبما أن `MAIL_SINK_SMTP=mailpit:1025` هو **الافتراضي الدائم** بـ`docker-compose.yml` الحالي، فكل تكة إرسال تلقائية (كل دقيقة، 24/7) تتجاوز النافذة أيضًا لا فقط `/admin/mail/send-now` اليدوية — مؤكَّد بسجلّات `core-scheduler`: عشرات التكات "نتيجة دورة الإرسال" بين 22:03Z و22:29Z (خارج النافذة تمامًا) نفّذت بنجاح (لم تُرفَض بسبب النافذة؛ كانت `claimed:0` فقط لأن الطابور كان فارغًا وقتها). هذا **سلوك تصميم متعمَّد وموثَّق** (`sender.py` تعليق صريح)، لا عطل — لكنه يعني أن **انضباط النافذة الزمنية للإرسال الفعلي لم يُختبَر حيًّا إطلاقًا بهذه البيئة** (لأنها دومًا بوضع sink)، وأن الانتقال للإنتاج الحقيقي يتطلّب من المشغّل **إزالة `MAIL_SINK_SMTP` صراحة من `.env`** — غيابه سيبقي النظام بوضع sink للأبد بصمت. راجع الاستنتاج 3. |
| 4 | قارئ الوارد (Inbox reader) | **NOT LIVE-TESTED** | لا خُطّاف/نقطة اختبار لحقن رسائل ارتداد/رد اصطناعية بـ`core/app/inbox.py` أو `inbox_api.py` (تحقّق بقراءة الملفين كاملين) — المسار الحيّ الوحيد `POST /inbox/run-now` يشترط `mail_links.status='ok'` أيضًا (نفس استعلام `send_builder`، `inbox.py::run_inbox_round`: `WHERE status = 'ok'`) — نفس عائق البند 3.3 بالضبط. مُنفَّذ فعليًا: `POST /inbox/run-now` → `{"mailboxes":0,"processed":0,"bounces":0}` (متوقَّع، لا صناديق نشطة). منطق التصنيف (`is_bounce`/`classify_kind`/`extract_original_message_id`) مُتحقَّق فقط بـ36 اختبار وحدة أوفلاين (`core/tests/test_inbox_classifier.py`، حسب `B4-executor-fixes.md`) — **غير مُتحقَّق حيًّا بمثال IMAP فعلي بهذه الجلسة**. |
| 5 | تنظيف بيانات الاختبار | **جزئي عبر API متاح** | `DELETE /mail-link/1` → `{"ok":true,"deleted":1}` (نُفِّذ). لا نقطة حذف لعملاء أو صفوف `send_queue` — تُركت (راجع القسم التالي). |

---

## الاستنتاجات (مرتّبة حسب الخطورة)

### 🟠 عالٍ 1 — بوابة التحقق الحقيقي لـ`mail-link` لا يمكن اجتيازها بهذه البيئة (NEEDS-OWNER)
**الملف:** `core/app/mail_api.py::create_mail_link` (يستدعي `_test_smtp`/`_test_imap` حقيقيين)
مقابل `core/app/send_builder.py::_fetch_active_customers_with_mail`
(`WHERE c.status='active' AND ml.status='ok'`) و`core/app/inbox.py::run_inbox_round`
(`WHERE status='ok'`).

المشكلة: تعليمات هذه المراجعة افترضت أن استخدام كلمة مرور وهمية مع `DRY_RUN`
مفعّل يكفي لاختبار المسار الكامل ("لا تُستخدَم أبدًا طالما `DRY_RUN` مفعّل").
هذا **غير دقيق بالكود الفعلي**: `create_mail_link` يفرض نجاح SMTP+IMAP
**حقيقيين** (اتصال فعلي بـ`smtp.gmail.com`/`imap.gmail.com` الافتراضيين، أو
أي مضيف يُمرَّر) قبل تسجيل `status='ok'` — بصرف النظر عن `DRY_RUN`/`MAIL_SINK_SMTP`
كليًا (فحص منفصل تمامًا، لا علاقة له بمنطق `resolve_transport`). و`mailpit`
**لا يوفّر خادم IMAP إطلاقًا** (`docker-compose.yml`: `expose: [1025, 8025]`
فقط — SMTP وHTTP API، لا IMAP)، فلا يوجد أي مضيف بهذه البيئة يمكن أن يُنجح
كلا الاختبارين معًا بكلمة مرور وهمية. النتيجة العملية: **لا يمكن الوصول
لـ`mail_links.status='ok'` بهذه البيئة إطلاقًا** بلا حساب Gmail اختباري حقيقي
(عنوان + كلمة مرور تطبيق فعليين) — ما يمنع اختبار المسار الكامل (فرصة حقيقية
→ `send_builder` → `sender` → mailpit بCC حقيقي) وأيضًا يمنع اختبار قارئ الوارد
حيًّا (نفس الشرط بالضبط).

**هذا ليس عطلاً بالكود** — التصميم fail-closed صحيح ومقصود (لا يُخزَّن صندوق
فاشل كـ'ok'). لكنه **فجوة اختبار بيئي حقيقية** تستحق توثيقًا صريحًا بدل افتراض
خاطئ بالتكليف مستقبلًا.

**التوصية:** `نحتاجك فورا` — حساب Gmail اختباري واحد (عنوان + App Password)
يُدخَل عبر `POST /mail-link` مباشرة (لا في الشات، لا بـ.env) ليكمل مراجع لاحق
البنود 3.3/3.6 وقارئ الوارد فعليًا. بديل أرخص: نقطة إدارية جديدة محمية
(`POST /admin/mail-link/{id}/force-ok`؟) تُفعَّل فقط حين `MAIL_SINK_SMTP` معرَّف
— قرار منتج يحتاج نقاشًا، ذكرته هنا كخيار لا توصية ملزمة.

### 🟡 متوسط 1 — `core-scheduler` يظهر "unhealthy" دومًا بسبب healthcheck موروث غير مناسب
**الملف:** `core/Dockerfile` (`HEALTHCHECK` يفحص `http://localhost:8000/health`)
مقابل `docker-compose.yml` (خدمة `core-scheduler`: `command: ["python", "-m", "app.scheduler_main"]`
— لا خادم HTTP إطلاقًا، فقط حلقة `APScheduler.BlockingScheduler`). كلا الخدمتين
`core`/`core-scheduler` تُبنيان من نفس `core/Dockerfile` (`build: context: ./core`)
فتَرثان نفس تعليمة `HEALTHCHECK` رغم أن `core-scheduler` لا تفتح المنفذ 8000
إطلاقًا — الفحص يفشل دومًا بالتصميم، لا بعطل فعلي. **مؤكَّد حيًّا بلا لبس**:
`ops logs core-scheduler 200` يُظهر تكات ناجحة متواصلة بلا انقطاع (كل دقيقة
إرسال، كل 10 دقائق بناء طابور، جولة اكتشاف كاملة ناجحة 49/49 مصدر) — **لا
كسر فعلي، فقط علم Docker خاطئ**. **الإصلاح:** أضف لخدمة `core-scheduler`
بـ`docker-compose.yml`:
```yaml
healthcheck:
  disable: true
```
أو فحصًا مناسبًا فعليًا (`test: ["CMD-SHELL", "pgrep -f scheduler_main || exit 1"]`).

### 🟡 متوسط 2 — وضع sink (`MAIL_SINK_SMTP`) يُسقط انضباط نافذة الإرسال بالكامل من الإرسال الفعلي، دومًا مفعّل افتراضيًا
راجع تفصيل الفحص 3.9 أعلاه. **الملف:** `core/app/sender.py::send_tick`
(الشرط `sink_active` يتجاوز `pacing.is_in_window` كليًا). **ليس عطلًا** —
موثَّق صراحة بنية الكود كسلوك تطوير مقصود — لكن نتيجتان عمليتان تستحقان
تسجيلًا صريحًا لا يبدو أنه مذكور بوضوح بأي تقرير سابق:
1. طالما `docker-compose.yml` الحالي يمرّر `MAIL_SINK_SMTP: ${MAIL_SINK_SMTP:-mailpit:1025}`
   (قيمة افتراضية غير فارغة)، **لا توجد طريقة لاختبار انضباط نافذة الإرسال
   الفعلي حيًّا إطلاقًا** بهذه البيئة (فقط بناء الطابور مُختبَر بالنافذة،
   لا الإرسال نفسه).
2. الانتقال للإنتاج الحي يتطلّب من المشغّل **إزالة/تفريغ `MAIL_SINK_SMTP`
   صراحة بـ`.env` على الخادم** (لا قيمة افتراضية تلقائية تُزيله) — نسيان هذه
   الخطوة يعني **استمرار كل الإرسال فعليًا لصندوق mailpit الداخلي للأبد
   بصمت** (لا خطأ ظاهر، `send-now`/التكة التلقائية تعمل "بنجاح" دومًا، فقط
   لا يصل شيء للعملاء الحقيقيين — عكس المخاطرة المعتادة، لكن مربِك تشغيليًا
   لو افتُرِض خطأً أن النظام "يعمل" بالإنتاج).

**التوصية:** ليست إصلاح كود إلزاميًا لهذه المراجعة، لكن يستحق سطرًا صريحًا
بـ`RUNBOOK.md` (يُبنى بـB7) تحت عنوان "قبل التفعيل الحي": "تأكد أن
`MAIL_SINK_SMTP` غير معرَّف (فارغ) بـ.env الخادم، وأن `MAIL_LIVE=true`،
وأن `DRY_RUN_TO` فارغ — وإلا يستمر كل الإرسال لـmailpit بصمت".

### 🔴 متوسط 3 — ملاحظة تشغيلية (أثناء هذه الجلسة): `git-remote` رجع فجأة لـHTTPS ومنع الدفع مؤقتًا
محاولة `repo_write` الأولى لنشر هذا التقرير (~22:52Z) فشلت: `ops git` أظهر
`fatal: could not read Username for 'https://github.com'` بعد `commit` ناجح محليًا — أي
أن ريموت git على المضيف كان مضبوطًا مؤقتًا على HTTPS (يتطلّب اعتمادًا تفاعليًا
غير متوفّر) لا SSH بمفتاح النشر الموثّق بـPLAN.md §1/§4 (رغم أن `ops git-remote-ssh` وثّق
`ok` بتاريخ 2026-09-07). النتيجة الفورية وقتها: `git status` أظهر
`Caddyfile`/`PLAN.md`/`README.md`/`REVIEW.md`/`docker-compose.yml` كـ"محذوفة" (` D `) — مقلق فعلي
لوقتٍ، لكن **لا خطر فعلي على البيانات**: كل الملفات ظلّت قابلة للقراءة كاملة عبر
`repo_read` طوال الوقت (لا حذف فعلي على القرص)، والتشخيص الفعلي: commit
محلّي ناجح لم يُدفَع، والذيل نفسه حذّر "next autodeploy tick will discard
it via git reset --hard origin/main" — وفعلًا التكة التالية أعادت
`git status` نظيفًا تلقائيًا (تحقّقتُ لاحقًا). **الإصلاح المُطبَّق فورًا
هذه الجلسة:** أعدتُ تشغيل `ops git-remote-ssh` (نجح، `ok`) فعاد الدفع
يعمل (هذا الملف نفسه نُشر بنجاح بعده). **يستحق تحقيقًا من المالك لاحقًا**:
ما الذي أعاد ضبط remote لـHTTPS بين 2026-09-07 و2026-09-08 (autodeploy
نفسه؟ عملية أخرى؟) — إن تكرر، أي `repo_write` مستقبلي سيفشل بصمت (commit
محلّي فقط، لا دفع) حتى يُكتشَف يدويًا كما حدث هنا.

### 🟢 منخفض 1 — قارئ الوارد NOT LIVE-TESTED (نفس جذر الاستنتاج عالٍ 1)
لا فعل إضافي مطلوب هنا بمعزل عن حل الاستنتاج عالٍ 1 — بمجرد توفر صندوق
Gmail اختباري حقيقي بحالة `mail_link.status='ok'`، شغّل `POST /inbox/run-now`
بعد إرسال بريد ارتداد ذاتي (لعنوان غير موجود) ورد ذاتي، وتحقق من
`GET /inbox/{id}/events` وتحديث `applications.status='bounced'`/استرداد
`wallets.balance`.

---

## بيانات اختبار متروكة (Test data left behind)

تعذّر حذفها لعدم وجود نقطة API مخصّصة (القاعدة: لا حذف مباشر بقاعدة البيانات
عبر `ops psql` — للقراءة فقط SELECT/EXPLAIN/SHOW):

| الجدول | الصف/المعرّف | الحالة | ملاحظة |
|---|---|---|---|
| `customers` | `id=2`, الاسم `Load Test ef320c66 #0` | `paused` | مُستبعَد تلقائيًا من كل معالجة حقيقية (`planner`/`send_builder` يفلتران `status='active'`) — بلا أثر عملي، آمن تركه |
| `send_queue` | `id=1..12`, `synthetic=true`, دفعة `ef320c66` | `sent` | لا `applications`/`company_cooldowns`/`ledger` مرتبطة (مُستبعَدة عمدًا لصفوف synthetic — مؤكَّد: `applications=0`, `company_cooldowns=0` بعد الاختبار) |
| mailpit (صندوق sink) | 12 رسالة، دفعة `ef320c66` | — | صندوق تطوير داخلي فقط (لا شبكة عامة) — يُفرَّغ يدويًا عبر واجهة mailpit أو يُترَك (لا قيمة إنتاجية) |
| `mail_links` | — | — | **نُظِّف**: `DELETE /mail-link/1` نُفِّذ بنجاح خلال هذه الجلسة (`{"ok":true,"deleted":1}`) |

**نُظِّف بنجاح:** سجلّ `mail_links` الوحيد الذي أنشأته هذه الجلسة (لعميل B3
الاختباري `id=1`) — لا أثر متبقٍّ له.

---

## ملاحظة جانبية: حالة B3 بـ`PLAN.md`

`PLAN.md` القسم 2 لا يزال يصنّف B3 كـ`TODO` رغم وجود `docs/reports/B3-executor.md`
(19KB، منفَّذ فعليًا) **ولا يوجد أي `docs/reports/B3-review.md`** — أي أن B3
لم تخضع لمراجعة رسمية كاملة إطلاقًا (بروتوكول القسم 0 بند 6: "لا يُعلَّم DONE
إلا بدليل"، لكن العكس أيضًا صحيح: لا يبقى TODO بلا سبب رغم تنفيذ كامل موثَّق).
**فحوصات B3 بهذه المراجعة محدودة عمدًا لـ"السلامة الحيّة" فقط** (بيانات
موجودة + نقطة المخطِّط تستجيب) كما طلب التكليف — **ليست مراجعة B3 الكاملة**
(لا معيار الحمل: "1500 ملف × 3000 وظيفة < 5 دقائق" عبر `scripts/bench_planner.py`،
ولا مراجعة منطق `matching.py`/الطبقات A/B/C/C2/D بالتفصيل). يستحق B3 مراجعة
مستقلة كاملة منفصلة قبل تعليمه DONE.
