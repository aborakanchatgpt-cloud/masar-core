# B5c — تقرير المنفّذ (فجوات ما بعد مراجعة B5a/B2b)

نُفّذ في sandbox معزول (`/home/claude/masar-core-b5c`)، بلا SSH، عبر جسر MCP
`Masar_Core` (`repo_write`/`job`/`core_call`/`ops`). العمل يعالج فقط الملفات
المملوكة لـB5c (`guarantee.py`, `reports.py`, `reports_api.py`,
`customers_api.py`, `feedback_api.py`, `link_api.py`, `main.py`, migration
`0010_b5c_gaps.py`) + `docs/reports/B5a-B2b-review.md` (البند 1 كان لهذه
الجلسة) + `docs/reports/B5b-executor.md`/`n8n/README.md` (فجوات NEEDS-CORE).
لم تُلمَس ملفات B6 المتوازية (`sender.py`, `pacing.py`, `inbox.py`,
`planner.py`, `send_builder.py`, `scheduler_main.py`, إعدادات pool/db، migration
`0011`, `scripts/load_test*`). لمسة صغيرة إضافية غير مذكورة صراحة بالنطاق —
`core/app/mail_api.py` (سطران): مبرَّرة أدناه (البند 3).

## 1) `guarantee.py` — فصل عدّاد التمديد (عيب [major] بالمراجعة، القسم 3.2)

المشكلة كما وثّقتها المراجعة: عمود `extension_days` واحد مشترك بين تمديد
انقطاع البريد (`OUTAGE_EXTENSION_DAYS=1`، يتكرر يوميًا) وتمديد مهلة الأداء
الإلزامية (`GRACE_EXTENSION_DAYS=2`، مرة واحدة). إن تراكم انقطاع بريد ≥2 يوم
وحده، ثم أُصلح البريد والعميل لا يزال قاصرًا، كان الفحص
`already_had_grace = extension_days >= 2` يعتبر المهلة مستهلَكة زورًا ويقفز
مباشرة لـ`refund_pending` بلا أي مهلة أداء فعلية.

**الإصلاح:** عمودان منفصلان بـ`guarantee_ledger` (migration 0010):
`grace_extension_days` و`outage_extension_days`. `extension_days` يبقى
موجودًا **كمجموع الاثنين** (توافقًا خلفيًا لأي قارئ حالي — `reports.py`/
`overview_api.py` لا يقرآنه فعليًا اليوم لكن العمود لم يُحذف). فحص
"استُهلِكت المهلة؟" يقرأ `grace_extension_days` حصرًا الآن. مسار الانقطاع
يزيد `outage_extension_days` فقط ولا يمسّ `grace_extension_days` إطلاقًا.

**اختبار جديد** (`test_mail_outage_extension_does_not_consume_mandatory_grace_period`،
`core/tests/test_guarantee.py`) يغطي بالضبط سيناريو المراجعة: يومان انقطاع
بريد متتاليان → إصلاح البريد → لا يزال قاصرًا → **يجب** أن يحصل على
`extended`/`grace_period_granted` أولًا (لا `refund_pending`) → تقييم رابع
بعد استهلاك المهلة الفعلية أيضًا → `refund_pending` أخيرًا. الاختبارات
السبعة القديمة عُدِّلت بصفر تغيير بالسلوك المُتوقَّع (تحقّقتُ يدويًا أن كل
مسار قديم يُنتج نفس `extension_days`/`status` كما قبل — فقط طريقة الحساب
الداخلية تغيّرت).

**ملاحظة ترحيل موثَّقة (أعلى الملف وأعلى migration 0010):** صفوف
`guarantee_ledger` القديمة (قبل هذا الترحيل، لا توجد بعد بالإنتاج فعليًا
حسب `docs/reports/B5a-B2b-review.md` — لا عملاء حقيقيين بعد) تبدأ
بـ`grace_extension_days=0` افتراضيًا؛ الاتجاه الآمن الوحيد الممكن بلا سجلّ
تاريخي يميّز مصدر كل يوم تمديد سابق.

## 2) `reports.py` — `send_queue_id` بـ`today_applications` (NEEDS-CORE #1)

