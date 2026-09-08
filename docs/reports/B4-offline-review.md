# B4 — مراجعة مستقلة غير متصلة (Offline Review): الإرسال والوارد

**التاريخ:** 2026-09-08
**المراجع:** Fable (جلسة فرعية Sonnet، مراجعة أوفلاين — لا وصول للخادم الحي)
**النطاق:** `core/app/mail_api.py`, `mail_crypto.py`, `send_builder.py`, `sender.py`,
`composer.py`, `inbox.py`, `inbox_api.py`, `cv_builder.py`, `apply_email.py`,
`pacing.py`, `templates/cv/*.html`, `scheduler_main.py`,
`migrations/versions/0005_b4_mail.py`, `docker-compose.yml`, `deploy/autodeploy.sh`.

**ملاحظة على PLAN.md:** القسم 2 لا يزال يصنّف B4 كـ`TODO` ولا يوجد تقرير
منفّذ (`docs/reports/B4-executor.md`) — لكن `git log` يُظهر 10 commits فعلية
لملفات B4 مكتملة (بما فيها hotfix لخطأ syntax سبق أن أسقط الخدمة بالكامل).
هذه المراجعة تعامل الكود كأنه مطروح فعليًا للمراجعة رغم عدم تحديث PLAN.md/عدم
وجود تقرير منفّذ رسمي — وهذا بحد ذاته ملاحظة بروتوكول (راجع الاستنتاجات).

---

## الحكم: **REJECT**

الكود المنطقي لـB4 (قواعد المطابقة/الجدولة/التشفير/التصنيف) مصمّم بعناية
ومطابق لمعظم القواعد غير القابلة للتفاوض في المنطق البرمجي البحت — لكن
توجد **فجوتان حاسمتان في البنية التحتية (docker-compose.yml)** تجعلان
الميزة **غير قابلة للعمل فعليًا في البيئة المنشورة كما هي الآن** (لا مفتاح
تشفير يصل للحاوية، ولا صندوق sink/mailpit موجود أصلًا)، إضافة إلى **صفر
اختبارات وحدة مخصّصة لكل منطق B4** رغم أن التوثيق الداخلي يدّعي وجودها
صراحة بأسماء ملفات غير موجودة. هذه ليست عيوبًا تجميلية — بند 3.g/a
بالتكليف (mailpit + MAIL_FERNET_KEY) يفشل فشلًا يمكن إثباته بقراءة الملف
فقط بلا حاجة لخادم حي.

---

## نتائج الاختبار

- `python -m compileall -q core` → **0 أخطاء** (لا مشاكل syntax/import حاليًا).
- `cd core && python -m pytest -q` → **108 نجحت / 108** (نفس عدد B3 بالضبط —
  `test_matching.py`, `test_normalizer.py`, `test_discovery_grouping.py` فقط).
  **لا يوجد أي ملف اختبار لمنطق B4** (`test_pacing.py`, `test_inbox_classifier.py`,
  `test_composer.py`, `test_apply_email.py`, `test_mail_crypto.py`, `test_sender.py`
  إلخ — لا شيء منها موجود بـ`core/tests/`) رغم أن `pacing.py` و`inbox.py`
  يذكران صراحة بتوثيقهما الداخلي "قابلة للاختبار المباشر" بأسماء ملفات محدَّدة
  توحي بأنها كُتبت ثم لم تُرفع، أو لم تُكتب أصلًا.
- سلسلة الترحيل الكاملة (Postgres 16 حقيقي محليًا، لا SQLite): `0001→0005`
  نجحت بالكامل، والتراجع خطوة واحدة (`downgrade -1`) وإعادة الترقية نظيفان
  بلا أخطاء. الفهارس المتوقعة موجودة فعليًا (`\di` تحقّق مباشر):
  `ix_mail_links_status`, `uq_mail_links_customer_id`,
  `ix_send_queue_status_send_after_locked`, `ix_send_queue_customer_send_after`,
  `uq_inbox_events_customer_message`, `ix_inbox_events_customer_id`,
  `uq_cv_variants_customer_family`.

---

## جدول القواعد غير القابلة للتفاوض

