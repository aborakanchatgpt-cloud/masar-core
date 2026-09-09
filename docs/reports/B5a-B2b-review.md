# B5a + B2b — مراجعة مستقلة لـ main المدمج (2026-09-09)

**المراجع:** جلسة مستقلة (Sonnet subagent، بلا SSH، عبر `git clone` كامل + Masar MCP bridge). **النطاق:** الكود المدمج فعليًا على `main` بعد تشابك commits B5a وB2b (لا فرعين منفصلين — تشابكا حرفيًا حسب `git log`).

## الحكم الإجمالي

| البند | الحكم |
|---|---|
| `main` المدمج (اختبارات/بناء) | **ACCEPT** |
| سلامة `PLAN.md` | **ACCEPT** (ملاحظة تكميلية بسيطة أدناه) |
| B5a (تقارير/ضمان/استبعاد/لوحة/ربط بريد) | **ACCEPT-WITH-FIXES** |
| B2b (تصنيف/مصادر) | **ACCEPT** |

---

## 1. نتائج الاختبار الكامل (main المدمج، Postgres 16 محلي حقيقي)

- `pytest -q` (من `core/`، `DATABASE_URL`+`TEST_DATABASE_URL` مضبوطان على `masar_test`، `alembic upgrade head` مطبّق مسبقًا): **259 نجح / 0 فشل / 0 تخطّي** (كل اختبارات DB-backed عملت فعليًا، لم تُتخطَّ). هذا يطابق حسابيًا 214 (قبل B4 F1/F2/F3 المدموج مسبقًا) + 28 (B5a) + 17 (B2b: 14+3) = 259 — لا تعارض ولا فقدان اختبارات بين الفرعين.
- `python -m compileall -q core` (من جذر المستودع): نظيف، exit 0.
- `python -c "import app.main, app.scheduler_main"`: نظيف، بلا استثناء.
- `alembic upgrade head` على قاعدة نظيفة: نجح، توقّف عند `0008_b5_reports` (B2b لم يضف ترحيلًا — صحيح، موثَّق).

**لا تعارض دمج بين B5a وB2b**: ملفات B5a (`reports.py`, `guarantee.py`, `*_api.py` الجديدة, `send_builder.py` hook, `scheduler_main.py`, migration `0008`) وملفات B2b (`discovery.py`, `matching.py`, `discovery_api.py`, `reclassify.py`, `taxonomy_local.yaml`, `sources_seed.csv`) لا تتقاطعان في أي ملف مُعدَّل، ولا تضاربا بأي ترحيل (B2b احتاج صفرًا). التشابك في `git log` كان زمنيًا فقط (commits متبادلة)، لا تعديلًا متزامنًا لنفس السطور.

## 2. سلامة PLAN.md

- الحجم الحالي: 23,101 حرفًا (تحت حد 25,000). لا علامات قطع أو تكرار قسم.
- القسم 0 (البروتوكول) كامل وسليم (7 قواعد ملزمة، جدول الأدوار).
- كل البنود B0–B7 موجودة بحالات صحيحة: B0=DONE، B1=WIP، **B2=WIP/REJECT** (محفوظ كما هو، لم يُغيَّر خطأً رغم B2b)، B3=WIP، B4=WIP، **B2b=WIP بأدلة B2b الرقمية كاملة** (85.30%/75.13% مع الاستعلام الحرفي)، **B5=WIP مع سطر فرعي B5a واضح** يشير لـ`docs/reports/B5a-executor.md`، B6=TODO، B7=TODO.
- القسم 3 (سجل التشغيلات) يحوي سطرين حديثين منفصلين لـB5a وB2b، وكلاهما يوثّق صراحةً أنه **لم يلمس ملفات الآخر** (تقاطع صفري موثَّق ذاتيًا من الطرفين، تحقّق مطابق لما وجدناه بالكود فعليًا في القسم 1 أعلاه).
- **ملاحظة تكميلية (لا عيب حرج):** `repo_list docs/reports` يُظهر 11 ملفًا؛ 8 مُشار إليها صراحة بـ`PLAN.md`. الثلاثة الباقية — `B2-executor.md`، `B2-executor-round2.md`، `B2-review.md` (المراجعة الأولى لـB2، حلّت محلها `B2-review-2.md`) — تقارير تاريخية مُستبدَلة، فقدانها من الإشارات لا يُفقد معلومة جوهرية، لكن لا حاشية تذكرها كـ"تاريخي" صراحة. توصية غير عاجلة: سطر واحد يوضّح ذلك حتى لا يظنها قارئ لاحق مفقودة سهوًا.

