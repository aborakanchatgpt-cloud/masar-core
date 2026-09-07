# مراجعة B2 (الدورة الثانية) — الاكتشاف الحقيقي (Masar Core)

**المراجع:** جلسة مراجعة مستقلة (Fable) — أدلّة حيّة فقط: `core_call` لـ`/admin/stats`+`/health`، و`ops("psql", …)` قراءة مباشرة على قاعدة الإنتاج، وقراءة كود `discovery.py`/`field_extractor.py`/`taxonomy_local.yaml` الفعلي من المستودع. لا اعتماد على أرقام `docs/reports/B2-executor.md` نصيًا — كل رقم أدناه أُعيد حسابه مستقلًا.
**تاريخ المراجعة:** 2026-09-07 (~18:44–19:05 UTC)
**الحالة اللحظية:** `jobs_total=19366`، `jobs_in_region=3804`، `jobs_sa=2935`، `sources_active=55`، `companies_total=104`، آخر جولة اكتشاف 18:36 UTC (65 مصدرًا، 0 خطأ).

## الخلاصة التنفيذية

البنية التحتية لا تزال تعمل (الجولات تنجح، القيد `uq_jobs_dedup_key` يمنع فعليًا أي تصادم حرفي بمفتاح التكرار — 0 تصادم على كامل الجدول)، والامتثال نظيف (كل المصادر الجديدة الخمس المفحوصة واجهات ATS رسمية JSON بلا كشط). **لكن اكتُشف عيبان جديدان عاليا الأثر لم يرصدهما المنفّذ ولا المراجعة السابقة**، بالإضافة إلى استمرار فشل معياري القبول الرقميين الأساسيين لـB2:

1. **التكرار الحقيقي أسوأ ممّا يُعلَن، وليس "قيدًا تشخيصيًا" كما ادّعى المنفّذ.** باستخدام `apply_url` كهوية الإعلان الحقيقي (الأوثق المتاحة)، **92.7%** من وظائف آخر 24 ساعة داخل النطاق (3528 من 3804) تشارك نفس الـ`url` مع صف آخر على الأقل، و**1432 رابط** فريد لديه أكثر من `dedup_key` مختلف تحته. السبب: مصادر Workable (Eram Talent، Hudson Manpower) تُرجع نفس الإعلان (نفس معرّف/`url` التقديم) مرارًا — مرة لكل مدينة مرشَّحة — وبما أن `dedup_key` الآن يشمل `location_text` الخام، كل مدينة تُنتج `dedup_key` مختلفًا فيُدرَج صفًا منفصلًا. معدّل التكرار الصادق (صفوف زائدة عن إعلان واحد لكل `url`) ≈ **55.1%** (2096 صف زائد من 3804) — أعلى من رقم `/admin/stats` المُعلَن (42.8%) وليس مجرد تضخيم تشخيصي كما ادّعى تقرير المنفّذ.
2. **تلوّث حقيقي بمجموعة "داخل النطاق" نفسها.** فحص مباشر لعمود `location` الخام لصفوف `out_of_region=false` بآخر 24 ساعة: **651 صفًا من 3804 (17.1%)** موقعها الخام يذكر صراحة دولة/مدينة غير خليجية بلا لبس (سنغافورة 222، ماليزيا 30، فيتنام 22، الهند 71+، مصر 27، رومانيا 14، الفلبين 10، باكستان 17، جنوب أفريقيا 5، بريطانيا/أمريكا...) بينما `country_code`/`out_of_region` يضعانها "داخل الخليج". السبب: `extract_country_code()` يسقط احتياطيًا لفحص العنوان+أول 300 حرف من الوصف (`extra_text`) عند فشل مطابقة `location_text`، **بلا شرط** — فأي ذكر عرَضي لدولة خليجية بالوصف (فقرة "نغطي الشرق الأوسط: السعودية، الإمارات، عُمان...") أو لاحقة عنوان عامة مثل "(Saudi Arabia)" (شائعة بوكالات التوظيف Eram Talent/Hudson Manpower لوصف "العميل" لا مكان العمل الفعلي) تكفي لتصنيف وظيفة مقرّها الحقيقي سنغافورة أو رومانيا كـ"داخل الخليج". **هذا يعني أن `jobs_in_region=3804` و`jobs_sa=2935` مضخّمان فعليًا بما لا يقل عن 17%.**
3. **`family_classified_pct_in_region` لا يزال 35.9%** (الهدف ≥80%) رغم توسّعتين سابقتين للمعجم — وجزء من "غير المصنّف" مقصود فعليًا (عائلة `sales_excluded` مُستبعدة بالكامل من الفحص لا تُوسَم بشيء، فتختلط بصريح "غير القابل للتصنيف").
4. **خلل الأقدمية (R2) أُصلح فعليًا بالكود** (حدود كلمة صريحة `\b...\b` مؤكَّدة بقراءة `field_extractor.py`) لكن أثر متبقٍّ صغير (6/3804 داخل النطاق) يبقى بسبب الرجوع لنص الوصف الكامل حين لا يطابق العنوان شيئًا.

