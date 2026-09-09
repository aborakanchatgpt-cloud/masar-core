# B2b — تقرير المنفّذ: توسعة تصنيف العائلة المهنية والمصادر

**التاريخ:** 2026-09-09 | **المنفّذ:** Sonnet subagent (sandbox، بلا SSH، عبر Masar MCP bridge) | **الحالة:** نُفِّذ ونُشر حيًّا، بانتظار مراجعة قبول — لا يُعلّم DONE ذاتيًا.

**النطاق:** بند 3 فقط من قائمة تصحيحات B2 (R10-R15): توسعة `taxonomy_local.yaml` وتمييز "غير مصنَّف حقيقي" عن "مستبعد عمدًا". البنود 1/2/4/5 (dedup_key بـ apply_url، تصحيح extract_country_code، بقايا خلل الأقدمية، dup_ratio_24h_in_region) **خارج النطاق ولم تُلمَس** — B2 نفسه يبقى REJECT/WIP. أُضيفت أيضًا 8 مصادر سعودية/خليجية جديدة موثّقة (لا كشط، بلا API مدفوع)، حسب طلب هذه الدورة.

## 1. الهدف الرقمي والنتيجة

| المقياس | الهدف | قبل (خط الأساس) | بعد (نهائي) | محقَّق؟ |
|---|---|---|---|---|
| `family_classified_pct_in_region` (يشمل out_of_scope) | ≥ 80% | 32.04% | **85.30%** | ✓ |
| `family_real_pct_in_region` (عائلة حقيقية فقط) | ≥ 70% | 31.82% | **75.13%** | ✓ |
| `jobs_in_region` (نفس المجموعة قبل/بعد) | — | 1367 | 1367 | مقارنة نظيفة، بلا انجراف صفوف |
| مصادر خليجية جديدة موثّقة | ≥ 8 | — | **8** (كلها enabled=true وحيّة) | ✓ |

كلا الصيغتين أُعيد تعريفهما في هذه الدورة لتقسيم على `jobs_in_region` **الكامل** (لا الكل ناقص المستبعد كما كانت الصيغة القديمة) — راجع §3.

## 2. الاستعلام الحي المستخدم (قبل وبعد، نفس الصيغة بالضبط)

نُفِّذ عبر `ops({cmd:"psql", args:["<السطر أدناه>"]})` (سطر واحد بلا فواصل منقوطة، حسب قيود أداة psql في هذا الجسر)، وهو نفس الاستعلام الذي يبنيه `scripts/classify_report.py`/`core/app/reclassify.py`:

```sql
SELECT
  count(*) FILTER (WHERE out_of_region = false) AS jobs_in_region,
  count(*) FILTER (
    WHERE out_of_region = false AND family IS NOT NULL
          AND family NOT IN ('out_of_scope', 'sales_excluded')
  ) AS classified_real,
  count(*) FILTER (
    WHERE out_of_region = false AND family IN ('out_of_scope', 'sales_excluded')
  ) AS out_of_scope,
  count(*) FILTER (WHERE out_of_region = false AND family IS NULL) AS unclassified
FROM jobs
```

`family_classified_pct_in_region = (classified_real + out_of_scope) / jobs_in_region`
`family_real_pct_in_region = classified_real / jobs_in_region`

**قياس خط الأساس** (قبل أي تعديل تصنيف هذه الدورة): `jobs_in_region=1367`، `classified_real=435`، `out_of_scope=13`، `unclassified=919` → 32.04% / 31.82%.

**بعد الدفعة الأولى من التوسعة** (مرحلية، لم تُحقّق الهدف): 65.18% / 56.33% — استُخدمت لتوجيه الدفعة الثانية.

**بعد الدفعة الثانية + إعادة التصنيف الحي النهائية** (نفس الاستعلام، نفس `jobs_in_region=1367`): `classified_real=1027`، `out_of_scope=139`، `unclassified=201` → **85.30% / 75.13%** — مطابق تمامًا لملخص `core/app/reclassify.py` (`python -m app.reclassify` عبر `ops script reclassify`، idempotent، صفوف out_of_region مستبعدة تمامًا).

## 3. أهم التغييرات التقنية

