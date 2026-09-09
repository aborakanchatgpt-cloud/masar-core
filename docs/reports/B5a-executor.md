# B5a — تقرير المنفّذ (التقارير والضمان والاستبعاد ولوحة الأدمن وربط البريد الذاتي)

نُفّذ في sandbox معزول (`/home/claude/masar-core-b5`)، بلا SSH، عبر جسر MCP `Masar_Core` (`repo_write`/`job`/`core_call`/`ops`). لم تُلمَس ملفات B2b (`discovery.py`, `collectors/`, `taxonomy`) ولا `customers_api.py`/`planner.py`/`matching.py` (WIP بانتظار مراجعة منفصلة). الترحيل الجديد: `0008_b5_reports.py` (B2b يستخدم `0009` كما طُلب).

## التصميم

### 1) التقرير اليومي (`reports.py` + `reports_api.py`)
- جدول `daily_reports` (customer_id, report_date, payload JSONB, status queued/delivered/failed, channel, created_at, delivered_at) + `unique(customer_id, report_date)` + فهرس على status.
- `build_customer_report(customer_id, date)`: قراءة فقط، يبني payload كامل + نص عربي جاهز (`text`) بنبرة إنسانية — تحية عشوائية (seed حتمي per customer+date)، تقديمات اليوم (شركة/مسمى/مدينة)، تقدّم الفترة بكلمات ("قدّمنا لك حتى الآن N فرصة" — بلا كسر ولا "حد")، قسم ردود اليوم، قسم "استبعدنا لك" (أسباب ودّية من `opportunities.reasons->>'skip_reason'` المحدودة لـ company_cooldown/weekly_company_cap/no_apply_email + استبعاد العميل)، خاتمة دعاء.
- **قيد موثَّق صراحة:** استبعادات مستوى المطابقة (سنوات خبرة/جنسية عبر `matching.check_disqualifiers`) لا تُخزَّن بأي جدول اليوم — `planner.py:_select_for_customer` يُسقطها قبل إنشاء أي صفّ `opportunities` (`if result.disqualified: continue`). فجوة بنيوية من B3، خارج نطاق B5a تعديلها في ملفات WIP. **NEEDS-OWNER.**
- `run_reports_round(date)`: صفّ واحد idempotent لكل عميل **نشط فقط** (`status='active'`)، `ON CONFLICT (customer_id, report_date) DO NOTHING`.
- Endpoints (admin token): `GET /admin/reports/pending` (مُصفّحة after_id)، `POST /admin/reports/{id}/delivered {channel}`، `POST /admin/reports/run {date?}`، `GET /customers/{id}/report?date=` (من القاعدة أو بناء حي للمعاينة).
- Scheduler: `run_daily_reports_job` — `CronTrigger(hour=16, minute=0)` UTC = 19:00 الرياض.

### 2) التغذية الراجعة والاستبعاد (`feedback_api.py` + hook في `send_builder.py`)
- جدولان: `application_feedback` (customer_id, send_queue_id, kind thumbs_down|celebrate, note, unique(customer_id,send_queue_id,kind)) و `customer_company_exclusions` (customer_id, company_id, reason, unique(customer_id,company_id)).
- `POST /customers/{id}/feedback {send_queue_id, kind, note?}`: idempotent (`ON CONFLICT DO NOTHING`، تكرار = 200 no-op)؛ `thumbs_down` يُدرج استبعاد شركة تلقائيًا (`reason='customer_feedback_thumbs_down'`)؛ `celebrate` بلا أثر جانبي.
- Hook: أضفت شرط `NOT EXISTS (SELECT 1 FROM customer_company_exclusions ...)` إلى `send_builder._fetch_candidate_opportunities` (تعديل سطري واحد بتعليق موثّق) — يمنع أي فرصة لشركة مستبعدة من دخول `send_queue` فورًا، حتى لو كانت `opportunities.status='planned'` أصلًا قبل الاستبعاد. لا يلمس `opportunities.status` نفسه (يبقى 'planned' بصمت — تعديله مسؤولية `planner.py` فقط).