**الحكم: REJECT (يبقى B2 = WIP).** معياران رقميان جوهريان من معايير قبول B2 (`dup_ratio<1%`، `family_classified≥80%`) لا يزالان بعيدين جدًا عن التحقق — وأسوأ من ذلك، الرقمان الآخران المُعلَنان كـ"متحقَّقين بفارق كبير" (`jobs_in_region`، `jobs_sa`) تبيّن أنهما ملوّثان بعيب برمجي جديد لم يُكتشف من قبل. البنية سليمة والامتثال نظيف — لكن هذا لا يكفي لاعتماد B2 "منجزًا".

---

## 1) `/admin/stats` و`/health` — تحقّق حي

```
GET /health → {"status":"ok","service":"masar-core","version":"0.2.0"}
GET /admin/stats → jobs_total=19366, jobs_in_region=3804, jobs_sa=2935,
  sources_active=55, companies_total=104, dup_ratio_24h_in_region=0.428,
  family_classified_pct_in_region=0.3588, last_round: 65 مصدرًا (65 نجح/0 فشل)،
  9680 وظيفة مجلوبة، 0 جديدة (الجولة الأخيرة لم تُضِف شيئًا — استقرار مؤقت)
```
مطابق لادّعاء المنفّذ رقميًا (`sources_active`، `jobs_in_region`، `jobs_sa`، `dup_ratio`، `family_classified` كلها كما ذُكر). المشكلة ليست في دقة نقل الأرقام، بل في **صحة الأرقام نفسها** (انظر §2 و§3).

## 2) معدّل التكرار الحقيقي (المطلوب §2 بالتكليف)

### (a) تصادم `dedup_key` الحرفي
```sql
SELECT COUNT(*) - COUNT(DISTINCT dedup_key) FROM jobs;  -- = 0
```
**0 تصادم على كامل الجدول (19366 صفًا).** القيد `uq_jobs_dedup_key` يعمل صحيح مئة بالمئة — لا إدراج مكرر بنفس المفتاح إطلاقًا. **هذا الجزء من ادّعاء المنفّذ صحيح تمامًا.**

### (b) نفس (شركة مطبَّعة، مسمى مطبَّع، `location` خام مطبَّع) — آخر 24 ساعة داخل النطاق
```
total_in_region_24h                = 3804
groups_company_title_loc_gt1       = 1473   (مجموعات فيها أكثر من صف)
rows_company_title_loc_dup_groups  = 3098   (صفوف داخل هذه المجموعات)
excess = 3098 − 1473 = 1625 → 1625/3804 = 42.7%
```
هذا يطابق تقريبًا `dup_ratio_24h_in_region` المُعلَن (42.8%) — **صيغة `/admin/stats` مؤكَّدة ومُعاد حسابها بنجاح؛ الرقم صادق حسب تعريفه الخاص.**

### (c) نفس (شركة، مسمى) بلا موقع
```
groups_company_title_gt1      = 1434
rows_company_title_dup_groups = 3744  (= 98.4% من كل الصفوف!)
excess = 3744 − 1434 = 2310 → 2310/3804 = 60.7%
```

