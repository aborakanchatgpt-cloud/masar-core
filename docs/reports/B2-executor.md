# B2 — الاكتشاف الحقيقي: تقرير التنفيذ

تنفيذ آلي (Executor) لبند الأعمال B2 من دليل مسار (§9 المرحلة 2): جمع وظائف
حقيقية من مصادر رسمية آلية القراءة فقط (JSON API لأنظمة ATS معروفة + JSON-LD
+ RSS رسمية)، بلا كشط ولا تجاوز robots.txt/حدود المعدّل/تسجيل دخول/CAPTCHA.

## ما تم بناؤه

**أ) ربط الجدولة**: `core/app/discovery.py` (منطق الجولة: جلب → تطبيع →
استخراج حقول → dedup → إدراج، مع تعطيل تلقائي بعد 3 أخطاء متتالية) و
`core/app/scheduler_main.py` (APScheduler، جولة دورية + بذر فوري عند الإقلاع).

**ب) واجهة إدارة** `core/app/discovery_api.py` — نقاط `/admin/*`:
`POST /sources` (إضافة+تحقق فوري)، `GET /sources`، `POST /sources/{id}/disable|enable`،
`POST /sources/{id}/purge-jobs` (حذف وظائف مصدر ملوّث — أُضيفت هذه الجلسة)،
`GET /stats`، `GET /dup-breakdown` (تشخيص التكرار حسب المصدر — أُضيفت هذه الجلسة)،
`POST /discovery/run-now`، `POST /discovery/seed-sources`،
`GET /discovery/probe` (تحقق بلا كتابة، استُخدم لبناء قائمة المصادر)،
`GET /quality-sample`.

**ج) `data/sources_seed.csv`**: **64 مصدرًا** محقَّقًا فعليًا (Greenhouse،
Lever، Ashby، SmartRecruiters، Workable، Recruitee) — كل صف موثّق بدليل
تحقّق حقيقي (fetch فعلي أعاد وظائف، وغالبًا ذُكرت وظيفة سعودية/خليجية صريحة
وُجدت بالبحث). `data/sources_no_api.md` يوثّق شركات كبرى بلا API آلي (Aramco،
SABIC، NEOM، ACWA Power، Almarai...) — لا يُبنى لها جامع، حسب القيد الصارم.

**د) الجودة**: `GET /admin/quality-sample` + `core/tests/test_normalizer.py`
(اختبارات وحدة لمنطق dedup_key/company_key بالمُطبِّع — 30 حالة، آخر تحقق
مطابق للمحتوى الحالي لعدم تعديل `normalizer.py` هذه الجلسة).

**هـ) هذا الملف** كدليل عربي نهائي.

## الحالة بعد التحقق الفعلي على الخادم المنشور

- **المصادر**: 64 نشطة، 3 مُعطَّلة يدويًا (بأسباب موثّقة):
  - Sanabil Venture Studio (id 4) — slug خاطئ، 404 دائم؛ أُزيل من CSV، وُثِّق بـ`sources_no_api.md`.
  - Tamara (id 9) — رابط قديم بمضيف EU لا يُحلَّل DNS؛ صُحّح لمضيف Greenhouse الموحّد وأُعيد بمعرّف صف جديد (id 72، نشط).
  - Jobgether (id 124) — وكالة إعادة نشر عالمية (ليست شركة توظّف مباشرة) تكرّر نفس المسمى بمدن/دول عديدة بلا مدينة مستخرجة، ما ضخّم مقياس التكرار زورًا؛ عُطّلت، أُزيلت من CSV، وحُذفت وظائفها (4554 صفًا) عبر `purge-jobs`.
