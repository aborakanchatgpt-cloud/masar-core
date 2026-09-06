# دليل تفعيل n8n المستضاف ذاتيًا (لأحمد)

n8n الآن مستضاف على نفس خادم مسار Core، على الرابط:

**https://n8n.62.238.117.20.sslip.io**

اتبع الخطوات بالترتيب:

## 1) إنشاء حساب المالك
افتح الرابط أعلاه لأول مرة، وستظهر صفحة إعداد المالك (owner setup) — أنشئ
الحساب بإيميلك وكلمة سر قوية. هذه الخطوة تُنفّذ مرة واحدة فقط.

## 2) نقل سير العمل (Workflows) من n8n Cloud
في **n8n Cloud** لكل سير عمل: قائمة Workflow ← Download (يحفظ ملف JSON).
ثم في **النسخة المستضافة ذاتيًا**: Import from file لرفع نفس الملف.
كرّر لكل سير عمل من القائمة التالية:

- Core - Call (`v7xKPShYWHJwqxvs`)
- Core - Notify Admin (`UubD6Kba97XGJlVt`)
- Core - Notify Customer (`S6AuI9VPaaQxhfhw`)
- GitHub - Commit File (`K65rLAVzarrtH4SV`)
- GitHub - Read File (`HEZbBbYAGxBEvdAz`)
- HTTP - Probe (`zjjF6JrOqB1udpRX`)
- سير العمل القديمة (النظام السابق): `Y3mccwWalUtkkjav`، `8TCvk5BsrMPosr9q`،
  `ZiVlsBibVps3pxGB`، بالإضافة لسير عمل الـ upsert المساعدة

⚠️ **خطوة يدوية منفصلة — Data Tables**: لا يمكن تصديرها/استيرادها بنفس طريقة
سير العمل. صدّرها من n8n Cloud كملفات CSV، ثم أعد إنشاء الجداول يدويًا في
النسخة المستضافة ذاتيًا واستورد البيانات.

## 3) إعادة إنشاء بيانات الاعتماد (Credentials)
بيانات الاعتماد لا تُنقل تلقائيًا — أعد إنشاءها يدويًا في النسخة الجديدة (أنت
تُدخل القيم بنفسك، لا تُشارك أو تُنسخ من أي مكان):

- Telegram (توكن البوت)
- Gmail (OAuth)
- Header Auth باسم **"Masar Core Admin Token"**
- Header Auth باسم **"Masar GitHub"**

## 4) تفعيل وصول MCP
في النسخة المستضافة ذاتيًا: Settings ← ابحث عن MCP أو "Instance-level MCP"
(إن كانت متاحة بهذا الإصدار) وفعّلها. ثم أضفها كموصل مخصّص (custom connector)
في Claude، وتأكّد أن Claude يقدر يسرد سير العمل (list workflows) بنجاح قبل
الانتقال للخطوة التالية.

## 5) التبديل الفعلي (بعد نجاح الخطوة 4 فقط)
حوّل المهام المجدولة في Claude لاستخدام الموصل الجديد (self-hosted)، واستمر
بالاحتفاظ بحساب n8n Cloud فعّالًا كخط رجوع حتى تتأكد من استقرار كل شيء لفترة
كافية.

## 6) ملاحظة أمنية
مفتاح تشفير n8n (`N8N_ENCRYPTION_KEY`) يُولّد تلقائيًا ويُحفظ داخل
`/opt/masar-core/.env` على الخادم — لا تشاركه مع أحد ولا ترفعه لأي مكان.