### الفحص الحاسم: هل هذه إعلانات مختلفة فعليًا أم نفس الإعلان بمواقع مختلفة؟

استُخدِمت **`apply_url`** كهوية الإعلان الحقيقي (الأوثق المتاحة — رابط تقديم واحد = نفس الطلب الوظيفي الفعلي بصرف النظر عمّا يُستخرَج من نص الموقع):

```sql
SELECT COUNT(*) AS urls_with_multi_dedup_keys, SUM(cnt) AS rows_involved FROM (
  SELECT url, COUNT(DISTINCT dedup_key) dkeys, COUNT(*) cnt
  FROM jobs WHERE out_of_region=false AND first_seen_at > now()-interval '24 hours'
    AND url IS NOT NULL AND url<>''
  GROUP BY url HAVING COUNT(DISTINCT dedup_key) > 1
) t;
-- → urls_with_multi_dedup_keys = 1432 , rows_involved = 3528  (92.7% من كل الصفوف)

SELECT COUNT(*) total, COUNT(DISTINCT url) distinct_urls,
       COUNT(*) FILTER (WHERE url IS NULL OR url='') null_urls
FROM jobs WHERE out_of_region=false AND first_seen_at > now()-interval '24 hours';
-- → total=3804, distinct_urls=1485, null_urls=223
```

**8 أمثلة فعلية** (من استعلام مباشر — Eram Talent، أكبر مصدر منفرد):

| id | العنوان | الموقع | `url` |
|---|---|---|---|
| 1591 | IT Asset Management Analyst (Saudi Arabia) | Jeddah | .../j/05EAA33D35 |
| 142157 | نفس العنوان | Jeddah | .../j/05EAA33D35 |
| 142158 | نفس العنوان | Riyadh | .../j/05EAA33D35 |
| 142159 | نفس العنوان | Tabuk | .../j/05EAA33D35 |
| 142160 | نفس العنوان | Madinah | .../j/05EAA33D35 |
| 142161 | نفس العنوان | Al Bahah | .../j/05EAA33D35 |
| 1649 | Maintenance Engineer-Biomedical (Saudi Arabia) | Jeddah | .../j/06954AB530 |
| 142216 | نفس العنوان | **Lusail, قطر** | .../j/06954AB530 |

**الخلاصة الحاسمة:** هذه **ليست إعلانات مختلفة** — نفس `apply_url` (نفس رابط التقديم الفعلي/نفس الطلب) يتكرر 6-7 مرات، مرة لكل مدينة "مرشَّحة" يذكرها المصدر (وأحيانًا مدن خارج السعودية بالكامل كـ"Lusail, قطر" أو "القاهرة" لنفس رابط "(Saudi Arabia)"!). **هذا تكرار حقيقي 100%، وهو بالضبط النمط الذي أُصلح جزئيًا (عدم-حتمية استخراج المدينة عبر الجولات) لكن لم يُصلَح كليًا** — لأن `location_text` نفسه (وليس فقط `city` المستخرَج) غير مستقر *داخل نفس الجلبة الواحدة* لبعض المصادر (القائمة الخام من Workable تُرجع نفس معرّف الوظيفة عدة مرات بمواقع مختلفة ضمن استجابة واحدة).

### الرقم الصادق النهائي
```
صفوف بها url:  3581  (3804 − 223 بلا url)
روابط مميّزة:   1485
زائد فعلي:      3581 − 1485 = 2096
نسبة التكرار الصادقة (على url) = 2096 / 3804 = 55.1%
```
**هذا أعلى من الرقم المُعلَن (42.8%)، وليس مجرد "أثر تشخيصي" كما زعم تقرير المنفّذ.** التنظيف الرجعي (حذف 19510 صف) كان فعليًا صحيحًا ومفيدًا، لكن الإصلاح البرمجي (`d_key = dedup_key(company_name, title, location_text, apply_url)`) **لم يعالج الحالة التي تُعيد فيها الاستجابة الواحدة نفس الوظيفة بمواقع متعددة** — لا يزال يُنتج `dedup_key` مختلفًا لكل موقع.

### التعريف الموصى به لـ`dup_ratio` (بديل لصيغة `/admin/stats` الحالية)