`_fetch_today_applications` يضيف الآن `send_queue_id`، `job_id`، `company_id`
لكل عنصر عبر `LEFT JOIN send_queue sq ON sq.opportunity_id =
applications.opportunity_id AND sq.status = 'sent'` — **بلا** لمس
`sender.py` (مملوك لـB6) ولا إضافة عمود جديد على `applications`:
`send_builder.py` يمنع أصلًا أكثر من صفّ `send_queue` غير ملغى لكل
`opportunity_id`، فالمطابقة حتمية. `send_queue_id=null` حين لا
`opportunity_id` على التطبيق (نادر، تطبيقات ما قبل B3). قائمة `excluded`
تحمل `job_id`/`company_id` (متاحان دومًا من `opportunities`/`jobs`) و
`send_queue_id: null` دومًا (فرصة مُستبعَدة لم تُبنَ لها صفّ `send_queue`
قط — لا معنى لأزرار عليها). النص (`text`) بلا أي تغيير كما طُلب.

وركفلو `n8n/workflows/masar_daily_report_relay.json` (عقدة "بناء الرسائل")
مبني مسبقًا ليقرأ `today_applications[].send_queue_id` تلقائيًا (فلتر
`withIds = apps.filter(a => a.send_queue_id != null)`) ويبني أزرار
👎/🎉 بصيغة `fb:<customer_id>:<send_queue_id>:<kind>` — **لا حاجة لأي تعديل
بملفات n8n**، الأزرار تظهر تلقائيًا الآن أن الحقل موجود.

**اختبار جديد:** `test_today_applications_carry_send_queue_id_when_available`
(`core/tests/test_reports.py`) — تطبيق مربوط بـ`send_queue` حقيقي يحمل
`send_queue_id` صحيحًا + `job_id`/`company_id`، وتطبيق آخر بلا `opportunity_id`
يحمل `send_queue_id=None` بلا استبعاد من القائمة (LEFT JOIN حقيقي).

## 3) `customers_api.py` — ثلاث نقاط جديدة (NEEDS-CORE #2/#3/#4)

- **`GET /customers/by-telegram/{chat_id}`** → `{customer_id, status}` أو
  404. مسار بثلاثة أجزاء لا يتصادم مع `/customers/{customer_id}` (جزء واحد)
  بصرف النظر عن ترتيب التسجيل.
- **`POST /customers/{id}/status {status, note?}`** — انتقالات مسموحة فقط
  ضمن `{active,paused}` (لا يُعاد تفعيل عميل `expired` من هنا — 409)؛ سجلّ
  تدقيق `customer_status_audit` (جدول جديد، migration 0010) بكل تغيير.
- **`POST /customers/{id}/cv`** (multipart) — سيرة PDF خام: فحص magic bytes
  (`%PDF-`)، حدّ 5MB، تُخزَّن `CV_DATA_DIR/<customer_id>/uploaded_cv.pdf`
  (نفس اتفاقية `cv_builder.py`، اسم ثابت مختلف عن `{family}.pdf` المولَّدة
  تلقائيًا — لا تصادم)، المسار + `sha256` يُسجَّلان على `customers` مباشرة
  (أعمدة جديدة `cv_pdf_path`/`cv_pdf_sha256`/`cv_pdf_uploaded_at` — لا
  `profiles` لأن onboarding قد لا يملك صفّ `profiles` بعد وقت الرفع).