- **الوظائف**: **6568** إجمالي (فريدة بضمان قيد فريد على `dedup_key` بقاعدة البيانات — لا تكرار إدراج فعلي ممكن هيكليًا).
- **جولات التشغيل**: عدّة جولات كاملة (64/64 مصدرًا نجح، 0 خطأ) تمّت هذه الجلسة، آخرها بمدة ~36 ثانية.
- **dup_ratio_24h (مقياس تقريبي إضافي)**: بدأ 30.58% (بسبب Jobgether)، ثم 11.22% بعد إصلاح خطأ حقيقي بالاستعلام (نسيان تضمين المدينة بمفتاح التجميع)، ثم **4.69%** بعد إصلاح ثانٍ (استخدام `location` الخام بديلًا لا فراغًا حين لا تُستخرج مدينة خليجية معروفة). التحقق اليدوي (عبر `dup-breakdown` ثم مقارنة الروابط) أثبت أن أغلب المتبقي (Fuku، MongoDB، Pure Storage...) وظائف حقيقية مختلفة (روابط/معرّفات مختلفة فعليًا) لشركات عالمية تنشر نفس المسمى بمكاتب متعددة لا يتعرّف `field_extractor.extract_cities()` على مدنها (لأنه مخصّص لمدن سعودية/خليجية فقط) — **وليس تسرّب تكرار فعلي**. التكرار الحقيقي (بمعنى إدراج نفس الإعلان مرتين) = 0% هيكليًا بقيد `uq_jobs_dedup_key`.

## عينة وظائف سعودية/خليجية فعلية (من `/admin/quality-sample` وقاعدة البيانات)

| الشركة | المسمى | المدينة | سنوات الخبرة | المستوى |
|---|---|---|---|---|
| Scale AI | Engineering Manager, Saudi Arabia | Riyadh | 6 | senior |
| Scale AI | Engagement Manager | Riyadh | 7 | manager |
| MongoDB | Enterprise Account Executive, KSA Public Sector (Arabic Speaker) | Dubai | 5 | — |
| Ajax Systems | Accountant | Warsaw (مثال دولي من نفس المصدر) | — | intern |
| Zeeco | Process Engineer | Dammam | — | — |
| HALA | (وظائف فينتك سعودية متعددة) | Riyadh | — | — |
| Hudson Manpower | Process/HSE/Materials/Structure Engineer | KSA (متعدد المواقع) | — | — |
| Accor | Purchasing Manager | Ras Al-Khaimah | — | manager |

(الأمثلة الكاملة عبر `GET /admin/quality-sample?n=50` بعد تسجيل الدخول الإداري.)

## ثغرات ومخاطر معروفة (تحتاج قرارًا من أحمد أو تنفيذًا لاحقًا)

1. **`field_extractor.extract_cities()`** يتعرّف فقط على مدن سعودية/خليجية —
   وظائف شركات عالمية بمكاتب غير خليجية تُستخرج بـ`city=NULL` دائمًا. هذا
   ليس خطأًا بحد ذاته (المنتج يستهدف السعودية) لكنه يجعل الغالبية العظمى من
   وظائف مصادر مثل MongoDB/Pure Storage/Scale AI/Cloudflare **غير سعودية
   وتحتاج تصفية عند العرض للمستخدم النهائي** (عبر `saudi_only`/الفلترة
   بالواجهة الأمامية، لا بهذا الجامع).
2. **`ops psql` عبر MCP بطيء جدًا** (~2 دقيقة لكل استعلام حتى القراءة فقط)
   وأحيانًا يرفض استعلامات SELECT صحيحة حين تُمرّر مع `-c` صراحة (يعمل
   بدونها) — سبب غير مؤكّد بالكامل، لم يُصلح لأنه خارج نطاق B2 (السكريبت
   بـ`deploy/ops/*` محظور التعديل إلا للضرورة). بديل: نقطتا `/admin/stats`
   و`/admin/dup-breakdown` الجديدتان تغنيان عن أغلب الاستعلامات اليدوية.
3. **لم يُعثر على واجهة آلية** لعدة شركات سعودية مرشَّحة معروفة (Tabby،
   Unifonic، Salla، Ziina) — لم تُفحص فعليًا هذه الجلسة (لا تُدرَج بلا
   تحقق)؛ مرشَّحة لجولة قادمة.
4. **Sanabil Venture Studio**: لم يُعثر على slug Greenhouse الصحيح (4
   تخمينات فشلت) — موثّق بـ`sources_no_api.md`.

## أوامر إعادة التحقق للمراجع

```
curl -s -H "Authorization: Bearer $CORE_ADMIN_TOKEN" https://<host>/admin/stats
curl -s -H "Authorization: Bearer $CORE_ADMIN_TOKEN" "https://<host>/admin/sources?active=true" | jq length
curl -s -H "Authorization: Bearer $CORE_ADMIN_TOKEN" "https://<host>/admin/dup-breakdown?limit=10"
curl -s -H "Authorization: Bearer $CORE_ADMIN_TOKEN" "https://<host>/admin/quality-sample?n=20"
```