## 3. مراجعة B5a التفصيلية (بالكود + فحص حي)

### 3.1 القواعد المنتجية — التحقق
- **لا ذكر أتمتة/AI/بوت، لا صياغة مالية/معاملات:** فحص نصي شامل على `reports.py`/`link_api.py`/`guarantee.py`/كل `*_api.py` — صفر تطابقات لأي من هذه المصطلحات في أي نص عميل. النص الحي الفعلي لتقرير العميل 1 (`GET /admin/reports/pending`) تحقّق مباشرة: نبرة إنسانية دافئة، بلا أي رقم/كسر/سقف.
- **لا سقوف/تقديرات ظاهرة للعميل:** `_progress_phrase` في `reports.py:179-189` يعرض فقط عددًا مطلقًا بلا مقام ("قدّمنا لك حتى الآن N فرصة") — لا صياغة `17/22` ولا "الحد الأقصى"/"حد يومي" بأي مكان بالنص المُولَّد. مطابق للتكليف حرفيًا.
- **المرتد لا يُحسب أبدًا:** `_count_counted_applications` (`reports.py:130-139`) و`_count_period_applications` (`guarantee.py:76-97`) كلاهما `status != 'bounced'` صراحة، وعدّاد `bounced` منفصل ويُخزَّن لكن لا يُحسب بالتقدّم/الهدف. اختبار `test_bounced_applications_excluded_from_counted_sent` يغطي هذا.
- **الهدف 510:** `MONTHLY_TARGET = 510` ثابت في كلا `reports.py` و`guarantee.py`.
- **تمديد يومين ثم تعويض تناسبي بانتظار المالك:** `guarantee.evaluate_period` (`guarantee.py:200-245`) يطبّق `GRACE_EXTENSION_DAYS=2` مرة واحدة، ثم عجز متبقٍ → `refund_amount = shortfall × price_sar ÷ 510`، حالة `refund_pending` (لا تحويل تلقائي — يتطلب `POST /admin/guarantee/{id}/settle` صريحًا من المالك). مطابق.
- **رصيد → na:** `_fetch_due_subscription` يرجع `None` لعميل بلا اشتراك مستحق → `{"status": "na"}` (`guarantee.py:143-144`). مطابق.
- **انقطاع بسبب العميل (بريد معطوب) → تمديد لا تعويض:** `mail_broken` (`guarantee.py:173-198`) يمدّد الفترة يومًا ويمنع الوصول لمرحلة التعويض بالكامل طالما `mail_links.status != 'ok'`. مطابق، ومُختبر (`test_customer_caused_mail_outage_extends_instead_of_refund`) ومُوثَّق كـNEEDS-OWNER (لا جدول تاريخي لحالة البريد — تبسيط مقبول موثَّق صراحة بالكود).

### 3.2 عيب حقيقي وجدته المراجعة (لم يذكره تقرير المنفّذ)

**[major] `guarantee.py:evaluate_period` — تداخل عدّاد التمديد بين "انقطاع بريد العميل" و"مهلة الأداء العادية" قد يُسقط مهلة الأداء الإلزامية (2 يوم) بالكامل.**

