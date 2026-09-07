# B2 — تنفيذ إصلاحات المراجعة الثانية (R10–R15)

مرجع: `docs/reports/B2-review-2.md` (REJECT). هذا التقرير يوثّق التنفيذ والنتائج الفعلية بعد التطبيق على الإنتاج.

## الالتزامات (R10–R14) — منفّذة

- **R10 (dedup بمعرّف apply_url + locations مصفوفة)**: `normalize_apply_url()` (مخطط+مضيف+مسار فقط، بلا استعلام/جزء) و`dedup_key()` الجديد يعتمد `sha1(source_id|url_مطبّع)` حين متوفر apply_url، وإلا `sha1(company|title|location)`. تجميع `_group_raw_jobs_by_identity()` يجمع raw_jobs بنفس الهوية *قبل* الإدراج (صف واحد لكل إعلان فعلي، مع `locations` كقائمة فريدة مرتّبة بالظهور). تنظيف رجعي عبر `POST /admin/discovery/merge-locations-cleanup` نُفّذ على الإنتاج:
  - `groups_merged=6149`, `rows_deleted=20376`, `dedup_keys_recomputed=10844`.
- **R11 (المنطقة من الحقول المهيكلة أولاً)**: `compute_region`/`extract_country_code` يعتمدان `location_text` أولاً، ولا يلجآان لـ`extra_text` إلا حين `location_text` فارغًا تمامًا. **خطأ إضافي مكتشَف أثناء الاختبار** (خارج قائمة R10-R15 الأصلية): المطابقة القديمة كانت تعتمد سلسلة فرعية (`name in lowered`) — و"Romania" تحتوي حرفيًا "oman" كسلسلة فرعية (r-**oman**-ia)، فكانت تُصنّف خطأًا كسعودية/خليجية. أُصلح بتعابير نمطية بحدود كلمة (`\bOman\b`). التنفيذ الرجعي عبر `POST /admin/discovery/recompute-region`:
  - `total_rows=10844`, `changed_to_out_of_region=235`, `changed_to_in_region=0`.
- **R12 (تصنيف الفئات المستبعدة)**: `EXCLUDED_FAMILY_NAMES` والفئات (`sales_excluded`) تُصنّف فعليًا بدل تجاهلها، ومقام `family_classified_pct_in_region` أصبح `classified / (total - excluded)`.
- **R14 (dup_ratio مُعاد تعريفه)**: `/admin/stats` و`/admin/dup-breakdown` يستخدمان استعلام المراجع بالضبط (هوية = apply_url مطبّع أو `noURL:company|title|location`).

## النتائج الفعلية على الإنتاج (بعد كل الإصلاحات)

من `GET /admin/stats`:
- `jobs_total=10844`, `jobs_in_region=1292`, `jobs_sa=964`
- `dup_ratio_24h_in_region=0.0` (نظيف بعد التنظيف الرجعي؛ الرقم القديم ~55% كان ناتجًا عن صفوف Workable متعددة المدن لنفس apply_url)
- `family_classified_pct_in_region=0.3266` (32.66%) — **دون الهدف 80%**

## الفجوة: نسبة التصنيف دون الهدف

رغم إضافة مقتطف اليامل من المراجعة (construction_pm، maintenance_ops، project_controls، hse، chem_process، it_software، finance_accounting، lab_chemistry، sales_excluded) وإعادة التصنيف، النسبة 32.66% فقط — لأن غالبية العناوين غير المصنّفة تقع خارج نطاق الفئات الحالية كليًّا (مبيعات عامة، إدارة عامة، إدارية) وليست مجرد كلمات مفتاحية ناقصة داخل فئة موجودة. رفع النسبة إلى 80% يتطلّب فئات تصنيف إضافية جديدة كليًا (مبيعات، إدارة/تشغيل عام، خدمة عملاء) — قرار نطاق يتجاوز مقتطف المراجعة المحدّد، فتُرك للجولة القادمة.

### أعلى 30 عنوانًا غير مصنّف (من `/admin/unclassified-sample?limit=30`)

| العنوان | التكرار |
|---|---|
| Field Sales Consultant - Classifieds | 5 |
| Sales Executive | 4 |
| Risk Manager | 3 |
| Senior Combustion Solutions Engineer - (End User Combustion Consultant) | 3 |
| Senior Sales Executive | 3 |
| Administrative Assistant | 2 |
| Advertising Sales Executive | 2 |
| Business Development Manager | 2 |
| Carpenter | 2 |
| Data Management, Specialist | 2 |
| Enterprise Account Executive | 2 |
| Field Sales (Business Development & Account Executive) | 2 |
| Field Service & Commissioning Engineer | 2 |
| IT Support Engineer-Part Time | 2 |
| Industrial Hygiene Technician (Saudi Arabia) | 2 |
| Landscape Inspector | 2 |
| MEP Inspector | 2 |
| Mechanical Technician | 2 |
| Operation Manager | 2 |
| PDR Repair Technician | 2 |
| Product Manager of AI Applications, Global Public Sector | 2 |
| Project Director | 2 |
| Project Manager | 2 |
| Sales Account Manager | 2 |
| Sales Consultant - Real Estate (استشاري مبيعات عقاريه) | 2 |
| Sales Engineer - Overhead Cranes | 2 |
| Sales Manager | 2 |
| Scaffolder Inspector - (100560) | 2 |
| Senior Project Manager | 2 |
| Sr. Maintenance Technician, General Assembly | 2 |

**ملاحظة**: نمط واضح — نحو ثلث العناوين غير المصنّفة "مبيعات" (Sales/Account/BD)، وفئة `sales_excluded` مصمّمة للاستبعاد لا التصنيف، فهذه العناوين تبقى `غير مصنّف` عمدًا حتى تُقرّ فئة "مبيعات" منفصلة إن أُريد تتبّعها.

## الاختبارات

- `core/tests/test_normalizer.py` — تمديد بـ9 اختبارات جديدة (R10: `normalize_apply_url`، تجميع apply_url بمعزل عن الموقع/الشركة/المسمى، نطاق source_id؛ R11: الحالة المطلوبة صراحة "Singapore + ذكر Saudi بالوصف" → `out_of_region=True`، والحالة الإضافية "Romania + ذكر Saudi" لاختبار إصلاح السلسلة الفرعية).
- `core/tests/test_discovery_grouping.py` (جديد) — 6 اختبارات لـ`_group_raw_jobs_by_identity`، منها اختبار تثبيت Workable متعدد المدن (6 مدن → مجموعة واحدة، من ضمنها Doha خارج السعودية).
- كل الاختبارات نُفّذت محليًا (`pytest`) قبل كل دفعة — 53/53 ناجحة.

## الالتزامات بالعملية

- لم يُعدّل `core/app/main.py` (مملوك لمنفّذ B3).
- لم تُنشأ ترحيلة 0005 (لأن `0004_b3_customers` لم توجد بعد وقت التنفيذ) — عمود `jobs.locations` أُضيف بـDDL آمن للتكرار (`_ensure_schema()`).
- compileall + استيراد محليان + فحص التوجيه عبر TestClient قبل كل دفعة؛ `/health` و`ops logs core 50` بعد الدفع النهائي أكّدا إعادة تشغيل حقيقية ("Started server process [1]").