**`customers.email_service` أصبح NULLable** (migration 0010): قيد UNIQUE
العادي تحوّل لفهرس فريد جزئي `WHERE email_service IS NOT NULL`. نفس الشيء
لـ`customers.telegram_chat_id` (كان فهرسًا عاديًا فقط منذ 0004 — الآن فريد
جزئي، يمنع ربط رقم محادثة تيليجرام بعميلين ويضمن صحّة `by-telegram`).
`CustomerCreateRequest.email_service` أصبح اختياريًا. `app.mail_api.create_mail_link`
(**لمسة خارج ملفاتي المُصرَّح بها، سطران فقط**، مبرَّرة صراحة بالتكليف: "ensure
create_mail_link fills it") يملأ `customers.email_service` بعنوان البريد
الموثَّق فعليًا عند نجاح الربط، عبر `COALESCE(email_service, :addr)` — لا
يكتب فوق قيمة موجودة أصلًا (عملاء أُنشئوا بالمسار القديم يحدّدون
`email_service` وقت الإنشاء يبقون كما هم). `app.mail_api.load_test`
(دالة B6 لاختبار الحمل — لم تُمسّ منطقيًا، فقط جملة `ON CONFLICT` واحدة):
Postgres يتطلّب تكرار شرط الفهرس الجزئي بجملة `ON CONFLICT` نفسها
(`ON CONFLICT (email_service) WHERE email_service IS NOT NULL`) وإلا فشل
استدلال الفهرس — تحقّقتُ يدويًا (استعلام SQL مباشر) أن الصياغة الجديدة تعمل
كـupsert حقيقي (نفس id عند تكرار البريد).

**11 اختبارًا جديدًا** بملف جديد `core/tests/test_customers_api.py`
(by-telegram ×2، status ×4، cv-upload ×4، create-customer-without-email ×1).

## 4) `feedback_api.py` مقابل `n8n/workflows/masar_feedback_callback.json`

**لا تعديل احتاجه هذا الملف.** فحصتُ عقدة "تسجيل التغذية الراجعة"
(httpRequest) بالوركفلو: `POST /customers/{customer_id}/feedback` بجسم
`{send_queue_id, kind}` — يطابق `feedback_api.FeedbackRequest` حرفيًا (الحقل
الثالث `note` اختياري أصلًا). شكل الاستجابة `{ok, already_recorded,
feedback_id?, company_excluded_id?}` لا يُقرأ إلا كـ`apiResult.ok === true`
بعقدة "بناء رسالة التأكيد" — متوافق تمامًا. لا تغيير مطلوب في الملف، ولا
تغيير مطلوب بملف الوركفلو.

## 5) `PLAN.md` — حاشية فهرسة تقارير B2 + تصحيحات دقّة داخل قسم B5

أضفتُ الحاشية المطلوبة (توضّح أن `B2-executor.md`/`B2-executor-round2.md`/
`B2-review.md` تقارير B2 مبكرة استبدلها `B2-review-2.md`). الملف كان بالفعل
عند **25,107 حرفًا** (فوق حدّ 25,000 المطلوب) قبل أي تعديل مني — لذا، ضمن
نطاقي الوحيد المصرَّح به (قسم B5 فقط)، اختصرتُ بعض العبارات المكرَّرة (لا
حذف معلومة) وصحّحتُ سطرين أصبحا فعليًا خاطئين بعد هذا التنفيذ (وصف
NEEDS-CORE الأهم كأنه لا يزال فجوة، و"باقي B5 لم يُبدأ" بعد سطر B5a رغم
اكتمال B5b/B5c لاحقًا) بدل تركهما نصًّا مضلِّلًا. النتيجة: **24,998 حرفًا**
(تحت الحدّ)، وبقية الملف (كل شيء خارج قسم B5) **مطابق حرفيًا بايت-ببايت**
لما كان عليه (تحقّق آلي بمقارنة السلاسل قبل الدفع).

## بوابات الجودة (محليًا قبل أي دفع)

- `pytest -q` (Postgres 16 محلي، `masar_test`): **259 → 272** (13 اختبارًا
  جديدًا: 1 guarantee + 1 reports + 11 customers_api)، 0 فشل، 0 تخطّي.
- `python -m compileall -q core migrations`: نظيف.
- `python -c "import app.main, app.scheduler_main"`: نظيف.
- `alembic upgrade head` / `downgrade -1` / `upgrade head` (round-trip كامل
  على قاعدة تحمل بيانات اختبار حقيقية): نجح بلا أخطاء.
- تحقّق SQL يدوي مباشر لصياغة `ON CONFLICT (email_service) WHERE
  email_service IS NOT NULL` (خارج pytest، عبر psql مباشرة) — upsert صحيح.

## لم يُنجَز / خارج النطاق

- لا نقطة نهاية لتنزيل ملف السيرة من تيليجرام نفسه (بوت الأدمن ينزّله
  ويرفعه لـ`POST /customers/{id}/cv` — خارج نطاق Core، مذكور بالفعل في
  `n8n/README.md` NEEDS-CORE #3 كخطوة لاحقة).
- `POST /admin/guarantee/{id}/settle` لا يزال يقرأ/يكتب `extension_days`
  فقط عند التسوية (لم يُلمَس — خارج نطاق البند 1، لا حاجة له لعمود جديد).
- لا إثبات حي كامل لمسار 👎 حقيقي عبر بوت تيليجرام فعلي (لا وصول شبكي من
  بيئة البناء لتيليجرام — نفس قيد B5b الموثَّق).