### 3) دفتر الضمان (`guarantee.py` + `guarantee_api.py`)
- جدول `guarantee_ledger` (customer_id, period_start, period_end, target, counted_sent, bounced, shortfall, extension_days, refund_amount numeric(10,2) NULL, status computed/extended/refund_pending/settled/na, unique(customer_id,period_start)).
- عمود جديد `customers.price_sar` (numeric(10,2) NULL) — لا عمود سعر موجود سابقًا؛ NULL يعني `refund_amount=NULL, status='refund_pending'` (ينتظر المالك).
- `evaluate_period(customer_id)`: يقيّم آخر اشتراك منتهي فعليًا (`ends_at <= now`)؛ الترتيب: (أ) بلغ الهدف → `computed`، شراكة تُغلق؛ (ب) بريد العميل معطوب (`mail_links.status != 'ok'`) → تمديد يوم دائمًا، **لا يصل أبدًا لمرحلة التعويض طالما البريد معطوب** حتى لو استُهلك التمديد العادي؛ (ج) لم يستهلك تمديد يومين بعد → يمنحه (`extension_days=2`)؛ (د) استُهلك التمديد والبريد سليم → تعويض تناسبي `shortfall × price_sar ÷ 510` (أو NULL إن لم يُحدَّد سعر) بحالة `refund_pending`. بلا اشتراك مستحق → `na` (يغطي عملاء الرصيد المسبق).
- **NEEDS-OWNER موثَّق:** لا جدول تاريخي لحالة `mail_links` يومًا بيوم، فـ`customer_caused_days` لا يُحسب كعدد صريح (الصيغة الحرفية بالدليل §3.12 تحتاج تاريخًا يوميًا). التفسير المطبَّق (تمديد يوم لكل تقييم طالما معطوب) يحقق نفس الأثر العملي (لا تعويض أثناء انقطاع بريد العميل) دون جدول جديد.
- Endpoints: `GET /admin/guarantee/pending`، `POST /admin/guarantee/{id}/settle` (idempotent، يرفض تسوية غير `refund_pending`).
- Scheduler: `run_guarantee_round_job` — `CronTrigger(hour=21, minute=30)` UTC = 00:30 الرياض.

### 4) لوحة الأدمن (`overview_api.py`)
`GET /admin/overview`: استعلام واحد مفهرس لكل قسم — customers_by_status (`ix_customers_status`)، sends_today/last_7_days/bounces_today (فهرسا `applications` جديدان بترحيل 0008)، send_queue_by_status، mail_links_by_status، pending_reports/pending_guarantees (فهارس status جديدة)، discovery_freshness (max عبر `ix_jobs_first_seen_at` الموجود)، `dry_run` (`sender.is_dry_run()`).

### 5) ربط البريد الذاتي (`link_api.py`)
- جدول `link_tokens` (customer_id, token_hash sha256, expires_at, attempts, used_at) + فهرس على customer_id ويونيك على token_hash.
- `POST /admin/customers/{id}/link-token` (محمي بتوكن الإدارة) → رمز 48 ساعة، لا يُخزَّن خامًا.
- `GET /link/{token}` صفحة HTML عربية RTL كاملة inline CSS، بلا أي ذكر أتمتة/AI، بلا سرّ بالرابط سوى الرمز العشوائي نفسه.
- `POST /link/{token}` → يستدعي `mail_api.create_mail_link` فعليًا (تحقق SMTP/IMAP حقيقي)؛ `skip_verify=1` بالاستعلام يُفعَّل **فقط** إذا كان DRY_RUN فعليًا فعّالًا أيضًا. حدّ 5 محاولات لكل رمز (`FOR UPDATE` يمنع سباقًا). كلمة المرور لا تُسجَّل أبدًا (لا logger ولا استجابة).