| # | القاعدة | الحالة | الدليل (ملف:سطر) |
|---|---|---|---|
| a | إرسال بالبريد فقط، من صندوق العميل المخصّص، أسرار Fernet مشفّرة، لا نص صريح بالسجلّات | **✅ بالكود / ❌ بالبنية التحتية** | تشفير: `core/app/mail_crypto.py:34-53`. لا تسجيل سرّ: `mail_api.py:42-65` (رسائل خطأ عامة فقط). **لكن**: `docker-compose.yml` لا يمرّر `MAIL_FERNET_KEY` لخدمتي `core`(38-77)/`core-scheduler`(79-98) رغم أن `deploy/autodeploy.sh:59-63` يولّده بـ`.env` على المضيف فقط — لا `environment:` ولا `env_file:` يربطه بالحاوية. أي استدعاء لـ`encrypt_secret`/`decrypt_secret` بالإنتاج يرفع `MailCryptoUnavailable` دومًا. |
| b | CC للعميل على كل تقديم | **✅ DONE** | `send_builder.py:391` (`cc_email: customer.get("mail_address")`) + `sender.py:322-325` (تُضاف دومًا لقائمة `recipients` بصرف النظر عن وضع النقل). |
| c | نافذة الإرسال أحد-خميس 08:00-16:30 الرياض فعليًا (لا UTC) | **✅ DONE** | `pacing.py:28-32,84-96` — تحويل صريح ذهابًا وإيابًا (`riyadh_naive_to_utc`/`to_riyadh_naive`) بمنطقة زمنية حقيقية، مع تعليق موثَّق صراحة يحذّر من فخ التخزين المباشر لساعات مُزاحة كـUTC. مُطبَّقة في `sender.py:362-366` و`scheduler_main.py:64-66`. |
| d | إحماء 6/12/16 ثم 22 كحد أقصى؛ هدف يومي 17 (حتى 22) | **✅ DONE** | `pacing.py:47-59` (`_RAMP_TIERS`, `DEFAULT_TARGET_DAILY=17`, `MAX_DAILY=22`) + `send_builder.py:243-249` (`target = min(target_daily, ramp, MAX_DAILY, wallet_balance)`). |
| e | تبريد شركة 60 يومًا/عميل؛ ≤3 عملاء/شركة/أسبوع | **✅ DONE** | `send_builder.py:44-46,152-184,296-303` (`COOLDOWN_DAYS=60`, `WEEKLY_CAP_CUSTOMERS=3`, نافذة متحركة 7 أيام). |
| f | الارتدادات لا تستهلك رصيدًا؛ مسار استرداد معاملي | **✅ DONE (بند الوارد) / ⚠️ جزئي (فشل SMTP نهائي)** | ارتداد IMAP: `inbox.py:223-256` — الاسترداد + تحديث `applications.status='bounced'` داخل نفس `engine.begin()` (معاملة واحدة). فشل SMTP نهائي (`MAX_ATTEMPTS`): `sender.py:251-284` يسترد أيضًا (نمط `adjustment` وليس `bounce_refund` تحديدًا، مقبول لأنه ليس ارتدادًا فعليًا). لكن راجع **F1** أدناه — نافذة ازدواج إرسال قد تُبطل هذا الضمان فعليًا. |
| g | `DRY_RUN` افتراضي؛ لا اتصال SMTP حقيقي؛ sink = mailpit داخل compose | **❌ FAIL (جزء البنية التحتية)** | المنطق البرمجي **fail-closed** فعلاً: `sender.py:99-117` (`resolve_recipient`) يرفع `ValueError` ولا يرسل شيئًا إن غابت كل من `MAIL_SINK_SMTP`/`DRY_RUN_TO`/`MAIL_LIVE=true` — هذا الجزء **DONE فعليًا وآمن افتراضيًا**. **لكن**: `docker-compose.yml` **لا يعرّف خدمة `mailpit` إطلاقًا** (تحقّق مباشر: `grep -n mailpit docker-compose.yml` بلا نتائج) ولا يمرّر `MAIL_SINK_SMTP`/`DRY_RUN_TO`/`MAIL_LIVE` لأي حاوية — فالسند التطويري (dry-run sink) الموصوف بتوثيق `sender.py:13-23` وواجهة `mail_api.py:213-233` (`/admin/mail/sink/messages`) **غير قابل للتشغيل أصلاً** بالبنية الحالية، ولا حتى للاختبار اليدوي. |
| h | لا كشف أتمتة/سقوف للعميل النهائي؛ لا بيانات حسّاسة متبقية بالجسم/السيرة | **✅ DONE** | لا ذكر لأرقام/سقوف/أتمتة بـ`data/phrases_ar.yaml`/`phrases_en.yaml` (تحقّق `grep`). `cv_builder.py:68-100` (`sanitize_cv_excerpt`) يقصّ قسم المراجع ويُبقي أول بريد/هاتف فقط. "References available upon request"/"المراجع متاحة عند الطلب" موجودة بكل القوالب الأربعة (`templates/cv/1-4.html`). |
| i | `SKIP LOCKED`، إرسال idempotent، `CHECK balance >= 0` | **✅ جزئي / ⚠️ فجوة حقيقية** | `SELECT...FOR UPDATE SKIP LOCKED`: `sender.py:168-195` (CTE صحيح). قيد `CHECK (balance >= 0)`: مُتحقَّق ببنية `0004_b3_customers` (مؤكَّد بتقرير B3، لم يُعدَّل هنا). **الـidempotency ضد الازدواج ناقصة** — راجع **F1** الحرج أدناه: انهيار العملية بين نجاح SMTP فعليًا وكتابة `_mark_success` بقاعدة البيانات يترك الصفّ بحالة `sending` حتى ينتهي `locked_until` (5 دقائق) ثم يُعاد التقاطه وإرساله **مجددًا فعليًا** لنفس الشركة. |
| j | قارئ IMAP: تصنيف ارتداد/رد/مقابلة؛ لا حذف/نقل لبريد العميل | **✅ DONE** | `inbox.py:144-146` (`imap.select("INBOX", readonly=True)` — لا `STORE`/`EXPUNGE`/`MOVE` بأي مكان بالملف، تحقّق `grep` كامل). التصنيف: `inbox.py:56-83` (أولوية صارمة bounce>interview>reply>other، موثّقة ومبررة). Idempotent عبر `ON CONFLICT (customer_id, message_id) DO NOTHING` (`inbox.py:198-220`، ومطابق لقيد `uq_inbox_events_customer_message` بالترحيل). |