- الكود يستخدم عمود `extension_days` **واحدًا مشتركًا** لكلا نوعي التمديد: تمديد الانقطاع (`OUTAGE_EXTENSION_DAYS=1` لكل تقييم يومي طالما البريد معطوب) وتمديد المهلة العادية لمرة واحدة (`GRACE_EXTENSION_DAYS=2`).
- الفحص `already_had_grace = existing.status == "extended" and prior_extension_days >= GRACE_EXTENSION_DAYS` (`guarantee.py:151-152`) **لا يميّز مصدر الأيام**. إن تراكم ≥2 يوم تمديد بسبب انقطاع البريد وحده (لا مهلة أداء فعلية مُنحت)، ثم أُصلح البريد والعميل لا يزال قاصرًا عن الهدف: التقييم التالي يجد `already_had_grace=True` (لأن `extension_days≥2` من الانقطاع فقط) ويقفز مباشرة إلى `refund_pending` (`guarantee.py:222-245`)، **متجاوزًا مهلة الأداء الإلزامية ذات اليومين التي يستحقها العميل أصلًا بصرف النظر عن الانقطاع.**
- **سيناريو فشل ملموس:** عميل بريده معطوب يومين متتاليين (تقييمان يوميان، كل منهما يضيف يومًا) → `extension_days=2`, `status='extended'`. اليوم الثالث: البريد يُصلَح لكن العميل لا يزال دون الهدف → `mail_broken=False`، `already_had_grace = True` (2≥2) → تعويض تناسبي فورًا بلا أي مهلة أداء حقيقية مُنحت له إطلاقًا.
- **غير مُختبر:** `test_customer_caused_mail_outage_extends_instead_of_refund` (`core/tests/test_guarantee.py:297-327`) يغطي فقط استمرار الانقطاع (يبقى `extended`)، لا الانتقال بعد إصلاح البريد حين `extension_days` المتراكم من الانقطاع وحده ≥2.
- **الإصلاح المقترح:** عمودان/عدّادان منفصلان (`grace_extension_days` و`outage_extension_days`) في `guarantee_ledger`، والفحص `already_had_grace` يقرأ `grace_extension_days` حصرًا لا `extension_days` الكلي. ترحيل صغير إضافي مطلوب (لا يمسّ `0008` — هذه المراجعة لا تُعدّل الكود، تكتفي بالتوصية).

### 3.3 الفهرسة (migration 0008 مقابل استعلامات B5a)
كل استعلام جديد مفهرس فعليًا:
- `daily_reports`: `WHERE status='queued' ... ORDER BY id` → `ix_daily_reports_status` (+PK لدعم `ORDER BY id`). ملاحظة بسيطة غير حاجبة: فهرس مركّب `(status, id)` كان سيخدم صفحات المؤشّر أفضل من فهرسين منفصلين مع نمو الجدول — ليست عائقًا الآن (حجم صغير).
- `guarantee_ledger`: `WHERE status='refund_pending'` → `ix_guarantee_ledger_status`؛ `unique(customer_id, period_start)` يخدم `_fetch_existing_ledger`/`_upsert_ledger`.
- `customer_company_exclusions`: `NOT EXISTS (... WHERE customer_id=:cid AND company_id=:cid)` بـ`send_builder.py` → يغطيه تمامًا `uq_customer_company_exclusions_customer_company (customer_id, company_id)` (بادئة يسرى مطابقة).
- `application_feedback`: `ON CONFLICT (customer_id, send_queue_id, kind)` → نفس القيد الفريد يخدم فحص التكرار مباشرة.
- `link_tokens`: البحث بـ`token_hash` → `uq_link_tokens_token_hash`؛ فهرس إضافي على `customer_id`.
- `applications`: فهرسان جديدان `(status, sent_at)` و`(customer_id, status, sent_at)` يخدمان `/admin/overview` (المرسل اليوم/7 أيام/المرتد اليوم) وعدّادات `reports.py`/`guarantee.py`. جيد لمعيار التوسّع.

### 3.4 الجدولة (scheduler_main.py) — تحقّق حي
- `run_daily_reports_job`: `CronTrigger(hour=16, minute=0)` بتوقيت UTC للمجدول (`BlockingScheduler(timezone="UTC")`) = **19:00 الرياض بالضبط** (UTC+3 ثابت، لا صيفي). صحيح.
- `run_guarantee_round_job`: `CronTrigger(hour=21, minute=30)` UTC = **00:30 الرياض** (اليوم التالي). صحيح.
- **تحقّق حي عبر `ops logs core-scheduler 100`:** بعد إعادة تشغيل core-scheduler (منذ ~ساعة وقت الفحص)، السجلات تُظهر بوضوح `Added job "run_daily_reports_job" to job store "default"` و`Added job "run_guarantee_round_job" to job store "default"` ضمن قائمة كل الوظائف السبع، بلا أي استثناء عند الإقلاع، و`Scheduler started` بعدها مباشرة. مطابق تمامًا للمعيار.

