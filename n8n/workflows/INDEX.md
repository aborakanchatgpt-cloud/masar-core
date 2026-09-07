# فهرس سير عمل n8n المُصدَّرة

هذا الفهرس يسرد كل سير عمل (workflow) في مشروع n8n Cloud الخاص بمسار، جاهزة للاستيراد
في نسخة n8n المستضافة ذاتيًا عبر **Import from URL**. الصق رابط الملف من عمود "رابط
الاستيراد" في مربع "Import from URL" داخل n8n (Workflows ← Import from URL).

آخر تحديث: 2026-09-07. المصدر: مشروع n8n Cloud `nMueigxksPOse8LQ` — 21 سير عمل إجمالاً.

---

## 1) جسور (Bridges) — تربط n8n بخدمة Masar Core

هذه سير العمل الجديدة التي تُستخدم كجسور HTTP بسيطة بين n8n وخدمة Masar Core /
GitHub، ويُنصح باستيرادها أولًا.

| الاسم | معرّف Cloud | نشط؟ | نوع المشغّل | الاعتمادات المطلوبة | رابط Import from URL |
|---|---|---|---|---|---|
| Core - Call | `v7xKPShYWHJwqxvs` | نعم | Webhook | Masar Core Admin Token (Header Auth) | https://raw.githubusercontent.com/aborakanchatgpt-cloud/masar-core/main/n8n/workflows/core-call__v7xKPShYWHJwqxvs.json |
| Core - Notify Admin | `UubD6Kba97XGJlVt` | لا (متوقف) | Webhook | — | https://raw.githubusercontent.com/aborakanchatgpt-cloud/masar-core/main/n8n/workflows/core-notify-admin__UubD6Kba97XGJlVt.json |
| Core - Notify Customer | `S6AuI9VPaaQxhfhw` | لا (متوقف) | Webhook | Telegram account | https://raw.githubusercontent.com/aborakanchatgpt-cloud/masar-core/main/n8n/workflows/core-notify-customer__S6AuI9VPaaQxhfhw.json |
| GitHub - Commit File | `K65rLAVzarrtH4SV` | نعم | Webhook | Header Auth account (Masar GitHub) | https://raw.githubusercontent.com/aborakanchatgpt-cloud/masar-core/main/n8n/workflows/github-commit-file__K65rLAVzarrtH4SV.json |
| GitHub - Read File | `HEZbBbYAGxBEvdAz` | نعم | Webhook | Header Auth account (Masar GitHub) | https://raw.githubusercontent.com/aborakanchatgpt-cloud/masar-core/main/n8n/workflows/github-read-file__HEZbBbYAGxBEvdAz.json |
| HTTP - Probe | `zjjF6JrOqB1udpRX` | نعم | Webhook | — | https://raw.githubusercontent.com/aborakanchatgpt-cloud/masar-core/main/n8n/workflows/http-probe__zjjF6JrOqB1udpRX.json |

ملاحظة: `Core - Notify Admin` يستدعي سير عمل آخر بمعرّفه الداخلي
(`ZiVlsBibVps3pxGB` = Job Bot - Claude Discovery Notifier) عبر عقدة Execute Workflow —
راجع القسم 4 لإعادة الربط بعد الاستيراد.

---

## 2) النظام القديم (Legacy) — بوت "مسار" الحالي على تيليجرام