استخدام `apply_url` كهوية أساسية (حين متوفر)، مع الرجوع لـ(شركة+مسمى+موقع) مطبَّعة فقط للصفوف بلا `url` (223 صفًا فقط حاليًا، أغلبها SmartRecruiters):

```sql
WITH scoped AS (
  SELECT id,
         COALESCE(NULLIF(url,''),
           'noURL:' || lower(regexp_replace(trim(company_name),'\s+',' ','g')) || '|' ||
                       lower(regexp_replace(trim(title),'\s+',' ','g')) || '|' ||
                       lower(regexp_replace(trim(COALESCE(location,'')),'\s+',' ','g'))
         ) AS identity_key
  FROM jobs
  WHERE out_of_region = false AND first_seen_at > now() - interval '24 hours'
),
grp AS (SELECT identity_key, COUNT(*) c FROM scoped GROUP BY identity_key)
SELECT
  (SELECT COUNT(*) FROM scoped)              AS total_in_region_24h,
  (SELECT COUNT(*) FROM grp)                 AS distinct_postings,
  (SELECT COALESCE(SUM(c)-COUNT(*),0) FROM grp WHERE c>1) AS excess_duplicate_rows,
  ROUND((SELECT COALESCE(SUM(c)-COUNT(*),0) FROM grp WHERE c>1)::numeric
        / NULLIF((SELECT COUNT(*) FROM scoped),0), 4)     AS dup_ratio_recommended;
```

يُطلَب من `/admin/stats` تبنّي هذه الصيغة بدل `(company,title,city)`، **بعد** إصلاح R10 أدناه (وإلا سيبقى الرقم قريبًا من 55%).

---

## 3) دقة الحقول (عيّنة 50 داخل النطاق — عبر SQL مباشر لأن `/admin/quality-sample` لا يفلتر حسب `out_of_region`)

`/admin/quality-sample?n=50` يُرجع أحدث 50 صفًا **بلا فلترة** — بالفعل 15 صفًا اختُبرت جميعها خارج النطاق (Brex بالولايات المتحدة/كندا/البرازيل رغم `region_filter='gcc'` بمصدرها). **هذه نقطة ضعف بالنقطة نفسها يجب أن تدعم `?in_region=true`.** استُبدلت بعيّنة SQL مباشرة (50 صفًا `out_of_region=false ORDER BY first_seen_at DESC`):

| الحقل | التقييم | ملاحظات |
|---|---|---|
| title | ~50/50 صحيح | منسوخ حرفيًا من المصدر |
| company | 50/50 صحيح | مطابق لاسم المصدر |
| city | معبّأ جزئيًا (~60%) وصحيح حين معبّأ | فارغ لبعض صفوف Elastic/GitLab/Coinbase remote |
| country/region | **غير موثوق — انظر R11** | 17.1% من كامل العيّنة داخل النطاق فعليًا خارج الخليج (Elastic×6 بموقع "Romania" لكن `country_code=OM`) |
| years_min | معبّأ جزئيًا، لم يُرصد خطأ واضح بالعيّنة | |
| seniority | خطأ صريح صغير (انظر R13) | مثال: Elastic "Snr Solutions Architect" → `seniority` فارغ رغم "Snr" (اختصار senior غير مغطّى بقائمة الكلمات المفتاحية) |
| family | خطأ ضمني كبير — 35.9% فقط مصنَّف | معظم صفوف العيّنة `family=NULL` رغم عناوين واضحة (Enterprise Account Executive، Accounting Manager) |