---

## الاستنتاجات (مرتّبة حسب الخطورة)

### 🔴 حرج 1 — `MAIL_FERNET_KEY` لا يصل لحاوية `core`/`core-scheduler` إطلاقًا
**الملف:** `docker-compose.yml:38-98` مقابل `deploy/autodeploy.sh:59-63`.
`autodeploy.sh` يولّد المفتاح في `.env` على المضيف (خارج أي حاوية)، لكن
لا `core:` ولا `core-scheduler:` بـ`docker-compose.yml` تحمل سطر
`MAIL_FERNET_KEY: ${MAIL_FERNET_KEY}` بقسم `environment:` (ولا يوجد
`env_file:` بديل بأي خدمة). Docker Compose لا يمرّر متغيرات `.env` تلقائيًا
لبيئة الحاوية — فقط يستخدمها للاستبدال (`${VAR}`) **داخل** ملف compose نفسه
حين يُذكَر صراحة. النتيجة: أي استدعاء لـ`mail_crypto.encrypt_secret`/
`decrypt_secret` بالإنتاج يرفع `MailCryptoUnavailable` دومًا — أي `/mail-link`
(إنشاء ربط بريد) **يفشل دومًا بالكامل**، وأي محاولة إرسال حقيقي بالمنطق
الحقيقي `resolve_transport` (`sender.py:76-96`) تفشل بصمت (تُعامَل كـ"لا
مصدر إرسال" وتُعاد للطابور/تفشل نهائيًا). **الإصلاح:** أضف
`MAIL_FERNET_KEY: ${MAIL_FERNET_KEY:-}` لقسم `environment:` بكلتا الخدمتين.

### 🔴 حرج 2 — لا خدمة `mailpit` بـ`docker-compose.yml` إطلاقًا
تحقّق مباشر: لا نتيجة لـ`grep -n mailpit docker-compose.yml`. البند 3.g
بالتكليف صريح: "confirm the compose file actually defines the mailpit
service". لا يوجد. سند التطوير الآمن (`MAIL_SINK_SMTP`) الموصوف بالتفصيل
في توثيق `sender.py` ومُستخدَم بواجهة `/admin/mail/sink/messages`
(`mail_api.py:213-233`) **غير قابل للتفعيل أصلاً** بالبنية الحالية — لا طريقة
لاختبار مسار الإرسال الفعلي (SMTP/MIME/مرفقات) محليًا بأمان بدون هذا. **الإصلاح:**
أضف خدمة `mailpit` (صورة `axllent/mailpit`) على `masar_internal`، ومرّر
`MAIL_SINK_SMTP=mailpit:1025` و`MAIL_SINK_API_URL=http://mailpit:8025` لكلتا
حاويتي core/core-scheduler.

### 🟠 عالٍ 1 (F1) — نافذة ازدواج إرسال حقيقية عند انهيار/إعادة تشغيل العملية
**الملف:** `sender.py:339-349` (`_process_row`) مقابل `sender.py:168-195`
(`_claim_due_batch`). التسلسل: `_smtp_send` ينجح فعليًا (الرسالة خرجت
للشبكة) → قبل تنفيذ `_mark_success` (سطر 347-348) تُقتَل العملية (SIGTERM
من `docker compose up -d --build` بأي نشر جديد — يحدث كل ~2 دقيقة حسب
بروتوكول autodeploy، أو تعطّل عادي). الصفّ يبقى `status='sending'` بـ
`locked_until` (+5 دقائق). بعد انتهاء القفل، تكة `sender_tick` التالية
(كل دقيقة) تُعيد التقاطه عبر نفس شرط `locked_until < now` وتُرسله **فعليًا
مرة ثانية** لنفس الشركة — يخالف صراحة بند 3.i "idempotent sends (no
double-send on retry)". **الإصلاح المقترح:** قبل إعادة إرسال صفّ استُعيد
بعد انتهاء قفل سابق (`attempts > 0` أو دليل استرداد آخر)، تحقّق أولًا من
عدم وجود صفّ `applications` مطابق (`customer_id`+`job_id`+`opportunity_id`،
`status='sent'`) قبل الاتصال بـSMTP مجددًا؛ إن وُجد، علّم `send_queue`
كـ`sent` مباشرة بلا إرسال فعلي جديد.

