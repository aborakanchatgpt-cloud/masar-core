# B2-close — تقرير المنفّذ: إغلاق الاكتشاف (R10/R11/R13/R14 + تغطية سعودية بلا كشط)

**التاريخ:** 2026-09-09 | **المنفّذ:** Sonnet subagent (sandbox محلي معزول — استنساخ محلي، بلا SSH، بلا دفع). **لا يُعلّم B2 "DONE" ذاتيًا** — تقرير منفّذ ينتظر مراجعة قبول.

## 0. ملخّص تنفيذي

فحص فعلي لكود `main` المستنسَخ محليًا (commit `c469e4d`) كشف أن **R10 (dedup_key بـapply_url)، R11 (extract_country_code)، وR14 (dup_ratio بهوية apply_url)، كانت جميعها مُصلَحة فعليًا بالكود بالفعل** — قبل هذه الجلسة، ضمن سلسلة commits `57cd1e2`…`ba05688` التي جرت **بعد** حكم REJECT المسجَّل بـ`PLAN.md` (`44fadd8`) لكن **قبل** جلسة B2b التي عالجت R12 فقط ولم تُحدِّث نص `PLAN.md` ليعكس أن R10/R11/R14 صارت جاهزة. أي أن `PLAN.md` كان متأخرًا عن الكود الفعلي. تحقّقتُ من الثلاثة بقراءة الكود + تشغيل 293 اختبارًا محليًا (بما فيها اختبارات R10/R11 التراجعية الموجودة مسبقًا بـ`test_normalizer.py`/`test_discovery_grouping.py`)، ولم ألمس منطقها.

**البند الوحيد المتبقّي فعليًا معطوبًا:** R13 (أولوية "intern" الخاطئة بفرع النص الكامل لـ`extract_seniority()`) — **أُصلح بهذه الجلسة** مع 4 اختبارات تراجعية جديدة.

**البند الثاني المطلوب:** تغطية سعودية مغلقة المصدر بلا كشط عبر `sitemap_jsonld`/`rss` — كان `rss.py` عامًا بالفعل (يعمل مع أي خلاصة RSS/Atom)، لكن `sitemap_jsonld.py` كان يتعامل مع أي رابط كـ"صفحة وظائف مفردة" فقط ولا يفهم `sitemap.xml` حقيقيًا رغم اسمه — **أُضيف دعم زحف sitemap.xml/sitemapindex فعلي** (بحد أقصى مصفوف، احترام robots.txt لكل رابط). أُضيف `scripts/verify_sources.py` وبحث فعلي عن 14 مرشحًا جديدًا عبر WebSearch/WebFetch، تحقّق 4 منهم كمصادر RSS حقيقية عاملة (تفاصيل §2).

## 1. R10/R11/R14 — التحقّق (لا إصلاح جديد، توثيق حالة فعلية)

| البند | الحالة الفعلية بكود `main` المستنسَخ | الدليل |
|---|---|---|
| R10 (dedup_key بـapply_url) | **مُصلَح فعليًا** — `normalizer.dedup_key()` يعتمد `sha1(source_id\|normalize_apply_url(url))` حين متوفر؛ `discovery._group_raw_jobs_by_identity()` يُجمّع raw_jobs بنفس الجولة قبل الإدراج، يُنتج `jobs.locations` (JSONB) بدل صف لكل مدينة | `core/app/collectors/normalizer.py`، `core/app/discovery.py:_group_raw_jobs_by_identity`؛ 8 اختبارات بـ`test_normalizer.py` + 6 بـ`test_discovery_grouping.py` (منها فكسّتشر Eram Talent الحرفي من `B2-review-2.md`) — كلها خضراء |
| R11 (extract_country_code) | **مُصلَح فعليًا** — `location_text` البنيوي وحده يُعتمَد حين متوفرًا (بلا رجوع لـ`extra_text` إطلاقًا)؛ مطابقة أسماء الدول/المدن بحدود كلمة صريحة (يمنع "oman" داخل "Romania") | `core/app/collectors/field_extractor.py:extract_country_code/compute_region`؛ 4 اختبارات مخصّصة (منها حالة Singapore+"نغطي السعودية..." المذكورة حرفيًا بالتكليف الأصلي) |
| R14 (dup_ratio بهوية apply_url) | **مُصلَح ومُعرَّض فعليًا** — `GET /admin/stats.dup_ratio_24h_in_region` يحسب الآن `COALESCE(url_normalized, 'noURL:'+company+title+location)` كهوية، لا (شركة+مسمى+مدينة) الأخشن | `core/app/discovery_api.py:stats()` (التعليق يوثّق R14 صراحة)، ونقطة تشخيص مرافقة `GET /admin/dup-breakdown` بنفس المنطق |