**5 أمثلة أخطاء ملموسة من العيّنة الحيّة:**
1. `Elastic — Principal Software Engineer - Vector Search` — `location="Romania"`, لكن `country_code="OM"` و`out_of_region=false` — **خطأ منطقة (R11)**.
2. `Elastic — Snr Solutions Architect` — `seniority=NULL` رغم "Snr" (اختصار شائع لـsenior لم يُضَف لقائمة الكلمات المفتاحية).
3. `Eram Talent — IT Asset Management Analyst (Saudi Arabia)` × 6 صفوف بنفس `apply_url` بمدن مختلفة (Jeddah/Riyadh/Tabuk/Madinah/Al Bahah/Al Khobar) — **تكرار حقيقي (R10)**.
4. `Coinbase — Senior Manager, Institutional Sales` — `family=NULL` رغم مسمى "Manager" واضح (لا عائلة مبيعات مفعَّلة — `sales_excluded` مُستبعدة كليًا من الفحص، §R12).
5. `Vice President, Internal Audit` (خارج العيّنة أعلاه لكن ضمن آخر 24 ساعة) — `seniority='intern'` رغم "Vice President" الصريح — **R13**.

### تحقّق خلل R2 (intern/internal/international) عبر SQL مباشر

```sql
SELECT COUNT(*) FROM jobs WHERE (title ILIKE '%internal%' OR title ILIKE '%international%') AND seniority='intern';
-- 30 صفًا على كامل الجدول، 6 منها داخل النطاق (0.16% من 3804)
```
قراءة كود `field_extractor.py` تؤكد: **الإصلاح مطبَّق فعليًا** (حدود كلمة صريحة `\bintern\b` عبر `re.compile`، والعنوان يُفحَص أولًا قبل النص الكامل) — لم يعد "intern" يطابق داخل "internal/international" كسلسلة فرعية. لكن الـ30 حالة المتبقية سببها مختلف: حين لا يطابق العنوان أي مستوى، يُفحَص النص الكامل (وصف حتى 5000 حرف) والذي غالبًا يحوي كلمة "internship"/"intern" **كوحدة مستقلة فعلية** بنص توضيحي/قانوني (مثال شائع: "…this is a full-time role, not an internship…") فتُطابَق رغم أن العنوان نفسه "Vice President"/"Senior Manager" واضح تمامًا. أثر صغير الآن (0.16%) لكنه انحدار حقيقي غير مغطّى باختبار (انظر R13).

---

## 4) فلتر المنطقة — هل يعمل؟

```sql
SELECT out_of_region, COUNT(*) FROM jobs GROUP BY 1;  -- true=15562, false=3804, NULL=0
```
لا قيم `NULL` — الفلتر يُطبَّق دومًا. لكن **دقّته محل شك جوهري (R11 أعلاه)**: عيّنة 10 صفوف عشوائية من `out_of_region=false` أظهرت 9 مواقع خليجية حقيقية (Riyadh، Jeddah، Abu Dhabi، Al Khobar، Doha، Dubai) **وصفًا واحدًا مضلِّلًا**: `Hudson Manpower — location="singapore, North East, Singapore" — country_code=SA`. الفحص الموسّع (§2) يؤكد أن هذا ليس استثناءً: **651/3804 (17.1%)** من "داخل النطاق" مواقعها الخام تذكر دولة غير خليجية صراحة.

---

## 5) التصنيف المهني (family) — أعلى 30 مسمى غير مصنّف + مقترح توسعة المعجم

```sql
SELECT title, COUNT(*) FROM jobs WHERE out_of_region=false AND (family IS NULL) GROUP BY title ORDER BY 2 DESC LIMIT 30;
```
(القائمة الكاملة أُخذت من الاستعلام؛ أبرزها: Field Sales Consultant، Project Management Engineer، Sales Executive، Field Operator، Asphalt Formulation Specialist، Scada Technician، Gas Distribution SCADA Specialist، Maximo EAM Functional Specialist، Planning & Coordination Specialist، High Voltage Technician، Enterprise Account Executive، Health/Safety/Environment Specialist (بفواصل)، Control Room Operator - DCS، Project Manager - Railway، Combustion Solutions Engineer، Risk Manager، Machinist، Electro-Mechanical Technician، Communication Engineer، SAP Finance & Controlling Consultant، Drilling Foreman، Mechanical Technician…)