### 🟠 عالٍ 2 — `opportunities.status='sent'` يُضبط عند البناء لا عند الإرسال الفعلي
**الملف:** `send_builder.py:404-407`. الحالة تُحدَّث لـ`'sent'` فور إدراج
الصفّ بـ`send_queue` (حالته `'queued'` وقتها)، لا بعد نجاح الإرسال الفعلي.
لو فشل الإرسال نهائيًا لاحقًا (`sender.py:253-284`، `MAX_ATTEMPTS`)، يُسترد
الرصيد بشكل صحيح لكن **`opportunities.status` يبقى `'sent'` للأبد** رغم أن
التقديم لم يصل فعليًا — يُفسِد أي تقرير/إحصاء يعتمد على `opportunities.status`
كمصدر حقيقة عن "ما أُرسل فعلًا" (الفرق الوحيد الموثوق يكون بمطابقة يدوية مع
`applications`/`send_queue.status`). **الإصلاح المقترح:** لا تُحدَّث
`opportunities.status` هنا إطلاقًا (اترك المصدر الحقيقي الوحيد
`send_queue.status`/`applications`)، أو حدّثها إلى `'sent'` فقط داخل
`sender._mark_success` بعد التأكد الفعلي من النجاح، مع تحديثها لـ`'skipped'`
عند فشل `send_queue` نهائيًا.

### 🟡 متوسط 1 — صفر اختبارات وحدة لمنطق B4 رغم توثيق داخلي يدّعي وجودها
`pacing.py:7` يذكر صراحة `core/tests/test_pacing.py`، و`inbox.py:15` يذكر
`core/tests/test_inbox_classifier.py` — **كلا الملفين غير موجودين**. لا يوجد
أي اختبار لـ: تحويل التوقيت ذهابًا وإيابًا (`riyadh_naive_to_utc`/
`to_riyadh_naive`)، حدود وتيرة الإحماء (`ramp_cap` عند الحدود بالضبط:
age_days=1/2/4/5/8/9)، تصنيف الوارد (`classify_kind`/`is_bounce`/
`is_interview_mention`)، حتمية `composer.build_email` (نفس المدخلات
تُنتج نفس الرسالة)، أو `apply_email.classify_email`/`select_apply_email`
(بما فيها حالة الحد الفاصل `supportive@x.com` المذكورة بالتوثيق نفسه —
غير مُتحقَّقة فعليًا بأي اختبار). بند 6 من بروتوكول `PLAN.md` صريح: "لا
يُعلَّم DONE إلا بدليل". **الإصلاح:** أضف الملفات الأربعة الموصوفة
بالتوثيق الداخلي نفسه قبل اعتماد B4.