| الاسم | معرّف Cloud | نشط؟ | نوع المشغّل | الاعتمادات المطلوبة | رابط Import from URL |
|---|---|---|---|---|---|
| Claude Autopilot - Build & Send Application | `Y3mccwWalUtkkjav` | نعم | Webhook | Gmail account | https://raw.githubusercontent.com/aborakanchatgpt-cloud/masar-core/main/n8n/workflows/claude-autopilot-build-send-application__Y3mccwWalUtkkjav.json |
| Job Bot - Customer Onboarding (Telegram Intake) | `8TCvk5BsrMPosr9q` | نعم | Manual / Telegram Trigger | Anthropic account، Telegram Admin Bot، Telegram account | https://raw.githubusercontent.com/aborakanchatgpt-cloud/masar-core/main/n8n/workflows/job-bot-customer-onboarding-telegram-intake__8TCvk5BsrMPosr9q.json |
| CV Variants - Upsert Row | `JSQNanVfsO1uOgoP` | نعم | Webhook | — | https://raw.githubusercontent.com/aborakanchatgpt-cloud/masar-core/main/n8n/workflows/cv-variants-upsert-row__JSQNanVfsO1uOgoP.json |
| CV Variants - Build PDF (Base64) | `lBjL0d4yTTzkDm2S` | نعم | Webhook | — | https://raw.githubusercontent.com/aborakanchatgpt-cloud/masar-core/main/n8n/workflows/cv-variants-build-pdf-base64__lBjL0d4yTTzkDm2S.json |
| Job Bot - Admin Assistant (Telegram) | `21JT5X0KN7TYlBV2` | نعم | Telegram Trigger / Manual | Anthropic account، Telegram Admin Bot، Telegram account | **⚠️ غير مُصدَّر تلقائيًا** — حجم الوركفلو (~90 عقدة) يتجاوز حد التصدير الآمن لهذه المهمة. صدّره يدويًا من n8n Cloud: Workflow ← Download، ثم Import from file في النسخة الذاتية |
| Job Postings Pool - Upsert Row | `UPWrmnLr4LWjLV3e` | نعم | Webhook | — | https://raw.githubusercontent.com/aborakanchatgpt-cloud/masar-core/main/n8n/workflows/job-postings-pool-upsert-row__UPWrmnLr4LWjLV3e.json |
| Desires Registry - Upsert Row | `z6pIYROLsrqzYAi2` | نعم | Webhook | — | https://raw.githubusercontent.com/aborakanchatgpt-cloud/masar-core/main/n8n/workflows/desires-registry-upsert-row__z6pIYROLsrqzYAi2.json |
| Company Directory - Upsert Row | `nsjUOSB0oJbVE9h7` | نعم | Webhook | — | https://raw.githubusercontent.com/aborakanchatgpt-cloud/masar-core/main/n8n/workflows/company-directory-upsert-row__nsjUOSB0oJbVE9h7.json |
| Job Bot - Weekly Skill Gap Analysis (Friday) | `VcTFiyUdB7FQDsmw` | نعم | Schedule | Anthropic account، Telegram account | https://raw.githubusercontent.com/aborakanchatgpt-cloud/masar-core/main/n8n/workflows/job-bot-weekly-skill-gap-analysis-friday__VcTFiyUdB7FQDsmw.json |
| Job Bot - Customer Retention (Auto) | `B2PYcIMOQN7i5VXA` | نعم | Manual / Schedule | Telegram Admin Bot، Telegram account | https://raw.githubusercontent.com/aborakanchatgpt-cloud/masar-core/main/n8n/workflows/job-bot-customer-retention-auto__B2PYcIMOQN7i5VXA.json |
| مراقب ويبهوك بوت العميل (Watchdog) | `CjlFHF1JG3GzYLbz` | لا (متوقف) | Schedule | Telegram Admin Bot، Telegram account | https://raw.githubusercontent.com/aborakanchatgpt-cloud/masar-core/main/n8n/workflows/webhook-watchdog__CjlFHF1JG3GzYLbz.json |
| Job Bot - Customer Reports (Scheduled) | `2pS6iFaEMcZTpb8f` | نعم | Schedule | Telegram account | https://raw.githubusercontent.com/aborakanchatgpt-cloud/masar-core/main/n8n/workflows/job-bot-customer-reports-scheduled__2pS6iFaEMcZTpb8f.json |
| Admin - Error Alert (Central) | `zv9sEDDzJUWuEVS4` | نعم | Error Trigger | Telegram Admin Bot | https://raw.githubusercontent.com/aborakanchatgpt-cloud/masar-core/main/n8n/workflows/admin-error-alert-central__zv9sEDDzJUWuEVS4.json |
| Customer Onboarding - Ask Language & Send ATS CV | `TA5HvrlhBaLvl72i` | نعم | Schedule / Execute-Workflow Input | Anthropic account، Telegram account | https://raw.githubusercontent.com/aborakanchatgpt-cloud/masar-core/main/n8n/workflows/customer-onboarding-ask-language-send-ats-cv__TA5HvrlhBaLvl72i.json |
| Job Bot - Claude Discovery Notifier | `ZiVlsBibVps3pxGB` | نعم | Webhook | Telegram Admin Bot | https://raw.githubusercontent.com/aborakanchatgpt-cloud/masar-core/main/n8n/workflows/job-bot-claude-discovery-notifier__ZiVlsBibVps3pxGB.json |

معظم سير العمل أعلاه معظّمة في المستودع من عملية تصدير سابقة (مطابقة لنفس تنسيق
التصدير: بدون معرّفات اعتماد داخلية، مع الاحتفاظ بأسماء الاعتمادات و`webhookId`).
سير عمل **Job Bot - Admin Assistant** وحده لم يُصدَّر بعد (راجع الملاحظة أعلاه).

---

## 3) جداول البيانات (لا تُصدَّر تلقائيًا)

جداول n8n Data Tables **لا** يمكن تصديرها/استيرادها كملفات JSON مثل سير العمل — يجب
إعادة إنشاء بنية كل جدول يدويًا في النسخة المستضافة ذاتيًا، ثم تصدير البيانات من
Cloud كـ CSV واستيرادها. القائمة التالية هي "قائمة تحقق" يدوية لكل جدول (الاسم،
المعرّف، أسماء الأعمدة). عدد الصفوف غير متوفر من واجهة البحث المستخدمة، تحقق منه
داخل n8n Cloud مباشرة قبل التصدير.