**ملاحظة منهجية مهمة:** عائلة `sales_excluded` بـ`taxonomy_local.yaml` تحمل `excluded: true`، ودالة `_build_family_patterns()` **تتخطّاها بالكامل** (`if spec.get("excluded"): continue`) — أي أن أي وظيفة مبيعات/تطوير أعمال **لا تُوسَم بأي شيء إطلاقًا**، فتُحسَب ضمن "غير مصنَّف" رغم أنها مُستبعدة عمدًا من التصنيف. هذا يُضخِّم نسبة "غير مصنَّف" المُعلَنة بلا تمييز بين "فجوة معجم حقيقية" و"استبعاد متعمَّد" — ويجب فصلهما في المقياس (R12).

### مقتطف YAML جاهز للّصق (توسعة `data/taxonomy_local.yaml` — إضافات فقط، تُدمَج داخل الكلمات الحالية لكل عائلة)

```yaml
families:
  construction_pm:
    keywords_en: [project management engineer, project execution lead, drilling foreman,
      high voltage technician, electrical maintenance technician]

  maintenance_ops:
    keywords_en: [field operator, control room operator, DCS operator, machinist,
      electro-mechanical technician, mechanical technician, SCADA technician,
      SCADA specialist, maximo EAM, EAM functional specialist]

  project_controls:
    keywords_en: ["planning and coordination specialist", "planning & coordination specialist"]

  hse:
    keywords_en: ["health, safety and environment specialist"]

  chem_process:
    keywords_en: [asphalt formulation specialist, combustion engineer, combustion solutions engineer]

  it_software:
    keywords_en: [MDM developer, data governance, informatica, communication engineer]

  finance_accounting:
    keywords_en: [risk manager, SAP FICO consultant, "SAP finance and controlling consultant"]

  lab_chemistry:
    keywords_en: [research technician]

  sales_excluded:   # ← يبقى excluded:true، لكن انظر R12 لتغيير سلوك classify_family نفسه
    keywords_en: [account executive, enterprise account executive, field sales consultant,
      business development specialist]
```
(كل قائمة أعلاه تُدمَج مع الكلمات الموجودة بنفس العائلة بالملف الحالي، لا تستبدلها.)

---

## 6) المصادر — نشطة/saudi_hits/معطّلة

`GET /admin/stats.sources_disabled`: **49 مصدرًا معطّلًا**، كلها بسبب `no_gcc_jobs` (تعطيل تلقائي بعد جولتين بلا وظيفة خليجية — الآلية تعمل كما يُدَّعى) عدا 3 تعطيلات يدوية (`Sanabil Venture Studio`، `Tamara`، `Jobgether`).

**كل الـ55 مصدرًا النشطة لديها `saudi_hits ≥ 1`** (لا يوجد مصدر نشط بصفر وظائف سعودية حاليًا — القائمة الكاملة رُتِّبت تصاعديًا وأدناها 1). لكن الفجوة بين `total_jobs` (كل ما جُلب تاريخيًا من المصدر) و`saudi_hits` (آخر جولة) كبيرة لمصادر كثيرة — مثال: `Cloudflare` 671 وظيفة إجمالًا مقابل `saudi_hits=1` بآخر جولة؛ `OKX` 680 مقابل 9؛ `Intellect` 630 مقابل 13. هذا يؤكد ملاحظة المراجعة السابقة (R4): كثير من المصادر "التوسعة" شركات عالمية تُنتج مئات الوظائف غير الخليجية مقابل وظيفة خليجية واحدة أو اثنتين فعليًا — عبء جلب كبير بلا عائد يُذكر.

**الحكم حسب النتيجة (كما طلب التكليف):** وظائف فريدة جديدة/يوم داخل النطاق = **3804** (كل صفوف `jobs_in_region` عمرها < 24 ساعة حاليًا) — أو **1485** إن استُخدم `apply_url` كهوية حقيقية بعد تصحيح R10. كلا الرقمين **> 200/يوم بفارق كبير**. **بناءً على هذا المعيار، 55 مصدرًا كافٍ حاليًا فعليًا** — لا داعٍ لمزيد من "صيد" مصادر عالمية هامشية (نمط الدفعة الرابعة: 11 مرشّحًا أعطوا مصدرًا ناجيًا واحدًا) حتى يتوفّر جامع لمصادر مغلقة سعودية فعلية (`sitemap_jsonld`/`rss` لمواقع توظيف حكومية أو ATS مغلقة) — هذا مُدرَج كـB2b غير حاجز بـ`PLAN.md`.