### 🟡 متوسط 2 — لا تخزين دائم/مشترك لملفات السيرة الذاتية (CV) بين الحاويتين
`cv_builder.py:178` (`CV_DATA_DIR` افتراضي `/data/cv`) — لا `volume` ولا
متغيّر بيئة `CV_DATA_DIR` بأي من خدمتي `core`/`core-scheduler` بـ
`docker-compose.yml`. المسار الافتراضي مجرد طبقة overlay مؤقتة داخل كل
حاوية، **غير مشتركة** بين `core` (تبني عبر `/admin/mail/queue-now` اليدوي)
و`core-scheduler` (يبني ويرسل تلقائيًا كل 10د/1د على التوالي بنفس العملية —
هذا المسار الآلي **متّسق داخليًا** لأنه نفس الحاوية، لكن **يُفقَد كليًا عند
أي إعادة نشر** لأنه غير مثبَّت كـvolume). النتيجة العملية: إعادة نشر
(تحدث كثيرًا أثناء تطوير B4 النشط) بين بناء الطابور والإرسال الفعلي (قد
تفصل بينهما ساعات ضمن نافذة الإرسال) تمحو ملف PDF، و`ensure_cv_variant`
(`cv_builder.py:170-171`) يعيد بناءه تلقائيًا **فقط إن استُدعيت مجددًا** —
لكن `sender.py:build_mime_message:142-152` لو وصل الصفّ للإرسال وملف
المرفق مفقود فعلًا (نافذة إعادة بناء بين حاويتين مختلفتين، أو استدعاء
`/admin/mail/queue-now` يدويًا من حاوية `core` ثم إرسال تلقائي من
`core-scheduler`) **يتخطّى المرفق بصمت** (`logger.warning` فقط) ويُرسل
البريد **بلا سيرة ذاتية مرفقة** — يخالف وعد المنتج الأساسي. **الإصلاح:**
أضف volume مشترك (`cv_data:/data/cv`) لكلتا الخدمتين + `CV_DATA_DIR=/data/cv`
صراحة بـ`environment:`.

### 🟡 متوسط 3 — الحارس `except ImportError` بـ`main.py` لا يحمي من أخطاء أخرى، وقد حدث فعلًا
**الملف:** `main.py:51-57`. التعليق يعد بأن غياب/عطب ملفات B4 "لا يعطّل
main.py"، لكن `except ImportError` فقط لا يلتقط `SyntaxError` أو أي استثناء
آخر يحدث أثناء تحميل `app.mail_api`/`app.inbox_api` أو أي وحدة تستوردها
هذه الملفات (`send_builder`, `sender`, `composer`, `cv_builder`, `pacing`,
`apply_email`, `mail_crypto`). **هذا حدث فعليًا** — `git log` يُظهر commit
`e9729d5` بعنوان "B4 hotfix: comment out stray line in mail_api.py
(SyntaxError crashed core)"، أي أن **كل** الخدمة (`/health` وكل نقاط
B1/B2/B3 أيضًا) توقفت بسبب خطأ بملف B4 وحده، رغم أن نية التصميم الموثَّقة
كانت عزل B4 تمامًا. **الإصلاح:** وسّع الالتقاط إلى `except Exception` مع
تسجيل الاستثناء كاملًا (`logger.exception`)، أو أفضل: أضف اختبار CI بسيط
(`python -m compileall`/`import app.mail_api`) يفشل البناء قبل النشر بدل
الاعتماد على حارس وقت التشغيل فقط.