## الاختبارات (28 جديدة، الإجمالي 214 → 242)
- `test_reports.py` (5): تقديمات اليوم + استبعاد المرتد من التقدّم، عميل غير موجود، لا فرص اليوم، نص التقدّم بلا كسر/حد، الجولة نشط-فقط + idempotent.
- `test_guarantee.py` (7): بلوغ الهدف، استبعاد المرتد من المُحتسَب، تمديد أول عجز، تعويض تناسبي بعد استهلاك التمديد، `refund_pending` بسعر NULL، رصيد بلا اشتراك = na، انقطاع بريد العميل يُمدِّد بدل تعويض.
- `test_feedback_api.py` (6): 👎 يستبعد الشركة، 🎉 لا يستبعد، idempotency بلا تكرار، kind غير صالح 400، عميل غير موجود 404، send_queue لعميل آخر 404.
- `test_send_builder_exclusion.py` (1): فرصة شركة مستبعدة لا تصبح مرشّحة أبدًا، بلا لمس `opportunities.status`.
- `test_link_api.py` (7): إنشاء رمز، نموذج رابط صالح/غير صالح/منتهٍ، نجاح فعلي بـskip_verify+DRY_RUN، رفض كلمة مرور خاطئة الطول، حدّ 5 محاولات.
- `test_overview_api.py` (2): كل الأقسام حاضرة، الأعداد تعكس إدراجًا حقيقيًا (قبل/بعد).

بوابات الجودة محليًا (قبل أي دفع): `compileall` ✅، `import app.main, app.scheduler_main` ✅، `pytest -q` 214→242 (0 فشل) على Postgres 16 محلي حقيقي، `alembic upgrade head` / `downgrade -1` / `upgrade head` نظيفة مرتين (قاعدة موجودة وقاعدة مُعاد إنشاؤها).

## الدفعات (Commits)
20 دفعة منفصلة عبر `repo_write` (ملف واحد/دفعة)، بالترتيب: migration `0008` → `requirements.txt` (python-multipart) → `reports.py` → `guarantee.py` → `reports_api.py` → `feedback_api.py` → `guarantee_api.py` → `overview_api.py` → `link_api.py` → `send_builder.py` (hook) → `main.py` (استيراد محروس) → `scheduler_main.py` (مهمّتان جديدتان — بعد أن أصبحت `reports.py`/`guarantee.py` حيّتين) → 6 ملفات اختبار → هذا التقرير → `PLAN.md`. جميعها أعادت `exit_code: 0` عبر `job()`.

## التحقق الحي
- `GET /health` → 200، الخدمة حيّة.
- `alembic_version` عبر `ops psql` → `0008_b5_reports`.
- `GET /admin/overview` → 200 مع كل الأقسام العشرة.
- `POST /admin/reports/run` ثم `GET /admin/reports/pending`: العميل الاصطناعي `id=2` (Load Test، **موقوف** حسب `docs/reports/B3B4-live-review.md`) **لا يظهر** بالنتائج — `run_reports_round` يغطي `status='active'` فقط بتصميم متعمّد (تقرير خدمة للمشترك الفعّال)، موثَّق بكود `reports.py`.

## NEEDS-OWNER / متابعة مستقبلية
1. قسم "استبعدنا لك" لا يعرض استبعادات مستوى المطابقة (سنوات خبرة/جنسية) لأن `planner.py` لا يخزّنها اليوم — يحتاج تعديل `planner.py`/`matching.py` (WIP) بمراجعة منفصلة.
2. `customer_caused_days` بدفتر الضمان مبسَّط (تمديد يوم لكل تقييم طالما البريد معطوب) بدل عدّ تاريخي دقيق — يحتاج جدول تاريخ حالة `mail_links` لو أراد المالك الصيغة الحرفية بالدليل.
3. `customers.price_sar` فارغ لكل العملاء الحاليين — المالك يحتاج تعبئته يدويًا حتى تُحسب مبالغ التعويض بدل NULL.
4. اختبار المسار الكامل لـ`POST /link/{token}` بلا `skip_verify` (تحقق SMTP/IMAP حقيقي) لم يُجرَ حيًّا — نفس بلوكر mailpit/IMAP الموثَّق سابقًا بـ`B3B4-live-review.md`.
