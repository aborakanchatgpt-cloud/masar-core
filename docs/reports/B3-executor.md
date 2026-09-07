# B3 — تقرير أدلة المنفّذ: العملاء/الملف الشخصي/المطابقة/المحفظة/الخطة

**التاريخ:** 2026-09-07
**النطاق:** ملفات B3 فقط (`migrations/versions/0004_b3_customers.py`،
`core/app/matching.py`، `core/app/planner.py`، `core/app/customers_api.py`،
`core/tests/test_matching.py`، `scripts/bench_planner.py`،
`deploy/ops/scripts/bench-planner.sh`) + تعديلان محروسان على `core/app/main.py`
و`core/app/scheduler_main.py` لا يمسّان ملكية B4 (`app/mail*`, `app/inbox_api.py`,
`app/collectors/*`).

---

## 1. ملخص المخطط (migration 0004_b3_customers)

`down_revision = "0003_b2_region_and_dedup_fixes"`. جداول جديدة:

| جدول | الغرض | ملاحظات بنيوية |
|---|---|---|
| `customers` | العميل نفسه | `cities`/`families` **JSONB** (لا `json` عادي — لازم لعمليات `?`/`jsonb_array_elements_text` بالمخطّط)، `status` CHECK(active/paused/expired)، `email_service` UNIQUE، `target_daily` افتراضي 17 |
| `profiles` | الملف الشخصي المُستخرج | `customer_id` PK/FK CASCADE، `years_exp NUMERIC(4,1)`، `certs/skills/titles/languages` JSONB، `extracted_by` CHECK(rules/claude) |
| `products` | كتالوج المنتجات | `type` CHECK(subscription/credits/standalone_cv) — مبذورة (seed) بأسعار مبدئية (§5) |
| `orders`, `ledger` | الطلبات ودفتر الحركات | `ledger.reason` CHECK(purchase/application_sent/bounce_refund/guarantee_refund/adjustment) |
| `wallets` | الرصيد | `customer_id` PK/FK CASCADE، `balance` **CHECK (balance >= 0)** على مستوى القاعدة — خط دفاع ثانٍِِِ إضافةً لقفل الصف بالتطبيق (§4) |
| `subscriptions` | الاشتراكات | `status` CHECK(active/extended/closed) |
| `opportunities` | الفرص المخطّطة يوميًا | `tier` CHECK(A/B/C/C2/D)، `status` CHECK(planned/sent/skipped/expired)، **UNIQUE(customer_id, job_id)**، فهرس (customer_id, planned_for) |
| `applications` | التطبيقات الفعلية المُرسَلة | عمود `company_key` مُطبّع (denormalized) + فهرس (company_key, sent_at) — يخدم فحص التبريد بلا JOIN لجدول companies بكل استعلام |
| `feedback` | تغذية راجعة (down/up/note) | — |
| `company_cooldowns` | تبريد صريح (60 يومًا) | PK مركّب (company_key, customer_id) |

فهرس إضافي على `jobs`: `ix_jobs_family_out_of_region_first_seen(family, out_of_region, first_seen_at)`
يخدم استعلامات الترشيح بمجموعة (set-based) بالمخطِّط. **لم يُلمَس عمود
`jobs.locations` ولا أي عمود آخر بجدول jobs المملوك لفريق الاكتشاف.**

تحقّق فعلي محليًا (Postgres 16 حقيقي، ليس فحصًا نحويًا فقط): سلسلة الترقية
الكاملة 0001→0004 نجحت، والتراجع خطوة ثم إعادة الترقية نظيفان، ـ19 جدولًا
موجودة، والمنتجات مبذورة بصف واحد لكل رمز (`ON CONFLICT (code) DO NOTHING`).

---

## 2. قواعد المطابقة (core/app/matching.py)

### الاستبعاد القاطع (check_disqualifiers) — ترجع كل الأسباب المطابقة معًا