---

## 7) الامتثال — 5 مصادر جديدة من `data/sources_seed.csv` (آخر دفعة)

| الشركة | النوع | الرابط |
|---|---|---|
| Coinbase | greenhouse | `boards-api.greenhouse.io/v1/boards/coinbase/jobs` |
| Binance | greenhouse | `boards-api.greenhouse.io/v1/boards/binance/jobs` |
| Reddit | greenhouse | `boards-api.greenhouse.io/v1/boards/reddit/jobs` |
| Affirm | greenhouse | `boards-api.greenhouse.io/v1/boards/affirm/jobs` |
| Brex | greenhouse | `boards-api.greenhouse.io/v1/boards/brex/jobs` |

كلها واجهات `boards-api.greenhouse.io` JSON عامة رسمية بلا مصادقة — نفس نمط بقية المصادر المعتمدة، **لا كشط، متوافقة**. ✅

---

## قائمة التصحيحات (R10 → R15)

**R10 — [حرج] تكرار حقيقي بنسبة ~55% عبر نفس `apply_url` (تعدّد المدن داخل نفس الجلبة).**
الملف: `core/app/discovery.py:run_round()` (سطر `d_key = dedup_key(company_name, title, location_text or None, apply_url)`).
الدليل: 1432 `url` فريد لديه أكثر من `dedup_key` واحد ضمن آخر 24 ساعة (3528/3804 صفًا = 92.7%)؛ صفوف زائدة فعليًا = 2096/3804 = 55.1% (أعلى من الرقم المُعلَن 42.8%).
الإصلاح المقترح: لمصادر ATS التي تضمن معرّف/رابط طلب واحد لكل وظيفة فعلية (`greenhouse`/`lever`/`ashby`/`smartrecruiters`/`workable`)، اجعل `dedup_key` يعتمد `apply_url` وحده حين متوفرًا (بلا `location_text`)، مع تجميع كل المواقع المُكتشفة لنفس `apply_url` بحقل `locations: []` بدل صف منفصل لكل مدينة؛ أبقِ صيغة (شركة+مسمى+موقع) fallback فقط للصفوف بلا `url` (223 صفًا/يوم حاليًا).
معيار القبول: تشغيل استعلام "urls_with_multi_dedup_keys" بعد الإصلاح على جولة جديدة → قريب من صفر.

**R11 — [حرج] تلوّث `jobs_in_region`/`jobs_sa` بوظائف غير خليجية فعليًا (17.1%) بسبب رجوع `extract_country_code()` لنص الوصف بلا شرط.**
الملف: `core/app/collectors/field_extractor.py`، دالة `extract_country_code()`.
الدليل: 651/3804 صفًا `out_of_region=false` موقعها الخام (`location`) يذكر دولة غير خليجية صراحة (سنغافورة، ماليزيا، رومانيا، الهند، فيتنام...)؛ مثال مباشر: Elastic × 6 صفوف بـ`location="Romania"` و`country_code="OM"`.
الإصلاح: لا تلجأ لـ`extra_text` (عنوان+وصف) إلا حين `location_text` فارغ تمامًا؛ إذا كان `location_text` معبّأً ويذكر دولة/مدينة محدَّدة (خليجية أو غيرها)، اعتمد ذلك حصرًا ولا تسمح لذكر عرَضي بالوصف بتجاوزه.
معيار القبول: إعادة استعلام "location يذكر دولة غير خليجية صراحة AND out_of_region=false" بعد الإصلاح → قريب من صفر؛ `jobs_in_region`/`jobs_sa` يُعاد قياسهما ويُتوقَّع انخفاضهما ~15-20%.

