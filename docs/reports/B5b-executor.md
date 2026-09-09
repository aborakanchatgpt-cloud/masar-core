# B5b — بوتا تيليجرام (عميل + أدمن) عبر n8n — تقرير المنفّذ

## الوضع
لا يوجد وصول لواجهة/API n8n من هذه الجلسة (Cloud القديم ميت، ولا صلاحيات على
النسخة الذاتية). سُلِّم العمل كملفات JSON قابلة للاستيراد + سكربت ops +
دليل NEEDS-OWNER، ونُفِّذ **الاستيراد الفعلي** حيًّا عبر `ops{cmd:"script"}`
(الاستيراد فقط — لا تفعيل، لا اعتمادات).

## الملفات المُسلَّمة (7)
- `n8n/workflows/masar_daily_report_relay.json` — جدولة كل 5د بين 19:00–21:30
  آسيا/الرياض → `GET /admin/reports/pending` → بناء رسائل تيليجرام (نص جاهز
  من `payload.text`، مقسّم على حدود 4096 حرفًا) → إرسال → `POST
  /admin/reports/{id}/delivered`. يتخطى عملاء بلا `telegram_chat_id` ويسجّلهم
  بالملخص. أزرار 👎/🎉 مبنية بشكل متوافق مستقبليًا (تُفعَّل تلقائيًا متى توفّر
  `send_queue_id`، راجع NEEDS-CORE #1).
- `n8n/workflows/masar_feedback_callback.json` — Telegram Trigger
  callback_query → تحليل `fb:<customer_id>:<send_queue_id>:<kind>` →
  `POST /customers/{id}/feedback` → ردّ تأكيد دافئ بالعربية (`answerCallbackQuery`).
- `n8n/workflows/masar_onboarding.json` — محادثة تسجيل عميل جديد (اسم، مدن،
  مجالات بأزرار من `data/taxonomy_local.yaml`، سيرة PDF، موافقة) → `POST
  /customers` → `POST /admin/customers/{id}/link-token` → رسالة تحوي رابط
  `/link/{token}` بصياغة غير آلية (لا ذكر لكلمة "بوت"/"أتمتة").
- `n8n/workflows/masar_admin_bot.json` + `masar_admin_bot_part2.json` —
  بوت الأدمن، محمي بحارس `MASAR_OWNER_CHAT_ID` (متغيّر n8n). قُسِّم لملفين
  مرتبطين عبر Execute Workflow لتجاوز حد 25,000 حرف لدفع repo_write (الجزء 2
  يحمل الإجراءات الثقيلة: بحث عن عميل، تفعيل/إيقاف، "نحتاجك"). الجزء 2 يبقى
  غير نشط دائمًا بالتصميم (يُستدعى داخليًا فقط).
- `deploy/ops/scripts/n8n_import.sh` — يستورد `n8n/workflows/masar_*.json`
  فقط (لا يلمس الوركفلوهات القديمة/parts)، idempotent عبر `id` علوي ثابت لكل
  ملف، لا يُفعّل أي وركفلو أبدًا.
- `n8n/README.md` — دليل عربي: قائمة NEEDS-OWNER مرقّمة، ترتيب تفعيل صارم،
  مسار اختبار، فجوات NEEDS-CORE، ملاحظات تنفيذ غير مُختبرة حيًّا.

## بنية الوركفلوهات (تفاصيل تقنية)
كل ملف export صالح: `{name, nodes[], connections, settings, pinData:{}, id}`.
اعتمادات بالاسم فقط (`Masar Core Admin Token` / `Masar Customer Bot` /
`Masar Admin Bot`) — لا قيم فعلية أو معرّفات داخلية. الحالة محفوظة عبر
`$getWorkflowStaticData('global')` (لا Data Tables — لا يمكن إنشاؤها عبر
JSON import، مؤكَّد من `n8n/workflows/INDEX.md`). typeVersions مطابقة
لأمثلة حقيقية من وركفلوهات المستودع القائمة (code=2, httpRequest=4.5,
if=2.2, telegram=1.2, telegramTrigger=1.5, scheduleTrigger=1.4, switch=3,
executeWorkflow=1.3, executeWorkflowTrigger=1.2).

## التحقق (Validation)
1. **تحقّق بنيوي صارم**: كل عقدة تحمل `id/type/typeVersion/position/parameters`،
   وكل اتصال بـ`connections` يشير لاسم عقدة موجود فعليًا (سكربت `validate()`
   مخصّص لكل وركفلو).
2. **تحقّق تنفيذي محلي حقيقي**: تثبيت `n8n` v2.35.7 عبر `npm install -g n8n`
   محليًا، تشغيل نسخة sqlite معزولة، وتنفيذ
   `n8n import:workflow --input=<file>` على كل ملف. تأكيد مباشر من
   `database.sqlite` أن الاستيراد **idempotent** (إعادة الاستيراد لا تكرّر
   الصفوف) وأن كل وركفلو يستورَد **غير نشط** افتراضيًا. هذا الاختبار كشف أن
   `n8n import:workflow` بهذه النسخة **يرفض** أي ملف بلا `id` علوي — مما
   أثّر على تصميم `n8n_import.sh` (نطاقه محصور بـ`masar_*.json` فقط، الملفات
   القديمة بلا `id` خارج نطاقه، موثّق بالسكربت وبـREADME).
3. **تحقّق حيّ على الخادم الفعلي**: تشغيل `ops{cmd:"script", args:["n8n_import"]}`
   فعليًا — النتيجة: **5 مستورد/محدَّث بنفس id، 0 فشل**، مؤكَّد بقائمة
   `n8n list:workflow` النهائية (تُظهر كل الوركفلوهات الـ5 الجديدة إلى جانب
   القديمة دون أي تكرار أو تلف).

## قيود لم تُختبر حيًّا (موثّقة بالREADME §4)
عقدة `answerQuery` وتعبير `inlineKeyboard` الديناميكي بُنيا حسب مخطّط
Telegram القياسي في n8n لكن بلا تنفيذ فعلي عبر بوت حقيقي (لا وصول شبكي من
بيئة البناء) — يحتاجان تأكيدًا من المالك بعد التفعيل.

## NEEDS-OWNER (ملخّص — التفاصيل الكاملة بـn8n/README.md)
1. اعتماد Header Auth **Masar Core Admin Token** (التوكن من `.env` بالخادم،
   لا يُلصق بأي محادثة).
2. اعتماد Telegram API **Masar Customer Bot** (بوت مسار الحالي).
3. اعتماد Telegram API **Masar Admin Bot** (بوت جديد منفصل — توكن مختلف
   إلزاميًا).
4. متغيّر n8n **MASAR_OWNER_CHAT_ID** (طريقة استخراجه موثّقة بالدليل).
5. تفعيل الوركفلوهات الأربعة **بالترتيب**: بوت الأدمن → تسجيل عميل جديد →
   ردود الفعل → تسليم التقرير اليومي (الجزء 2 من بوت الأدمن يبقى غير نشط
   عمدًا).

## NEEDS-CORE (فجوات خارج نطاق B5b — تفاصيل كاملة بـREADME §3)
1. **[الأهم]** `daily_reports.payload.today_applications` بلا أي معرّف —
   يمنع أزرار 👎/🎉 الفعلية اليوم؛ الحل مصمَّم متوافقًا مستقبليًا بلا حاجة
   لتعديل n8n لاحقًا.
2. لا نقطة نهاية لتحويل `telegram_chat_id` → `customer_id` — عولج بتضمين
   `customer_id` داخل `callback_data` (يخالف الصيغة الحرفية المطلوبة بالتكليف
   عمدًا لعدم وجود بديل).
3. لا نقطة نهاية لرفع سيرة PDF فعلية — يُخزَّن معرّف ملف تيليجرام فقط كمرجع.
4. لا نقطة نهاية لتفعيل/إيقاف عميل (`POST /admin/customers/{id}/status`) —
   الزر يعمل بالواجهة، يردّ باعتذار واضح بدل الفشل الصامت.
5. تصميم متعمَّد يستحق مراجعة: `customers.email_service` NOT NULL/UNIQUE مع
   تجميع Gmail المؤجَّل — عولج بقيمة مؤقتة فريدة.

## لم يُلمَس
- بقية `n8n/workflows/*.json` (تصدير n8n Cloud القديم) و`n8n/workflows/parts/`
  و`n8n-import.sh` الموجود مسبقًا — خارج النطاق بالكامل، كما هو منصوص.