| الاسم | المعرّف | الأعمدة |
|---|---|---|
| Verified Sessions | `JjLFqojqJV57a8v7` | telegramChatId، verifiedPhone، step، cvText، customerName، email، fieldOfWork، preferredLocation، jobTitlesCsv، interviewJobTitle، interviewCompany، interviewDate، contactCategory، contactMessageText |
| Job Applications Tracker | `2vlFuz9bzENT5uru` | applicationDate، company، jobTitle، status، keywordsUsed، applyUrl، source، applyEmail، emailSubject، emailBody، tailoredCv، customerId |
| CV Variants | `Pf9X1L2vRygzpK8H` | customerId، clusterId، language، representativeDesires، cvJson، generatedAt، pdfBase64 |
| AdminSession | `F4UAPZYnSB6YjqvL` | chatId، step، whatsappNumber، durationDays، monthlyFee، notes، startMode، fixedStartDate |
| SubscriptionHistory | `FNCWot3mueqY9lKe` | whatsappNumber، customerName، eventType، eventDate، priceAtEvent، daysChanged، notes |
| Pending Approvals | `Rnlo1aVbOJhC8NWR` | whatsappNumber، subscriptionDurationDays، monthlyFee، notes، used، startMode، fixedStartDate |
| Desires Registry | `ex3Tf6SOlT7ICitA` | canonicalTitle، customerIds، lastSearchedAt، active، jobsFoundTotal |
| Company Directory | `GB1UglRypwgpjEZt` | companyName، fieldOfWork، applyEmail، emailStatus، emailSource، lastEmailCheckedAt، sampleJobTitles، lastSeenAt |
| Job Postings Pool | `wogTyknFBjGfvFrS` | postingId، company، jobTitle، jobDescription، applyEmail، applyUrl، source، fieldOfWork، publishDate، expiryDate، isExpired، canonicalTitle، jobLocation |
| Customers | `RqFudRWq2sXxEBgT` | customerName، whatsappNumber، telegramChatId، fieldOfWork، cvText، subscriptionStartDate، subscriptionEndDate، subscriptionStatus، monthlyFee، jobsFoundTotal، applicationsSentTotal، lastSatisfactionCheck، notes، lastRenewalReminderSent، email، preferredLocation، jobTitles، onboardingStatus، preferredCvLanguage |
| Canonical Taxonomy | `mLZYYHjXKaVIygF9` | rawTerm، canonicalTerm، termType، addedAt |
| PotentialCustomers | `MNKw2LOl3mB9Jr8N` | whatsappNumber، telegramChatId، firstContactDate |
| Costs | `QJ7P2kzTBCs1b8c9` | amount، reason، costDate |
| Company Email Directory | `1I1ikUQerKy5WjDs` | companyKey، companyName، email |
| Company_Email_Tasks | `zhVZ6lZi2jB65kxU` | received_at، sender_email، subject، category، summary، status، agent، project، location، title، region، result |

---

## 4) ترتيب الاستيراد المقترح

1. **الجسور أولًا** (القسم 1) — لا تعتمد على أي شيء آخر، وتحتاج فقط إعادة إدخال
   اعتمادات Masar Core Admin Token وMasar GitHub (Header Auth).
2. ثم **النظام القديم** (القسم 2)، بالترتيب: أنشئ جداول البيانات (القسم 3) وأعد
   إدخال اعتمادات Telegram/Anthropic/Gmail أولًا، ثم استورد سير العمل.
3. **إعادة الربط بين سير العمل**: بعض سير العمل يستدعي سير عمل آخر بمعرّفه
   الداخلي (Execute Workflow)، وهذه المعرّفات ستتغيّر بعد الاستيراد في النسخة
   الجديدة. راجع يدويًا بعد الاستيراد:
   - `Core - Notify Admin` (`UubD6Kba97XGJlVt`) → يستدعي `ZiVlsBibVps3pxGB`
     (Job Bot - Claude Discovery Notifier). أعد اختيار سير العمل الصحيح من
     القائمة داخل عقدة Execute Workflow بعد الاستيراد.
   - `Job Bot - Customer Onboarding (Telegram Intake)` (`8TCvk5BsrMPosr9q`) →
     يستدعي `TA5HvrlhBaLvl72i` (Customer Onboarding - Ask Language & Send ATS
     CV). نفس الشيء: أعد الربط يدويًا بعد الاستيراد.
   - سير عمل **Job Bot - Admin Assistant** لم يُفحص للإحالات الداخلية لأنه لم
     يُصدَّر بعد — تحقّق منه يدويًا عند تصديره.
4. فعّل سير العمل واحدًا تلو الآخر بعد التأكد من الاعتمادات والإحالات، وليس
   دفعة واحدة.