**R12 — [عالٍ] `family_classified_pct_in_region` (35.9%) لا يميّز بين "فجوة معجم" و"استبعاد متعمَّد"، والمعجم لا يزال ناقصًا لأعلى المسمّيات تكرارًا.**
الملف: `core/app/discovery.py:classify_family()` + `data/taxonomy_local.yaml`.
الإصلاح: (أ) دمج مقتطف YAML أعلاه بالملف؛ (ب) تعديل `classify_family()` ليُرجع اسم العائلة المُستبعدة (مثل `sales_excluded`) بدل `None` حين تُطابَق، ويُحسَب `family_classified_pct_in_region` كـ classified/(total − excluded) بدل classified/total.
معيار القبول: بعد (أ)+(ب)، إعادة قياس النسبة على وظائف داخل النطاق فقط ≥ 60% كخطوة أولى (الهدف النهائي 80%).

**R13 — [متوسط] بقايا خلل الأقدمية (انحدار R2 جزئي) — 6/3804 داخل النطاق (0.16%).**
الملف: `core/app/collectors/field_extractor.py:extract_seniority()`.
الدليل: عناوين صريحة "Vice President, Internal Audit"، "Senior Manager, Internal Audit"، "International Tax Director" → `seniority='intern'` رغم عدم مطابقة العنوان نفسه لأي نمط (الرجوع للنص الكامل يلتقط كلمة "internship" ضمن نص توضيحي/EEO بالوصف).
الإصلاح: حين لا يطابق العنوان أي مستوى، لا تسمح لمطابقة "intern" وحدها بالنص الكامل بالفوز إن وُجدت أي مطابقة لمستوى أقوى (senior/manager/lead) بنفس النص — أعطِ الأولوية لأقوى إشارة موجودة، لا لأول إشارة بترتيب القائمة. أضف حالة اختبار انحدار جديدة (اقتراح سابق R7 من المراجعة الأولى لم يُنفَّذ بعد).

**R14 — [متوسط] إعادة تعريف `dup_ratio_24h_in_region` بصيغة `apply_url`-محورية (بعد R10).**
انظر الاستعلام الكامل بالقسم 2 أعلاه. الهدف الواقعي المقترح بعد R10: < 5% (وليس <1% غير الواقعي لمصادر وكالات توظيف تُعيد نشر نفس الإعلان بعشرات المدن شرعًا أحيانًا).

**R15 — [منخفض، غير حاجز] `sources_active` (55/60) مقبول حسب النتيجة.**
وظائف فريدة/يوم داخل النطاق (3804 خامًا، أو 1485 بهوية `apply_url` بعد R10) أعلى بكثير من عتبة 200/يوم — لا داعٍ لمزيد من صيد مصادر عالمية هامشية (معدّل نجاح الدفعة الرابعة: 11 مرشّحًا → مصدر ناجٍ واحد). الأولوية: جامع `sitemap_jsonld`/RSS لمصادر سعودية مغلقة فعلية (B2b أدناه)، لا مزيد من شركات Greenhouse عالمية بـ`saudi_hits` أحادي الرقم.

---

## ملخص الأدلة (استعلامات SQL نُفّذت مباشرة عبر `ops("psql", …)` على الإنتاج، قراءة فقط)

- تصادم `dedup_key`: `SELECT COUNT(*)-COUNT(DISTINCT dedup_key) FROM jobs` → 0
- مجموعات (شركة،مسمى،موقع) داخل النطاق/24س: انظر القسم 2(b)
- مجموعات (شركة،مسمى) بلا موقع: انظر القسم 2(c)
- تكرار `apply_url` الحقيقي: انظر القسم 2 (الاستعلام الحاسم) — 1432 رابطًا/3528 صفًا
- توزيع `out_of_region`: `SELECT out_of_region, COUNT(*) FROM jobs GROUP BY 1`
- تلوّث المنطقة: فحص `location ILIKE` لـ15 دولة/مدينة غير خليجية معروفة → 651 صفًا
- خلل seniority: `SELECT title,seniority FROM jobs WHERE (title ILIKE '%internal%' OR '%international%') AND seniority='intern'`
- أعلى 30 عنوان غير مصنَّف: `GROUP BY title` على `family IS NULL AND out_of_region=false`
- المصادر: `JOIN sources s ON companies c ... LEFT JOIN jobs j` مع `saudi_hits`/`disabled_reason`
- الكود: قراءة كاملة لـ`core/app/discovery.py`، `core/app/collectors/field_extractor.py`، `data/taxonomy_local.yaml`