### 🟢 منخفض 1 — استدعاءات إدارية متزامنة (blocking) داخل مسارات `async def`
`mail_api.py:198-210` (`queue_now`/`send_now`) دوال `async def` تستدعي
مباشرة `send_builder.build_queue_round`/`sender.send_tick` — دوال متزامنة
بالكامل (SQLAlchemy sync + `smtplib`) بلا `run_in_threadpool`/
`asyncio.to_thread`. مع `uvicorn` بعامل واحد افتراضيًا (`core/Dockerfile`
بلا `--workers`)، استدعاء `/admin/mail/send-now?limit=200` يُجمّد حلقة
الحدث بالكامل (كل نقاط `core` الأخرى، بما فيها `/health`) حتى تكتمل الدفعة.
نمط موجود مسبقًا ببقية الكود (`customers_api.py:/plan/run-now` بحسب تقرير
B3) فليس عيبًا مستحدَثًا خاصًا بـB4 وحده، لكن يستحق معالجة موحّدة لاحقًا.

### 🟢 منخفض 2 — لا فهرس على `applications.message_id`
`inbox.py:224-232` يستعلم `WHERE customer_id = :cid AND message_id = :mid`
لكل رسالة ارتداد. الفهرس الوحيد المتاح `ix_applications_customer_id`
(العمود الأول فقط) — مقبول عمليًا الآن (عدد طلبات العميل الواحد يوميًا صغير
جدًا، 17-22) لكن يستحق `ix_applications_customer_message_id` وقائيًا مع نمو
حجم البيانات.

### 🟢 منخفض 3 — استعلام مرشّحي الإرسال بلا `LIMIT`
`send_builder.py:90-107` (`_fetch_candidate_opportunities`) يجلب **كل**
فرص العميل `planned_for <= today` بلا حد أعلى (حلقة `for cand in candidates`
تتوقف مبكرًا بمجرد `queued >= remaining`، لكن الاستعلام نفسه يُحمِّل الكل
بالذاكرة أولًا). لعميل مُعلَّق طويلًا ثم يُعاد تفعيله بتراكم كبير من فرص
`planned` غير مُرسَلة، هذا قد يجلب آلاف الصفوف لعميل واحد. اقتراح: أضف
`LIMIT 300` تقريبًا (الترتيب `score DESC` أصلًا يضمن مرور أفضل المرشّحين أولًا
فلا يتأثر السلوك عمليًا).

---

## فحوصات لا تزال تحتاج خادمًا حيًّا (Live checks still required)

هذه المراجعة أوفلاين بالكامل — التالي **لا يمكن إثباته إلا على `masar-core-1`
الحي بعد تطبيق إصلاحي الحرج 1/2 أعلاه**:

1. أن `mail-link` فعليًا يختبر SMTP/IMAP حقيقيين (Gmail App Password حقيقي)
   وينجح/يفشل بشكل متوقَّع.
2. معدّل تصريف فعلي تحت حمل (اختبار `/admin/mail/load-test` + مراقبة
   `send_tick` كل دقيقة) للتحقق من هدف 25,500 رسالة/يوم (1500×17) خلال
   نافذة 8.5 ساعة فعليًا لا حسابيًا فقط.
3. أن `mailpit` (بعد إضافته) يستقبل فعليًا رسائل بمرفق PDF صالح وCC صحيح —
   فحص بصري حقيقي لموضوع/جسم/مرفق رسالة كاملة من طرف لطرف.
4. سلوك إعادة النشر (autodeploy) أثناء دورة إرسال فعلية قيد التنفيذ — هل
   تتكرر مشكلة F1 (الازدواج) عمليًا أم أن نافذة الخطر أضيق مما تبدو نظريًا؟
   يحتاج رصدًا حيًّا على عدة دورات نشر متتالية مع حركة إرسال فعلية.
5. أداء IMAP فعليًا على 1,500 صندوق (المعيار: ساعة واحدة لكل 1,500 صندوق
   بـ20 عاملًا) — لا يمكن قياسه بلا صناديق Gmail حقيقية.
6. أن Gmail فعليًا لا يُصنّف رسائل Masar كسپام عند حجم الإرسال المستهدف
   (خارج نطاق أي مراجعة كود بالكامل).
7. التحقق الحي من أن `docker compose up -d --build` بعد إضافة `MAIL_FERNET_KEY`
   لقسم `environment:` يُعيد فعليًا فك تشفير أسرار مخزَّنة سابقًا (لا تعارض
   إصدار متغيّر).