**لماذا لم تظهر هذه الحقيقة بـ`PLAN.md` سابقًا؟** ترتيب الـcommits: `a3461f2`/`44fadd8` (حكم REJECT) → `57cd1e2`…`ba05688` (تنفيذ الإصلاحات الفعلية + تقرير أدلة) → جلسة B2b لاحقة عالجت **R12 فقط** دون إعادة فحص/تحديث حالة R10/R11/R14 بـ`PLAN.md`. هذا التقرير يصحّح التوثيق.

## 2. R13 — الإصلاح الفعلي الوحيد المطلوب هذه الجلسة

**العيب:** `extract_seniority()` — حين لا يطابق العنوان أي مستوى، يُفحَص النص الكامل بترتيب القائمة الثابت (`intern, entry, senior, manager, lead`) ويُعاد **أول** تطابق. مثال حقيقي من `B2-review-2.md`: عنوان "Vice President, Internal Audit" (لا يطابق شيئًا) + وصف يحوي "…not an internship…" (`intern`) و"Head of Finance" (`manager`) → كان يُعاد `intern` رغم إشارة `manager` أقوى، لمجرد ترتيبه الأول بالقائمة.

**الإصلاح:** بفرع النص الكامل فقط (فرع العنوان غير مُتأثِّر): تُجمَع كل المستويات المطابقة؛ إن كان `intern` الوحيد يُعاد كما هو (إعلان تدريب حقيقي)؛ وإلا يُرجَّح أول مستوى **غير** `intern` مطابق (بترتيب القائمة الأصلي) — لا يفوز `intern` أبدًا حين يتعايش مع أي إشارة أخرى.

```python
matched = {level for level, pattern in _SENIORITY_PATTERNS if pattern.search(text)}
if not matched:
    return None
if matched == {"intern"}:
    return "intern"
for level, _pattern in _SENIORITY_PATTERNS:
    if level != "intern" and level in matched:
        return level
return "intern"
```

**الاختبارات (4 جديدة، `core/tests/test_normalizer.py`):** الحالة المُبلَّغة حرفيًا (VP+manager)، حالة senior مشابهة، حالة "intern فقط لا إشارة أخرى" (يبقى `intern` — لا انحدار عكسي)، وحالة تأكيد أن فرع العنوان غير متأثّر ("International Tax Director" يطابق "director" مباشرة بالعنوان).

## 3. تغطية سعودية مغلقة المصدر بلا كشط (البند 2)

### 3.1 `core/app/collectors/sitemap_jsonld.py` — الامتداد الفعلي