| السبب | الشرط |
|---|---|
| `out_of_region` | `job.out_of_region = true` |
| `family_excluded` | العائلة ضمن `EXCLUDED_FAMILY_NAMES` (حاليًا `sales_excluded`) — قاطع حتى مع توسيع العائلة |
| `family_not_selected` | العائلة ليست ضمن عائلات العميل المختارة، ولا نمرّ بتمريرة التوسيع |
| `city_not_selected` | مدينة الوظيفة معروفة وليست ضمن مدن العميل — **لا يوجد توسيع جغرافي إطلاقًا** (فقط توسيع العائلة C2 مدعوم) |
| `years_exceeded` | `years_min > years_exp + YEARS_SLACK` |
| `seniority_too_senior` | فرق الرتبة (وظيفة − ملف) ≥ 2 |
| `seniority_too_junior` | فرق الرتبة (ملف − وظيفة) ≥ 3 |
| `saudi_only_mismatch` | الوظيفة سعودي حصرًا والعميل ليس كذلك |
| `degree_mismatch` | (غير مُفعّل عمليًا اليوم — راجع الفجوات §6) |
| `company_cooldown` | مُمرَّر من planner.py (60 يومًا) |
| `weekly_company_cap` | مُمرَّر من planner.py (3 عملاء/شركة/نافذة 7 أيام متحركة) |

### الدرجة (compute_score) — كل جزء 0..1، ثم وزن ثابت

| الجزء | الوزن | القاعدة |
|---|---|---|
| title | 0.35 | Jaccard على توكنات مُوسّعة بمرادفات (QA/QC/HSE/PM...) بين مسمى الوظيفة ومسميات الملف المرجّحة؛ fallback 0.5 عند تطابق العائلة فقط |
| skills | 0.30 | Jaccard بين مهارات الوظيفة والملف؛ 0.5 إن لم تُستخرج مهارات بالإعلان |
| seniority | 0.15 | `1 − 0.25×|فرق الرتبة|`؛ 0.6 إن كانت مجهولة |
| location | 0.10 | 1.0 مطابقة، 0.3 غير محددة (لن تصل 0.0 عمليًا — المستبعدة تُفلتَر قبلها) |
| recency | 0.05 | 1.0 خلال 3 أيام → انحدار خطي → 0.5 عند 14 يومًا فأكثر؛ 0.5 إن كان التاريخ مجهولًا |
| source_quality | 0.05 | 0.3 وكالة توظيف (بالاسم)، 1.0 نموذج تقديم مباشر لصاحب العمل، 0.6 غير معروف |

### الطبقات
A ≥ 0.75، B ≥ 0.55، C ≥ 0.40 (ضمن عائلة/مدينة مختارة)، **C2** = نفس عتبة C
لكن عبر تمريرة توسيع العائلة فقط (المدينة تبقى قاطعة دائمًا)، D < 0.40
(لا تُخطَّط ولا تُرسَل إطلاقًا).

**اختبارات:** `core/tests/test_matching.py` — **55 حالة** (المطلوب ≥25)، تشمل:
كل سبب استبعاد على حدة + حدوده (هامش السنة، فروق الرتب)، توسيع العائلة
(بما فيها أن `sales_excluded` تبقى مستبعدة حتى مع التوسيع)، القيد الجغرافي
الصارم (بلا توسيع)، عتبات الطبقات الأربع (11 حالة parametrize على الحدود
الدقيقة)، اختبارات وحدة لكل دالة `score_*`، تحقّق مجموع الأوزان = 1.0، وحالة
تجميع أسباب استبعاد متعددة معًا. النتيجة: **55 نجحت / 55**، والمجموعة
الكاملة (بما فيها ملفات ما قبل B3) **108 نجحت / 108** بلا أي تراجع (regression).

---

## 3. المخطِّط (core/app/planner.py) والجدولة

- تمريرتان لكل جولة: **أساسية** (عائلات/مدن مختارة، `SELECT DISTINCT ON
  (c.id, j.id)` مع LATERAL join على مصفوفات JSONB لتفادي التكرار)، ثم
  **موسّعة** (توسيع العائلة فقط، JOIN عادي بلا LATERAL فتفرّع فلا حاجة
  لـDISTINCT) — فقط للعملاء الذين لم يبلغوا `target_daily` بعد التمريرة
  الأولى.
- **إدراج idempotent**: `ON CONFLICT (customer_id, job_id) DO UPDATE ...
  WHERE status='planned'` — استدعاء متكرر لنفس اليوم لا يُنشئ صفوفًا مكررة
  ولا يُهدر "خانات" الهدف اليومي (راجع إصلاح خلل "الخانة المهدرة" أدناه).