### 3.5 استبعاد الشركة (👎) — hook في send_builder.py
الكود سليم بالفحص المصدري (`send_builder.py:112-122`، معلّق بدقة، مفهرس بشكل صحيح، لا يلمس `opportunities.status`). **فحص حي غير حاسم بسبب بيانات الاختبار:** العميل `id=2` (بيانات Load Test اصطناعية موثّقة سابقًا كـ"موقوف/اصطناعي" في `docs/reports/B3B4-live-review.md`) — تبيّن أن **كل الـ12 صفًا** بـ`send_queue` له `job_id IS NULL` (تحقّق `ops psql` مباشر). لذا `POST /customers/2/feedback {send_queue_id:1, kind:thumbs_down}` أعاد `company_excluded_id: null` بصرف النظر عن صحة الكود — الشرط `sq_row.get("job_id") is not None` بـ`feedback_api.py:78` صحيح ومقصود، لكنه لم يجد `job_id` ليعمل عليه. **[minor]** الإثبات الحي الكامل لهذا المسار (استبعاد شركة فعلي بصف `customer_company_exclusions`) لم يتحقّق بهذه الجلسة رغم المحاولة المباشرة كما طلب التكليف — يتطلب عميلًا (أو صفّ `send_queue`) بـ`job_id` حقيقي غير NULL. **الإثبات غير المباشر كافٍ للقبول:** الاختبارات الآلية (`test_send_builder_exclusion.py`, `test_feedback_api.py`) تغطي نفس المسار بالضبط ببيانات DB حقيقية وتمر بنجاح.
**idempotency التغذية الراجعة:** تحقّق حي كامل — النداء الأول أعاد `{"ok":true,"already_recorded":false,"feedback_id":1,...}`، والثاني (بنفس `customer_id`/`send_queue_id`/`kind`) أعاد فورًا `{"ok":true,"already_recorded":true}` بلا صف مكرّر (`SELECT * FROM application_feedback WHERE customer_id=2` أظهر صفًا واحدًا فقط).

### 3.6 صفحة ربط البريد (link_api.py) — تحقّق حي
- `POST /admin/customers/2/link-token` أعاد رمزًا (`link_token_id:1`) وصلاحية 48 ساعة.
- `GET /link/{token}` أعاد صفحة HTML كاملة (نموذج إدخال بريد/كلمة مرور)، **بلا أي كلمة مرور أو سرّ ظاهر بالصفحة نفسها** (طبيعي — لم يُرسَل النموذج بعد، والتكليف طلب صراحة عدم الإرسال).
- بالفحص المصدري: كلمة المرور الخام (`app_password_raw`) لا تُمرَّر لأي `logger.*` بأي مسار بالملف؛ استثناء غير متوقع يُسجَّل بلا أي تفصيل من الطلب (`logger.exception(...)` بلا `app_password_raw` بالوسائط). التوكن نفسه يُخزَّن كـ`sha256` فقط (`token_hash`)، ويُستخدم مرة واحدة (`used_at` يُفحص ويُمنع إعادة الاستخدام)، ومحدود بـ5 محاولات لكل رمز مع قفل `FOR UPDATE` يمنع سباق تزامن حقيقي بين طلبين متزامنين على نفس الرمز. كل هذا مطابق للمعيار تمامًا.

### 3.7 لوحة الأدمن (overview_api.py) — تحقّق حي
`GET /admin/overview` أعاد 200 بكل الأقسام العشرة المتوقعة (`customers_by_status`, `sends_today`, `sends_last_7_days`, `bounces_today`, `send_queue_by_status`, `mail_links_by_status`, `pending_reports`, `pending_guarantees`, `discovery_freshness`, `dry_run`). كل استعلام يستخدم فهرسًا موجودًا فعليًا (تحقّق بالقسم 3.3 وبفهارس B1-B4 السابقة).

## 4. مراجعة B2b (موجزة كما طُلب)