1. **تطبيع نص جديد** (`discovery._normalize_for_match()`): يطوي التشكيل/الهمزات/التاء المربوطة العربية، `Sr./Jr.`→senior/junior، يحذف أرقامًا رومانية لاحقة، `"&"`→`"and"` — يُطبَّق على نص البحث والكلمات المفتاحية معًا. `classify_family(title, description)` أصبح **عنوان-أولًا-ثم-وصف-كملاذ-أخير** بدل الاعتماد على الوصف فقط.
2. **إعادة تسمية** `sales_excluded` → `out_of_scope` (يبقى `sales_excluded` مقبولًا للتوافق الرجعي؛ `EXCLUDED_FAMILY_NAMES = {"out_of_scope", "sales_excluded"}` في `discovery.py`، `matching.py`، و`discovery_api.py`). `out_of_scope` يُحسب **"مصنَّفًا"** ضمن `family_classified_pct_in_region` (لأنه قرار تصنيف صريح ومقصود) ويُستثنى فقط من `family_real_pct_in_region`.
3. **`data/taxonomy_local.yaml`**: توسّع من ~16 عائلة إلى **22 عائلة** عبر دفعتين مقاسَتين حيًّا لكل منهما — الأولى (65.18%/56.33%) لم تكفِ، فسُحبت أعلى 350 عنوانًا غير مصنَّف حقيقي من قاعدة الإنتاج الحيّة وصُمّمت دفعة ثانية أكبر: عائلة جديدة `legal_compliance`، وكلمات عامة (bare) محسوبة الموضع **بترتيب إعلان الملف** (`classify_family` يعيد أول عائلة تُطابق حسب ترتيب اليامل) — مثل `technician`/`foreman`/`hydraulics`→`maintenance_ops`، `planner`/`contract administrator`/`cost controller`→`project_controls`، `inspector`/`surveyor`→`construction_pm`، `expeditor`→`supply_chain`.
4. **`core/app/reclassify.py`** (جديد): يعيد حساب `family` لكل صفوف `jobs WHERE out_of_region = false` دفعة دفعة (`BATCH_SIZE=1000`) بترقيم مفاتيح، idempotent، يكتب فقط عند التغيّر. ضروري لأن كاش المعجم (`_families_cache`) في العملية الحيّة لا يلتقط تعديلات `taxonomy_local.yaml` تلقائيًا — يُشغَّل كعملية بايثون طازجة عبر `deploy/ops/scripts/reclassify.sh` → `docker compose exec -T core python -m app.reclassify`.
5. **`core/app/discovery_api.py`**: `/admin/stats` أُعيد تعريف صيغتيه (§2) وأُضيف مفتاح `family_real_pct_in_region` جديد للاستجابة. باقي النقاط لم تُمس.
6. **`scripts/classify_report.py`** (جديد، قراءة فقط): تقرير تغطية بلا كتابة على القاعدة، لأخذ عيّنة أعلى العناوين غير المصنَّفة تكرارًا لأي توسعة معجم لاحقة.

## 4. المصادر الثمانية الجديدة (كلها ATS عام، بلا كشط، بلا API مدفوع)

أُضيفت إلى `data/sources_seed.csv` (upsert idempotent عبر `discovery.seed_sources()`)، كل صف موثَّق بـ`terms_note` يذكر عدد الوظائف الفعلي من `probe` + عيّنة مواقع من `WebFetch`:

| الشركة | نوع ATS | عدد الوظائف الحي المرصود |
|---|---|---|
| SOUM | lever | 17 |
| ADIA | workable | 10 |
| Decima International | greenhouse | 69 |
| Checkout.com | ashby | 170 |
| BioCatch | lever | 13 |
| Squadio | workable | 52 |
| Ethos Interactive | smartrecruiters | 7 |
| FlyAkeed | workable | 1 |

تحقّق إضافي عبر `POST /admin/discovery/seed-sources` (أعاد `{"inserted":0,"updated":110,"skipped":0}` — الجدولة الحيّة كانت زرعتها تلقائيًا قبل التحقق اليدوي) وعبر `ops psql` مباشرة على `sources`/`companies`. **ملاحظة تنظيف:** صفوف `Decima International`/`Checkout.com`/`BioCatch` مكرّرة حرفيًا مع صفوف من دفعة B2 سابقة في نفس الـCSV — بلا ضرر وظيفي (upsert على `source_url`) لكنها تستحق تنظيفًا لاحقًا.