- **تبريد الشركة/سقف أسبوعي**: يُجلَبان دفعة واحدة بمجموعة SQL
  (`fetch_cooldown_pairs`/`fetch_weekly_cap_companies`) من UNION
  لـ`applications` + `company_cooldowns`، ثم يُمرّران كقيم منطقية جاهزة
  لـ`matching.evaluate()` — لا استعلام DB داخل حلقة بايثون لكل مرشّح.
- **قاعدة "فرصة واحدة لكل شركة يوميًا لكل عميل"**: تُطبّق عبر مجموعتي
  `job_id`/`company_key` مُخطّطة مسبقًا، تُهيّآان من الفرص المخطّطة فعليًا
  ذلك اليوم (تشمل تشغيلات top-up الساعية السابقة بنفس اليوم) وتُحدّثان فور
  اختيار كل مرشّح جديد — **قبل** تشغيل التمريرة الموسّعة، حتى لا تُعاد نفس
  الشركة عبر التمريرتين.
- **الجدولة** (`core/app/scheduler_main.py`، عملية `core-scheduler` منفصلة):
  `CronTrigger(hour="3-12", minute=0)` بتوقيت UTC = 06:00–15:00 الرياض
  (UTC+3 ثابت، بلا توقيت صيفي بالسعودية) — 10 تشغيلات/يوم،
  `max_instances=1 + coalesce=True` يمنعان التراكم، والاستثناءات تُلتقَط
  فتُسجّل ولا توقف الجدولة (نفس فلسفة `run_collector_round` من B2).