كان `fetch_jobs(url)` يعامل أي رابط كصفحة وظائف مفردة فقط — لا يفهم `sitemap.xml` حقيقيًا رغم اسم الوحدة. **أُضيف:** كشف تلقائي بالمحتوى الفعلي (جذر `<urlset>`/`<sitemapindex>`، لا امتداد الرابط)؛ عند اكتشاف sitemap حقيقي: انتقاء روابط الوظائف (فرز بكلمات مفتاحية بالمسار: job/career/vacan/position/hiring/recruit/وظيف/توظيف، رجوع لإرجاع الكل إن لم يُطابق شيء)، حد أقصى `MAX_JOB_PAGES=40`؛ `sitemapindex` مدعوم بعمق واحد (`MAX_SUBSITEMAPS=5`). **robots.txt يُتحقَّق لكل رابط** (parser واحد لكل نطاق) — رابط ممنوع يُتخطّى بصمت، ورابط sitemap نفسه الممنوع يرفع `ValueError` كالسابق. **توافق رجعي كامل:** صفحة HTML مفردة (كل مصادر `sitemap_jsonld` الحالية) بلا أي تغيير — 7 اختبارات جديدة تغطي: توافق رجعي، sitemap حقيقي، `sitemapindex`، حجب robots.txt، كشف المحتوى، الحد الأقصى.

### 3.2 `core/app/collectors/rss.py` — تحقّق + تحسين طفيف

الوحدة كانت **عامة بالفعل** (أي خلاصة RSS/Atom) — تحقّقتُ فعليًا (§3.3) أنها تعمل بلا تعديل ضد خلاصات Teamtailor حيّة حقيقية. أُضيف تحسين بسيط: استخراج وسم `<location>`/مرادفاته إن وُجد بعنصر `<item>` بدل الاعتماد فقط على النص الحر لاحقًا — 5 اختبارات جديدة (الوحدة كانت **بلا أي اختبار من قبل**).

### 3.3 `scripts/verify_sources.py` (جديد)

يقرأ CSV بصيغة `sources_seed.csv`، ولكل صف: **يتحقّق من robots.txt أولًا وحصرًا** — رابط ممنوع لا يُجلَب محتواه إطلاقًا، ثم GET أولي + `discovery.validate_source()` (نفس منطق `/admin/discovery/probe`) لعدّ الوظائف، ويطبع جدولًا + سطر JSON نهائي. 5 اختبارات وحدة تغطي القراءة/الحجب/النجاح/الفشل بمحاكاة كاملة.

**⚠️ قيد بيئي لهذه الجلسة تحديدًا:** sandbox هذه الجلسة خلف بروكسي صادر يمنع أي HTTP مباشر (`curl`/`httpx`) لأي نطاق خارج قائمة سماح ضيقة (npm/pypi/anthropic فقط) — مؤكَّد فعليًا: `curl https://example.com` وأيضًا `boards-api.greenhouse.io` يفشلان بـ`403 CONNECT tunnel failed` من نفس البروكسي. هذا لا يعطّل السكربت نفسه (مُختبَر وحدويًا بالكامل أعلاه، يعمل بلا تعديل بأي بيئة نشر حقيقية — بما فيها Core الحيّة ذات وصول إنترنت كامل). **البديل للتحقّق الحي الفعلي من المصادر الجديدة:** أداة `WebFetch` (مسار شبكي منفصل غير خاضع لبروكسي الصندوق الرملي) — نفس مبدأ التحقّق (جلب فعلي + قراءة محتوى)، موثَّق أدناه.

### 3.4 البحث والتحقّق الفعلي — 14 مرشحًا، 4 مُضافون

بحثت (WebSearch) عن شركات/جهات سعودية/خليجية تنشر `sitemap.xml`/JSON-LD أو RSS رسميًا، ثم تحقّقت (WebFetch) من كل مرشّح فعليًا:

**فشلوا/غير قابلين (10):** NEOM (شهادة SSL منتهية)، flynas (لا JSON-LD/sitemap)، ROSHN (تسويقية بلا وظائف مُدرَجة)، Jarir (404)، Almarai (يُحوِّل لـSuccessFactors خارجي)، Elm (خطأ شهادة)، Tawuniya (404)، stc (SPA بلا محتوى ثابت)، Zamil Industrial (404)، RCJY الهيئة الملكية للجبيل وينبع (`sitemap.xml` حقيقي لكن يشير لصفحات بحث ديناميكية فقط، لا صفحات وظائف فردية — JS بالكامل).

**نجحوا (4، كلهم RSS رسمي عام من Teamtailor — منصّة موثّقة تدعم `{company}.teamtailor.com/jobs.rss` رسميًا):**