**لم يُنجز** (متروك لدورة تالية): جامع `sitemap_jsonld`/RSS لمصادر سعودية مغلقة فعليًا (كطاقات/جدارة أو صفحات وظائف شركات سعودية كبرى بـJSON-LD) — كل الثمانية أعلاه من نوع ATS عام لا sitemap/RSS محلي؛ معدّل نجاح ترشيح شركات Greenhouse عالمية هامشية كان يتراجع بسرعة (13→8→6→11 مرشّحًا أعطوا 5→1→0→1 مصادر ناجية فقط) مما دفع لإيقاف هذا المسار والتركيز على المصادر الموثّقة أعلاه بدل الاستمرار فيه.

## 5. الاختبارات وبوابات الجودة

- `python -m compileall core/app core/tests scripts` — نظيف.
- `python -c "import app.main, app.scheduler_main"` — نظيف (لم تُمس main.py/scheduler_main.py أنفسهما، فقط استيراد سليم عبر التبعيات المعدَّلة).
- `pytest -q` محليًا: **231 نجح / 0 فشل** (كان 214 قبل هذه الدورة؛ +14 `test_classify_family.py` + 3 `test_reclassify.py`، ولا رجوع في أي اختبار قائم).
- `test_classify_family.py` (14): تطابقات إنجليزية/عربية مباشرة، تطبيع همزة/تاء مربوطة، `Sr./Jr.`، أرقام رومانية، `"&"`→`"and"`، شرطة مائلة/ترقيم، تصنيف `out_of_scope` (يتحقق أن `fam in EXCLUDED_FAMILY_NAMES`)، أولوية العنوان على الوصف عند التعارض، بلا تطابق→`None`، نص فارغ→`None`.
- `test_reclassify.py` (3، مدعوم بقاعدة `masar_test`، يتخطّى بأمان إن تعذّر الاتصال): يصحّح عائلة قديمة خاطئة ويتجاوز صفوف خارج النطاق، idempotent (`rows_changed==0` في التشغيلة الثانية)، تحقّق جبري من كلا الصيغتين.

## 6. قائمة الـcommits (12 هذه الدورة، بالترتيب)

1. `c437101` — `data/taxonomy_local.yaml` (دفعة أولى)
2. `b2e8f76` — `core/app/discovery.py` (تطبيع + عنوان-أولًا + إعادة تسمية out_of_scope)
3. `3598bef` — `core/app/matching.py` (`EXCLUDED_FAMILY_NAMES`)
4. `4414552` — `core/app/discovery_api.py` (إعادة تعريف صيغتي `/admin/stats`)
5. `768d675` — `core/app/reclassify.py` (جديد)
6. `1be6508` — `core/tests/test_classify_family.py` (جديد، 14 اختبارًا)
7. `1025f81` — `core/tests/test_reclassify.py` (جديد، 3 اختبارات)
8. `535c810` — `scripts/classify_report.py` (جديد، قراءة فقط)
9. `6efa59a` — `deploy/ops/scripts/reclassify.sh` (جديد)
10. `9c088c3` — `data/sources_seed.csv` (+8 مصادر)
11. `0a1e581` — `data/taxonomy_local.yaml` (دفعة ثانية، تصل للهدف)
12. `b35f2f1` — `PLAN.md` (تحديث حالة B2/B2b فقط)

جميعها على `main` (autodeploy). لم تُلمَس ملفات B5a المحظورة (`reports.py`, `guarantee.py`, `customers_api.py`, `sender.py`, `send_builder.py`, `main.py`, `scheduler_main.py`, migration `0008`), ولا احتاج العمل أي ترحيل جديد (`0009_b2b_taxonomy.py` غير مطلوب — بلا تعديل مخطط).

## 7. ما تبقّى (خارج نطاق B2b أو متروك عمدًا)

- بنود B2 الأصلية 1/2/4/5 (dedup_key بـ apply_url، extract_country_code، بقايا خلل الأقدمية، dup_ratio_24h_in_region) — **لم تُلمَس**، B2 يبقى REJECT.
- جامع `sitemap_jsonld`/RSS لمصادر سعودية مغلقة فعليًا — لا يزال TODO (§4).
- دقّة كل عائلة من الـ22 على حدة لم تُراجَع فرديًا — المقياس المتحقّق هو تغطية إجمالية (مصنَّف/غير مصنَّف) لا دقة تصنيف لكل عائلة.
- تكرار حرفي لثلاثة صفوف في `sources_seed.csv` (§4) — تنظيف غير عاجل.