- **التطبيع والتصنيف منطقي وسليم بالفحص:** `_normalize_for_match` يطوي التشكيل/الهمزات/التاء المربوطة/`Sr.`/`Jr.`/أرقام رومانية/`&`، و`classify_family` عنوان-أولًا-ثم-وصف. مُختبر بـ14 اختبارًا نقيًا يغطي كل حالة توثّق تعليقها.
- **`out_of_scope` مُعامَل بشكل متّسق:** يُحسب "مصنَّفًا" ضمن `family_classified_pct_in_region` ويُستثنى فقط من `family_real_pct_in_region` — نفس التعريف بالضبط في `discovery.py`, `matching.py`, `discovery_api.py`, و`reclassify.py` (تحقّقتُ من الأربعة، لا انحراف بينها).
- **`reclassify.py` idempotent فعليًا:** إعادة حساب مشروطة بالتغيّر فقط (`if new_family != row["family"]`)، ترقيم دفعات بمفتاح `id` (لا OFFSET)، لا يلمس صفوفًا خارج النطاق. مطابق لتوصيف التقرير.
- **`seed_sources()` idempotent:** `ON CONFLICT (name)`/upsert بالرابط لكل مصدر — تكرار الاستدعاء بلا ضرر (تحقّق حي أيضًا عبر سجلات `core-scheduler`: "0 جديد، 110 محدّث، 0 متخطّى" عند كل إقلاع).
- **لا كشط، الأدلة الحية تدعم ذلك:** سجلات `core-scheduler` (القسم 3.4) تُظهر 52 مصدرًا تُستدعى كلها عبر REST APIs رسمية لأنظمة ATS (`boards-api.greenhouse.io`, `api.lever.co`, `api.ashbyhq.com`, `apply.workable.com`, `api.smartrecruiters.com`, `*.recruitee.com/api/`) — لا طلب HTML واحد لصفحة موقع شركة. **[minor]** لا آلية `robots.txt` صريحة بالكود؛ غير حاجب لأن كل المصادر الحالية REST APIs عامة (لا ينطبق عليها `robots.txt` تقليديًا)، لكن يستحق تنبيهًا إن أُضيف لاحقًا جامع `sitemap_jsonld`/HTML (البند المؤجَّل صراحة بتقرير B2b نفسه).
- **B2 نفسه صحيح البقاء REJECT/WIP:** تأكّدتُ أن B2b لم يلمس أيًّا من `dedup_key`/`extract_country_code`/منطق الأقدمية/`dup_ratio_24h_in_region` (بحث نصي في `discovery.py` — التغييرات الوحيدة هي التطبيع وإعادة التسمية والعتبة، لا تعديل بمنطق الديدوب أو استخراج الدولة).

## 5. قائمة الإصلاحات المطلوبة (للمنفّذ القادم)

1. **[major]** `core/app/guarantee.py:evaluate_period` (حول السطور 150-152 و200-245): افصل عدّاد "تمديد انقطاع البريد" عن عدّاد "مهلة الأداء العادية" (عمودان منفصلان بـ`guarantee_ledger`، أو منطق حالة إضافي يميّز مصدر كل يوم تمديد) بحيث لا يُسقِط انقطاع بريد سابق مهلة الأداء الإلزامية ذات اليومين. أضف اختبارًا يغطي: انقطاع بريد يومين → إصلاح البريد → العميل لا يزال قاصرًا → يجب أن يحصل أولًا على مهلة الأداء (`extended`، سبب `grace_period_granted`) قبل أي `refund_pending`.
2. **[minor]** أضف سطرًا/حاشية بـ`PLAN.md` (أو ملف فهرس منفصل) يذكر أن `docs/reports/B2-executor.md`, `B2-executor-round2.md`, `B2-review.md` تقارير تاريخية استُبدلت بـ`B2-review-2.md` — حتى لا تبدو "مفقودة" لاحقًا أثناء أي تشذيب إضافي لحجم الملف.
3. **[minor]** إثبات حي كامل لمسار استبعاد الشركة (👎 → صف `customer_company_exclusions` فعلي) يحتاج عميل/صف `send_queue` بـ`job_id` غير NULL (لا يوجد حاليًا سوى بيانات Load Test اصطناعية لعميل `id=2`) — لا تعديل كود مطلوب، فقط بيانات اختبار حية مناسبة عند المراجعة القادمة.
4. **[minor]** فهرس مركّب `(status, id)` على `daily_reports` بدل فهرسين منفصلين (`status` + PK) — تحسين أداء مستقبلي لصفحات المؤشّر مع نمو الجدول، غير عاجل بالحجم الحالي.
5. **[cleanup، غير عاجل]** صفوف مكرّرة حرفيًا بـ`data/sources_seed.csv` (Decima International/Checkout.com/BioCatch) — موثَّقة بالفعل بتقرير B2b كـ"لا ضرر وظيفي" (upsert)، تستحق تنظيفًا لاحقًا فقط.