| الشركة | الرابط | تحقّق WebFetch | robots.txt |
|---|---|---|---|
| Samir Group | `samirgroup.teamtailor.com/jobs.rss` | تغذية RSS صالحة، **2 وظيفة، كلتاهما سعودية فعليًا** (Mechanical Sales Engineer — Jeddah وAl Khobar) — شركة سعودية حقيقية (NDT/موثوقية صناعية، جدة، +70 عامًا) | تحقّق مباشر: لا يمنع `/jobs.rss` لعامل المستخدم العام |
| Chalhoub Group | `chalhoubgroup.teamtailor.com/jobs.rss` | 10 وظائف، منها وظائف جدة فعلية (Dyson Red Sea Mall، برادا) | لم يُختبَر صراحة (نفس منصّة Teamtailor، نمط robots.txt مطابق لـSamir Group) |
| Supersub | `supersub.teamtailor.com/jobs.rss` | 16 وظيفة، **4 سعودية فعليًا** (الرياض/جدة/الدمام) | كالسابق |
| Qureos Inc | `qureosinc.teamtailor.com/jobs.rss` | 25 وظيفة، 2 سعودية فعليًا (Control Engineer × Riyadh) | كالسابق |

أُضيف الأربعة لـ`data/sources_seed.csv` (type=`rss`) بـ`terms_note` يوثّق الدليل الحرفي أعلاه. **لا كشط** — RSS رسمي موثَّق من Teamtailor نفسها، بلا مصادقة، بلا تجاوز أي قيد وصول.

## 4. بوابات الجودة (محليًا)

- `python -m compileall core/app core/tests scripts` — نظيف.
- `python -c "import app.main"` و`python -c "import app.scheduler_main"` — نظيفان.
- `pytest -q`: **272 → 293** (+21: 4 R13، 7 `test_sitemap_jsonld.py`، 5 `test_rss_collector.py`، 5 `test_verify_sources.py`)، **0 فشل**.
- `alembic upgrade head` → `alembic downgrade base` → `alembic upgrade head`: نظيف بالكامل على قاعدة فارغة (0001→0011 ثم عكسًا ثم للأمام) — **لا ترحيل جديد بهذه الجلسة**: لا R13 ولا امتداد sitemap_jsonld/rss ولا المصادر الجديدة تحتاج أي تعديل مخطّط (كلها منطق/بيانات بحتة). تحقّق: `migrations/versions/` لا يحوي `0012_b7_catalog` (الأعلى فعليًا `0011_b6_load`) — لا حاجة لترحيل `0013` أصلًا بما أن لا تعديل مخطّط مطلوب.

## 5. الملفات المُغيَّرة/الجديدة

`core/app/mail_api.py`، `core/app/matching.py` (تصحيح سطري تعليق مكسورين، مطلوب مسبقًا للتشغيل) — `core/app/collectors/field_extractor.py` (R13) — `core/app/collectors/sitemap_jsonld.py` (امتداد sitemap.xml) — `core/app/collectors/rss.py` (تحسين موقع اختياري) — `core/tests/test_normalizer.py` (+4 R13) — `core/tests/test_sitemap_jsonld.py` (جديد) — `core/tests/test_rss_collector.py` (جديد) — `core/tests/test_verify_sources.py` (جديد) — `scripts/verify_sources.py` (جديد) — `data/sources_seed.csv` (+4 صفوف) — `PLAN.md` (تحديث + تقسيم) — `docs/PLAN_LOG.md` (جديد، السجل التاريخي).

## 6. المتبقّي خارج النطاق

دقّة كل عائلة تصنيف على حدة فرديًا (متروك من B2b، لا يزال). مصادر RCJY/الحكومية الديناميكية (JS) — بلا حل بلا كشط. تنظيف تكرار حرفي بصفوف Decima/Checkout.com/BioCatch بـ`sources_seed.csv` (ملاحظة B2b سابقة، غير عاجل).