### خلل مُكتشَف ومُصحّح أثناء الاختبار المحلي الحقيقي ("الخانة المهدرة")
اختبار محلي بجولتَي `plan/run-now` متتاليتين لنفس اليوم كشف أن مرشّحًا
مخطّطًا مسبقًا يمكن أن يُعاد اختياره بالجولة التالية، فيستهلك "خانة" من
الهدف اليومي عبر `ON CONFLICT DO UPDATE` دون أن يُنشئ فرصة جديدة فعليًا —
النتيجة: عميل يتوقف عند 16 فرصة رغم توفر مرشّحين جدد كافيين لبلوغ 17. أُصلح
بجلب مجموعتي job_id/company_key المخطّطتين فعلًا لليوم قبل كل تمريرة
(`_fetch_existing_planned`)، واستبعادهما مبكرًا بـ`_select_for_customer`.
**تحقّق بعد الإصلاح** (Postgres 16 حقيقي محليًا): جولة أولى → 16 فرصة،
جولة ثانية → **17/17 بالضبط** (زيادة فرصة واحدة فقط، لا إعادة)، جولة ثالثة
→ **0 فرص جديدة** (idempotent صحيح)، 17 شركة مختلفة لـ17 فرصة (قاعدة "شركة
واحدة/يوم" سليمة عبر التمريرتين).

### قياس الأداء (scripts/bench_planner.py)
بيانات اصطناعية حتمية (`random.Random(seed=42)`) داخل مخطط Postgres معزول
تمامًا (`b3bench`، يُحذَف بالكامل بالنهاية حتى عند الفشل) — **لا يلمس أي
جدول إنتاجي**. النتيجة المُقاسة محليًا (Postgres 16 حقيقي، نفس عتبة القبول
1,500×3,000):

```json
{"ok": true, "n_customers": 1500, "n_jobs": 3000,
 "seed_seconds": 0.355, "plan_seconds": 28.404,
 "customers_active": 1500, "customers_planned": 1500,
 "opportunities_planned": 25500, "under_5_minutes": true}
```

**≈28.4 ثانية** لكامل الجولة (المعيار: <300 ثانية) — كل الـ1,500 عميل بلغوا
هدفهم اليومي كاملًا (17×1500=25500). هامش أمان كبير (~10×) حتى مع نمو حجم
البيانات الحقيقي لاحقًا.

**ملاحظة تنفيذية موثّقة (فجوة صغيرة):** `deploy/ops/run_queue.sh` (ملف
مشترك خارج ملكية B3) يفرض `timeout 120` ثانية على أمر `script` — أقل من
حد القبول 300 ثانية بالتكليف. النتيجة الفعلية (28 ثانية) بعيدة جدًا عن كلا
الحدّين فلا أثر عملي الآن، لكن إن كبر حجم بيانات البنش مستقبلًا يجب رفع هذا
التايم-آوت بـ`run_queue.sh` أيضًا — راجع §6.

`deploy/ops/scripts/bench-planner.sh` يُشغّل عبر `ops("script",
["bench-planner"])`؛ يستدعي
`docker compose exec -T core python /repo/scripts/bench_planner.py`
(المسار `/repo/scripts/...` لا `scripts/...` النسبي لأن `core/Dockerfile`
ينسخ `core/app/` فقط داخل الصورة — `scripts/bench_planner.py` متاح فقط عبر
تركيب المستودع الكامل للقراءة فقط `./:/repo:ro`).

---

## 3.5 عيّنة خطة فعلية (عميل اختباري: مهندس عمليات سعودي، 6 سنوات خبرة، الرياض/جدة)

بيانات الملف: `years_exp=6.0`، `nationality_saudi=true`، `families=["chem_process"]`،
`cities=["Riyadh","Jeddah"]`. أول 10 من أصل 17 فرصة فعلية أنتجها
`GET /plan/1/today` (بيانات وظائف اختبارية محلية، لا بيانات إنتاجية):

| job_id | الطبقة | الدرجة | المسمى | الشركة | المدينة |
|---|---|---|---|---|---|
| 37 | A | 0.9611 | Process Engineer 2 | Zeeco Test | Riyadh |
| 78 | A | 0.9400 | Process Engineer | Chem Co 25 | Jeddah |
| 65 | A | 0.9400 | Process Engineer | Chem Co 12 | Riyadh |
| 66 | A | 0.9400 | Process Engineer | Chem Co 13 | Jeddah |
| 67 | A | 0.9400 | Process Engineer | Chem Co 14 | Riyadh |
| 77 | A | 0.9400 | Process Engineer | Chem Co 24 | Riyadh |
| 56 | A | 0.9397 | Process Engineer | Chem Co 3 | Jeddah |
| 68 | A | 0.9397 | Process Engineer | Chem Co 15 | Jeddah |
| 57 | A | 0.9374 | Process Engineer | Chem Co 4 | Riyadh |
| 69 | A | 0.9374 | Process Engineer | Chem Co 16 | Riyadh |

المجموع الكامل: **17/17 فرصة** (الهدف اليومي `target_daily=17` بُلغ بالكامل)،
**16×A + 1×C2** (فرصة واحدة فقط احتاجت توسيع العائلة — بيانات الاختبار
المحلية غنية بوظائف `chem_process` مطابقة، لذلك نادرًا ما احتاجت التوسيع)،
**17 شركة مختلفة** لـ17 فرصة (لا تكرار شركة بنفس اليوم)، **صفر صفوف
مستبعدة** بجدول `opportunities` (بالتصميم: الاستبعاد يُطبّق قبل الإدراج،
فلا تصل صفوف مستبعدة للجدول أصلًا — لا عمود "disqualified" بالجدول لهذا
السبب تحديدًا).

استدعاء ثانٍِِ متكرر لنفس اليوم (`POST /plan/run-now?customer_id=1`) أعاد
`opportunities_planned: 0` — **idempotent** مؤكّد.

---

## 3.6 أمثلة API فعلية (مُختَبَرة محليًا)

```
POST /customers
Authorization: Bearer <CORE_ADMIN_TOKEN>
{"name": "...", "email_service": "...", "cities": ["Riyadh","Jeddah"], "families": ["chem_process"]}
→ 200 {"ok": true, "customer_id": 1, ...}

POST /customers/1/profile
{"cv_text": "..."}   # استخراج قواعدي (rules-based) عبر field_extractor.py
→ 200 {"ok": true, "years_exp": 6.0, "seniority": null, "skills": [...], ...}

POST /wallet/1/credit
{"amount": 100, "reason": "purchase"}
→ 200 {"ok": true, "balance": 100}
# محاولة خصم أكبر من الرصيد المتاح رُفضت فعليًا (اختُبر) بلا تعديل جزئي للرصيد

GET /plan/1/today
→ 200 {"customer_id": 1, "date": "2026-09-07", "count": 17, "opportunities": [...]}

POST /plan/run-now?customer_id=1
→ 200 {"ok": true, "sync": true, "result": {"opportunities_planned": 1, "seconds": 0.015, ...}}

GET /admin/matching/explain?customer_id=1&job_id=37
→ 200 {"disqualified": false, "reasons": [], "score": 0.9611, "tier": "A",
       "score_parts": {"title": 0.889, "skills": 1.0, "seniority": 1.0,
                        "location": 1.0, "recency": 1.0, "source_quality": 1.0}}

GET /admin/matching/explain?customer_id=1&job_id=21   # وظيفة عائلة "quality" (خارج اختيار العميل)
→ 200 {"disqualified": false, "score": 0.4005, "tier": "C2",
       "widened_family_pass": true}   # قبلت فقط عبر تمريرة التوسيع
```

كل نقاط النهاية أعلاه محمية بـ`require_admin_token` (Bearer fail-closed) —
اختُبر أن استدعاء بلا توكن أو بتوكن خاطئ يُرفض بـ401/403.

---

## 4. المحفظة والمعاملات

`POST /wallet/{id}/credit`: قفل صف `SELECT ... FOR UPDATE` على `wallets`،
ثم تحديث الرصيد وإدراج سطر `ledger` **بمعاملة واحدة**، مع رفض أي عملية
تُنتج رصيدًا سالبًا (تحقّق تطبيقي + قيد `CHECK (balance >= 0)` بقاعدة
البيانات كخط دفاع ثانٍ مستقل). اختُبر محليًا فعليًا: إضافة رصيد ثم محاولة
خصم أكبر من الرصيد المتاح — رُفضت بخطأ واضح، الرصيد لم يتغيّر (لا معاملة
جزئية).

---

## 5. المنتجات المبذورة (products seed)

| code | النوع | السعر (ريال) | تفاصيل |
|---|---|---|---|
| SUB30 | subscription | 90 | 30 يومًا، هدف 17 فرصة/يوم |
| CR100 | credits | — | 100 رصيد |
| CR300 | credits | — | 300 رصيد |
| CR510 | credits | — | 510 رصيد |
| CV1 | standalone_cv | 15 | سيرة ذاتية مستقلة |

أسعار مبدئية (placeholder) بانتظار قرار أحمد النهائي — راجع §6.

---

## 6. الفجوات وما يحتاج قرار أحمد

1. **هامش سنوات الخبرة `YEARS_SLACK=1`**: الدليل الأصلي يذكر هامش سنتين؛
   شُدّد عمدًا لسنة واحدة بتكليف B3 نفسه — **قرار مقصود موثّق هنا، وليس
   خطأًا**، لكن يحتاج تأكيد أحمد إن كان الهامش الأصح فعلًا 1 أو 2.
2. **`degree_mismatch` غير مُفعّل عمليًا**: جدول `jobs` الحالي لا يحمل عمود
   "الشهادة/التخصص المطلوب" مستخرجًا بعد (`field_extractor.py` مملوك لفريق
   الاكتشاف — لا يجوز لـB3 تعديله). الدالة جاهزة وتقبل الحقل، لكنه دائمًا
   `None` اليوم فلا يُسبّب استبعادًا أبدًا. يحتاج تنسيقًا مستقبليًا مع منفّذ
   الاكتشاف لإضافة الاستخراج.
3. **أسعار المنتجات** (§5) مبدئية بانتظار تأكيد أحمد.
4. **`timeout 120`s بـ`run_queue.sh` لأمر `script`** أقل من حد قبول البنش
   (300s) — لا أثر عملي حاليًا (28s فعليًا) لكن يستحق رفعًا وقائيًا إن كبر
   حجم بيانات البنش الاصطناعية مستقبلًا (ملف مشترك خارج ملكية B3، فقرار
   تعديله يحتاج تنسيقًا).
5. **مصادر جودة (`source_quality`)**: التمييز بين "وكالة توظيف" و"صاحب عمل
   مباشر" اليوم اجتهادي بمطابقة كلمات دلالية باسم الشركة (`AGENCY_NAME_HINTS`)
   لعدم وجود عمود "نوع الشركة" بجدول `companies` بعد — تحسين مستقبلي محتمل.
6. **معجم مرادفات المسمى الوظيفي** (`TITLE_SYNONYM_GROUPS`) طبقة خفيفة
   يدوية بديلة مؤقتة لمعجم ESCO الكامل (`data/esco/*.csv` غير موجود بعد
   بالمستودع حتى تاريخه) — يُنصَح باستبداله لاحقًا بمعجم أشمل.
