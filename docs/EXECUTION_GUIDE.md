# مسار v5 — دليل التنفيذ الكامل (Handoff للجلسة المنفِّذة)

> **لمن يقرأ هذا الملف من جلسة جديدة:** هذا الملف هو المرجع الوحيد والكامل. اقرأه كله قبل أي خطوة. كل قرار فيه نهائي ومتفق عليه مع أحمد، فلا تعد فتح النقاش فيه. حين يكتب أحمد "نفذ" ابدأ من القسم 9 (خطة التنفيذ) بالترتيب، واعمل باستمرار دون توقف، ولا تسأل إلا حين تُحجب بصلاحية أو معلومة لا يمكنك الحصول عليها بنفسك (القسم 10 يحدد متى تسأل). أرسل لأحمد ملخصًا قصيرًا في نهاية كل مرحلة عبر وركفلو إشعار الأدمن.
> التاريخ: 5 سبتمبر 2026. المالك: أحمد الغامدي (Telegram admin chat: 677475661، واتساب +966544161255).

---

## 0. قواعد العمل للجلسة المنفِّذة (اقرأها أولًا)

1. **الأدوات المتاحة لك:** n8n عبر MCP (`mcp__n8n__*`، projectId `nMueigxksPOse8LQ`)، المهام المجدولة في Claude Code Remote (`list_triggers/create_trigger/update_trigger/delete_trigger`)، جهاز أحمد عبر جسر الجهاز (`mcp__remote-devices__*`: shell محلي `device_bash`، ملفات، ومتصفح Chrome `mcp__claude-in-chrome__*` أو المتصفح المدمج). أحمد أعطى صلاحية كاملة لاستخدام جهازه ومتصفحه لإنشاء الحسابات (GitHub، مزود الخادم) وإعداد الخادم. إن احتجت صلاحية أو خطوة لا تستطيعها، اطلبها منه في المحادثة بجملة واحدة واستمر في ما تستطيعه.
2. **الأسرار:** لا تكتب أي كلمة مرور/توكن/كلمة مرور تطبيق في المحادثة ولا في Git. تُوضع فقط في ملف `.env` على الخادم (عبر device_bash + ssh، أو يلصقها أحمد بنفسه) وفي GitHub Secrets عند الحاجة.
3. **لا تحذف بيانات ولا تُطفئ النظام القديم** قبل أن يتفوق الجديد عليه 3 أيام متتالية (القسم 9، المرحلة 4). القديم والجديد يعملان بالتوازي على أحمد كعميل تجريبي.
4. **القيد الدائم غير القابل للتفاوض:** ممنوع بناء أو استخدام أي أداة/طريقة هدفها تجاوز قيود الوصول أو البحث لأي موقع (لا كشط لمواقع تمنعه، لا تجاوز CAPTCHA، لا إنشاء حسابات نيابة عن أحد، لا أتمتة نماذج داخل حسابات الغير). المصادر المسموحة في القسم 5 فقط.
5. **لا رسوم لكل عملية:** ممنوع أي API أو خدمة تُحاسب لكل طلب/إيميل/وظيفة. المسموح: خادم بمبلغ ثابت، وخدمات مجانية، واشتراك n8n وClaude الحاليان.
6. **نبرة العميل:** بوت "مسار" إنسان دافئ بلغة عربية مهذبة مع أدعية خفيفة، لا يكشف أبدًا أنه آلي، ولا يستخدم صياغة تجارية ("خدمة مدفوعة"، "باقة"…) في الحوار مع العميل؛ تُعرض الخيارات كأنواع مساعدة لا كمنتجات. لوحة الأدمن لأحمد يمكن أن تكون تقنية/تجارية بحرية.
7. **الإبلاغ:** في نهاية كل مرحلة، ومرة يوميًا على الأقل، أرسل لأحمد عبر `execute_workflow` على `ZiVlsBibVps3pxGB` (body: `{"message": "..."}`) ملخصًا: ما أُنجز، ما التالي، ما يحتاج قراره.
8. **الذاكرة:** ملف الذاكرة `/areas/masar-saas.md` يحتوي قرارات أحمد الثابتة؛ اقرأه في البداية وحدّثه عند أي قرار جديد يقوله أحمد.

---

## 1. الفكرة والقرارات النهائية (لا تُناقش مجددًا)

**المنتج:** خدمة "مسار": العميل يرسل سيرته الذاتية مرة واحدة عبر بوت تيليجرام، والنظام يقدّم له تلقائيًا على وظائف مناسبة لمستواه وتخصصه يوميًا، بالإيميل، من صندوق بريد خاص بالخدمة أنشأه العميل نفسه.

**القرارات:**
1. **التقديم بالإيميل فقط.** لا حزم "بضغطة"، لا أتمتة نماذج.
2. **هوية الإرسال = صندوق بريد جديد ينشئه العميل خصيصًا للخدمة** (Gmail، باسمه الحقيقي، تحقق بخطوتين، كلمة مرور تطبيق). مسار لا يلمس بريده الشخصي أبدًا. النظام يرسل من هذا الصندوق (SMTP) ويقرأ وارده (IMAP) لاكتشاف الردود والارتدادات، بموافقة موثقة.
3. **الهدف:** 17 تقديمًا يوميًا كحد أدنى، 22 كحد أعلى، ≥ 510 في الشهر. المحاسبة **شهرية** (لا يومية) مع تسخين تدريجي للصناديق الجديدة.
4. **لا يُعرض للعميل أي تقدير طاقة أو سقف.** تقدير الطاقة داخلي فقط (للمخطِّط ولأحمد).
5. **سياسة الضمان (للاشتراك الشهري فقط):** إن لم يبلغ 510 عند نهاية الشهر → تمديد يومين تلقائي → إن بقي عجز → تعويض = العجز × (سعر الاشتراك ÷ 510)، يحسبه النظام ويرسل لأحمد زر اعتماد، وأحمد يحوّل. المرتد لا يُحتسب. أيام الانقطاع بسبب العميل (فصل بريده) تُمدد بدل التعويض.
6. **المحفظة:** كل ما يُباع يتحول إلى "رصيد تقديمات". أنواع: رصيد مسبق (لا ينتهي، يتوقف عند 0 حتى الشحن، بنفس الوتيرة اليومية)، اشتراك شهري (منحة 510 + ضمان)، باقات (تركيبات). كل إيميل ناجح يخصم 1 داخل نفس معاملة التسجيل.
7. **خدمة السيرة الذاتية ATS:** مجانية داخل الاشتراك الشهري، ومنتج مستقل برسوم رمزية لغير المشتركين.
8. **الطبقات عند التخطيط:** A مطابقة مباشرة (درجة ≥ 0.75) ← B مجاورة (مسمى في نفس العائلة أو مدينة مقبولة، 0.60–0.75) ← C استباقي (شركات ثبت أنها وظّفت المسمى خلال 90 يومًا ولها إيميل توظيف) ← D توسيع جغرافي **ضمن المدن التي اختارها العميل فقط**. التوسيع صامت (لا يُبلَّغ العميل).
9. **إيميلات الشركات:** careers@/hr@/recruitment@/jobs@ أو إيميل مذكور في الإعلان = درجة أولى. info@/contact@ مسموح كمستوى أخير بشروط (القسم 4.8). sales@/support@/billing@/noreply@/marketing@ ممنوعة نهائيًا.
10. **الـCC:** في النظام القديم تبقى CC للعميل. في v5 لا CC (الرد يصل صندوقه مباشرة ويُقرأ آليًا ويُرسل له على تيليجرام).
11. **البنية:** n8n + تيليجرام تبقى واجهة (تسجيل، أدمن، تجديد، تقارير). القلب (اكتشاف، مطابقة، تخطيط، إرسال، محفظة، قراءة الردود) يُبنى كخدمة "Masar Core" (Python) مع Postgres وGotenberg على خادم واحد ثابت التكلفة، مصممة كخدمات منفصلة قابلة للتوسع.
12. **Claude** يبقى لمهام محدودة العدد: استخراج ملف العميل من السيرة مرة واحدة، تنويعات السيرة أسبوعيًا، توسيم المسميات الجديدة دفعة يومية، اكتشاف مصادر شركات أسبوعيًا، تدقيق عينة يومية. لا شيء في المسار اليومي يعتمد عليه.

---

## 2. جرد النظام الحالي (كما هو في 5 سبتمبر 2026)

### 2.1 n8n (projectId `nMueigxksPOse8LQ`, instance `aborakan.app.n8n.cloud`)

**Data Tables:**
| الجدول | id | ملاحظة |
|---|---|---|
| Customers | `RqFudRWq2sXxEBgT` | عميل واحد فعلي (id=1 أحمد، حالته Expired حاليًا — اشتراك تجريبي) |
| CV Variants | `Pf9X1L2vRygzpK8H` | customerId, clusterId, language, representativeDesires, cvJson, pdfBase64, generatedAt |
| Job Postings Pool | `wogTyknFBjGfvFrS` | 47 صفًا، 36% تكرار |
| Desires Registry | `ex3Tf6SOlT7ICitA` | 19 مسمى، كلها active=false حاليًا (العميل منتهٍ) |
| Company Directory | `GB1UglRypwgpjEZt` | companyName, fieldOfWork, applyEmail, emailStatus, emailSource, lastEmailCheckedAt, sampleJobTitles, lastSeenAt |
| Company Email Directory | `1I1ikUQerKy5WjDs` | قديم، مزدوج مع السابق |
| Job Applications Tracker | `2vlFuz9bzENT5uru` | 106 صفوف؛ 42 مُرسلة فعليًا |
| Canonical Taxonomy | `mLZYYHjXKaVIygF9` | rawTerm, canonicalTerm, termType, addedAt |
| Pending Approvals `Rnlo1aVbOJhC8NWR`, Verified Sessions `JjLFqojqJV57a8v7`, AdminSession `F4UAPZYnSB6YjqvL`, SubscriptionHistory `FNCWot3mueqY9lKe`, PotentialCustomers `MNKw2LOl3mB9Jr8N`, Costs `QJ7P2kzTBCs1b8c9`, Company_Email_Tasks `zhVZ6lZi2jB65kxU` | | جداول التسجيل والإدارة — تبقى |

**Workflows:**
| الاسم | id | الدور | مصيره في v5 |
|---|---|---|---|
| Job Bot - Customer Onboarding (Telegram Intake) | `8TCvk5BsrMPosr9q` | 58 عقدة، Onboarding Router (state machine في jsCode) | يبقى + إضافات (القسم 7) |
| Claude Autopilot - Build & Send Application | `Y3mccwWalUtkkjav` | ويبهوك `claude-apply-send`: Quality Check → PDF (Gotenberg على Render) → Gmail (حساب واحد) → Tracker | يُعدَّل فورًا (المرحلة 0) ثم يُستبدل بـSender في Core |
| CV Variants - Upsert Row `JSQNanVfsO1uOgoP`, CV Variants - Build PDF `lBjL0d4yTTzkDm2S` | | | تبقى مؤقتًا ثم تنتقل لـCore |
| Job Postings Pool - Upsert Row | `UPWrmnLr4LWjLV3e` | | يُستبدل بالجامع |
| Desires Registry - Upsert Row | `z6pIYROLsrqzYAi2` | | يُستبدل |
| Company Directory - Upsert Row | `nsjUOSB0oJbVE9h7` | | يُستبدل |
| Job Bot - Admin Assistant (Telegram) | `21JT5X0KN7TYlBV2` | بوت الأدمن | يبقى + لوحة أرقام |
| Job Bot - Customer Retention (Auto) | `B2PYcIMOQN7i5VXA` | تذكير/انتهاء | يبقى ويقرأ من Core |
| Job Bot - Customer Reports (Scheduled) | `2pS6iFaEMcZTpb8f` | | يُستبدل بتقرير Core اليومي |
| Job Bot - Weekly Skill Gap Analysis | `VcTFiyUdB7FQDsmw` | | يبقى |
| Customer Onboarding - Ask Language & Send ATS CV | `TA5HvrlhBaLvl72i` | هدية السيرة | يصبح "منتج السيرة" (القسم 4.10) |
| Job Bot - Claude Discovery Notifier | `ZiVlsBibVps3pxGB` | إشعار الأدمن: body `{"message"}` | يبقى (قناة الإبلاغ) |
| Admin - Error Alert (Central) | `zv9sEDDzJUWuEVS4` | | يبقى |
| مراقب ويبهوك بوت العميل | `CjlFHF1JG3GzYLbz` | | يبقى |

**مواقع مهمة في وركفلو الإرسال `Y3mccwWalUtkkjav`:** عقدة `Quality Check & Build Email` (jsCode: قالبان A/B، فحص placeholder)، عقدة `Send Application Email` (Gmail credential `daiR07ka2HzN33Kl`، senderName=customerName، replyTo=customerEmail، cc=customerEmail)، عقدة `Convert HTML to PDF (Gotenberg)` (URL: `https://gotenberg-pdf-5abo.onrender.com/forms/chromium/convert/html`، retry ×5)، عقدة `Log Application - Sent`.

### 2.2 المهام المجدولة في Claude Code Remote
| الاسم | trigger_id | الجدولة | مصيره |
|---|---|---|---|
| Job Applications … Shard A | `trig_014uCrVrmWSUUQMChjVRBmyp` | `20 */2 * * *` | يُعدَّل (المرحلة 0) ثم يُحذف بعد المرحلة 4 |
| Shard B | `trig_01EPHCoWLFdY3eiNZj8n6ZMs` | `20 1-23/2 * * *` | نفسه |
| Shard C | `trig_01886tPQLG83vpJoyn9nPtbD` | `50 */2 * * *` | نفسه |
| Job Discovery via Claude | `trig_01DpjVgj9ca6YR6YDfKFBxag` | `12 */2 * * *` | يُحذف بعد المرحلة 2 |
| Job Bot - Company Email Finder | `trig_01XkWaknvXBwnfUnBchMDakc` | `30 */4 * * *` | يُحوَّل إلى "اكتشاف مصادر الشركات" أسبوعيًا |
| Job Bot - Desires Registry Sync | `trig_01YH3m3jXLZefPQBvWkePYLg` | `8 */6 * * *` | يُحذف بعد المرحلة 2 |
| Job Bot - CV Variant Generator | `trig_01MRxuTTXxXrqTWPfuy1s62M` | `15 */12 * * *` | يبقى ويصبح أسبوعيًا ويكتب عبر Core |
| Job Bot - Customer Status Report | `trig_01UeKNEzYe4Nx4zuY9GDD8av` | `35 */6 * * *` | يُحذف |
| Job Bot - Pipeline Watchdog | `trig_018Q2jkzhsaChDih3u1bDgNx` | `5 * * * *` | يُحذف |
| Job Bot - Underserved Customer Watchdog | `trig_012GojNEayHRktMojFbKY81F` | `0 18 * * *` | يُحذف (يحل محله تنبيه Core) |

### 2.3 العيوب الموثقة (أدلة من الفحص الحي)
- سقف التقديم 216/يوم للنظام كله (3 شاردات × 12 × 6)؛ المرصود 8.4/يوم لعميل واحد.
- حساب Gmail واحد يرسل بأسماء العملاء (سقف 500/يوم = 29 عميلًا؛ نمط انتحال).
- تكرار: 47 صفًا في المخزون = 30 وظيفة فريدة؛ Elbait استقبلت 8 رسائل من نفس المرشح (3 لنفس الوظيفة)؛ QS Quest ×3؛ Eram ×3. السبب: postingId من الرابط الذي يختلف بين `/mobile/` والديسكتوب.
- لا استبعاد قاطع: 13 من 30 وظيفة فريدة غير مؤهلة (Director 20–25 سنة، 15 سنة، SRE برمجيات، كهرباء…).
- 26% من تنفيذات وركفلو الإرسال فشلت (Gotenberg على Render ينام).
- 4 من 42 سيرة مُرسلة احتوت أرقام هواتف المراجع.
- إرسال إلى sales@/info@ (6 من 42).
- الإرسال 24/7 بما فيه الجمعة.
- الاكتشاف متوقف كليًا (كل الرغبات inactive) ومسجَّل "نجاح".

---

## 3. البنية المستهدفة: Masar Core

### 3.1 الخدمات (وحدات داخل مشروع Python واحد، كل وحدة بجداولها وواجهتها)
1. **identity** — العملاء، الموافقة، الحذف، بريد الخدمة (SMTP/IMAP مشفر).
2. **billing** — الكتالوج، الطلبات، دفتر الرصيد (ledger)، الضمان/التعويض.
3. **profile** — الملف المهيكل للعميل، تأكيده، تنويعات السيرة (يستدعي Gotenberg).
4. **discovery** — سجل المصادر، الجامع (collectors لكل نوع مصدر)، الوظائف، الشركات.
5. **taxonomy** — العائلات، المسميات الموحّدة، المهارات (ESCO + محلي)، طابور التوسيم.
6. **matching** — التطبيع، الاستبعاد القاطع، الدرجة، الطبقات.
7. **planning** — خطة اليوم، الوتيرة، التسخين، الطابور.
8. **sending** — الإرسال SMTP، القوالب التركيبية، الخصم من الرصيد، إعادة المحاولة.
9. **inbox** — قراءة IMAP: ردود، ارتدادات، دعوات مقابلات.
10. **reporting** — تقرير العميل اليومي، أزرار التغذية الراجعة، لوحة الأدمن، التنبيهات.
11. **bridge** — واجهة REST (FastAPI) بتوكن، يستدعيها n8n، ووركفلوهات n8n "جسر" تستدعيها جلسات Claude.

### 3.2 التقنيات
Python 3.12، FastAPI، SQLAlchemy + Alembic، APScheduler، psycopg، aiosmtplib/smtplib، imapclient، httpx، selectolax/lxml، rapidfuzz، Postgres 16، Gotenberg (Docker)، Caddy (TLS تلقائي) أو Cloudflare Tunnel (مجاني، بلا فتح منافذ)، Docker Compose، GitHub Actions أو سكربت سحب تلقائي.

### 3.3 مخطط قاعدة البيانات (Postgres)
```sql
-- identity
customers(id serial pk, name, telegram_chat_id, whatsapp, personal_email_optional,
  service_email, smtp_password_enc, imap_ok bool, mail_link_status text, -- ok|broken|unset
  mail_link_broken_since timestamptz, status text, -- pending|active|paused|expired|deleted
  consent_at timestamptz, deleted_at timestamptz, created_at, updated_at)
-- billing
products(id, code unique, kind text, -- credits|subscription|package|cv_service
  name_ar, applications int, duration_days int, includes_cv bool, price_sar numeric, active bool)
orders(id, customer_id, product_id, price_sar, paid_at, approved_by_admin bool, note)
ledger(id, customer_id, order_id, kind text, -- grant|debit|refund|extend|bounce_reversal|compensation
  amount int, price_per_app numeric, ref_application_id, created_at)
-- balance = sum(amount) where kind in (grant, debit(-1), bounce_reversal(+1))
subscriptions(id, customer_id, order_id, start_date, end_date, extended_days int default 0,
  target_apps int default 510, status, closed_at, shortfall int, compensation_sar numeric, compensation_paid bool)
-- profile
profiles(customer_id pk, years_experience numeric, seniority text, nationality text,
  degree_family text[], families text[], titles jsonb, -- [{canonical_id, weight 1.0|0.7|0.4}]
  skills text[], certs text[], cities_primary text[], cities_accepted text[],
  sectors text[], blocklist_companies text[], current_employer text, langs text[],
  cv_text, confirmed_at, source text) -- claude|heuristic
cv_variants(id, customer_id, cluster_id, language, cv_json jsonb, pdf bytea, template_id int, generated_at)
-- taxonomy
families(id, code, name_ar, name_en)
titles(id, family_id, canonical_ar, canonical_en, aliases text[], esco_code)
skills(id, canonical, aliases text[], esco_uri, idf numeric)
tagging_queue(id, raw_term, term_type, context, status, created_at)
-- discovery
companies(id, company_key unique, name, aliases text[], sector, city, country,
  source_quality numeric, careers_email, careers_email_class text, -- careers|posted|generic|banned
  careers_email_status text, careers_page_url, ats_type, last_hiring_titles text[],
  last_hiring_seen_at, suppressed bool, suppressed_reason)
sources(id, company_id, kind text, -- ats_greenhouse|ats_lever|ats_smartrecruiters|ats_workable|ats_ashby|ats_recruitee|ats_teamtailor|ats_bamboohr|ats_workday|ats_successfactors|ats_oracle|sitemap_jsonld|rss|alert_mail|manual
  url, config jsonb, enabled bool, last_ok_at, last_error, last_count int, avg_per_day numeric, terms_note text)
postings(id, posting_key unique, company_id, source_id, title_raw, title_id, family_id,
  description, city, country, years_req_min int, seniority text, saudi_only bool,
  degree_req text[], certs_req text[], skills text[], lang text,
  apply_mode text, -- email|form|unknown
  apply_email, apply_email_class text, apply_url, posted_at date, expires_at date,
  fetched_at, updated_at)
-- matching/planning
opportunities(id, customer_id, posting_id, company_id, tier text, score numeric, score_parts jsonb,
  excluded_reason text, planned_for date, status text, -- planned|queued|sent|failed|skipped|excluded
  unique(customer_id, posting_id))
send_queue(id, opportunity_id, send_after timestamptz, attempts int, last_error, locked_until timestamptz)
applications(id, customer_id, company_id, title_key text, posting_id, sent_at, message_id,
  subject, body, cv_variant_id, apply_email, bounced bool, bounce_at, reply_at, reply_kind text,
  counted bool default true)
-- unique per 60 days enforced by partial index on (customer_id, company_id, title_key) + trigger checking date window
company_contact_log(company_id, week_start date, customers_count int, primary key(company_id, week_start))
feedback(id, customer_id, application_id, kind text, -- irrelevant|interview|applied_elsewhere
  reason text, created_at)
-- ops
metrics_hourly(ts, source_id, postings_new int)
alerts(id, kind, payload jsonb, sent_at)
settings(key pk, value jsonb) -- send_window, ramadan, holidays, caps
```

### 3.4 مفتاح التكرار للوظيفة (`posting_key`)
1. إن كان الرابط من منصة معروفة وفيه معرّف (GulfTalent `-\d+$`، Sabbar UUID، Bayt/Naukrigulf معرّف رقمي، ATS id) → `platform:id`.
2. وإلا → `sha1(company_key + '|' + normalize(title) + '|' + city)`.
`normalize`: lowercase، حذف الأقواس ومحتواها، حذف كلمات الأقدمية الزخرفية والجنسية ("Saudi national", "سعودي", "Senior/Junior" تُحفظ في حقل seniority لا في المفتاح)، توحيد عربي (أ/إ/آ→ا، ة→ه، ى→ي، حذف التشكيل والتطويل)، ضغط المسافات.
`company_key`: normalize(name) بعد حذف اللواحق (Co., Ltd, LLC, International, Holdings, شركة، المحدودة).

### 3.5 المطبّع (Normalizer) — استخراج حتمي من نص الإعلان
- **السنوات:** أنماط `(\d{1,2})\s*\+?\s*(years|yrs|سنوات|سنة|عام)`، ونطاق `(\d+)\s*[-–to]+\s*(\d+)` يؤخذ الأدنى؛ "fresh/خريج حديث" = 0.
- **الأقدمية:** قاموس: intern/trainee/متدرب=0، junior/مبتدئ=1، (لا شيء)=2، senior/أول=3، lead/principal/رئيس فريق=4، supervisor/مشرف=4، manager/مدير=5، head/director/رئيس قسم/مدير عام=6، VP/executive=7.
- **الجنسية:** `saudi national|saudis only|سعودي الجنسية|للسعوديين` → saudi_only=true.
- **التخصص المشترط:** بعد "bachelor|degree|بكالوريوس" حتى نهاية الجملة، يُطابق قاموس تخصصات (chemical, mechanical, electrical, civil, industrial, computer, …).
- **الشهادات:** قاموس (NEBOSH, OSHA, PMP, Six Sigma, ISO 9001, API, NACE, ASNT, CSWIP, HACCP, …).
- **المدينة:** قاموس مدن السعودية والخليج بالعربية والإنجليزية والصيغ الشائعة (Jeddah/جدة/جده، Riyadh/الرياض، Dammam/الدمام، Jubail/الجبيل، Yanbu/ينبع، KAEC/رابغ، Makkah/مكة، NEOM…). "عن بعد/remote" → city='remote'.
- **اللغة:** نسبة الحروف العربية > 30% → ar.
- **المهارات:** مطابقة معجم skills (aliases) على النص.
- **نوع التقديم:** إيميل في النص (`[\w.+-]+@[\w-]+\.[\w.]+`) → apply_mode=email + تصنيف الإيميل (4.8)؛ وإلا form.
- **الحقول غير المستخرجة تبقى NULL** ولا تسبب استبعادًا (تسبب خصمًا في الدرجة فقط).

### 3.6 الاستبعاد القاطع (قبل الدرجة؛ يُسجَّل السبب في `excluded_reason`)
1. `years_req_min > profile.years + 2`
2. `seniority ≥ 5 (manager+) AND profile.years < 6`
3. `saudi_only AND profile.nationality != 'SA'`
4. `degree_req` غير فارغ ولا يتقاطع مع `profile.degree_family` (كيميائية تتقاطع مع: chemical, process, petrochemical, materials, industrial)
5. `family_id ∉ profile.families`
6. `posted_at < today - 14` أو `expires_at < today`
7. `company ∈ blocklist` أو `company = current_employer`
8. `apply_email_class = banned` (sales/support/billing/noreply/marketing/finance)
المستبعَد بالأسباب 1–4 يظهر في تقرير العميل تحت "استبعدنا لك" بزر "قدّم رغم ذلك" (يتجاوز القاعدة لهذه الوظيفة فقط).

### 3.7 الدرجة والطبقات
`score = 0.35·title + 0.30·skills + 0.15·seniority + 0.10·location + 0.05·recency + 0.05·source_quality`
- title: وزن المسمى في ملف العميل (1.0 أساسي / 0.7 ثانوي / 0.4 هامشي) إن تطابق canonical؛ وإلا 0.5 إن كان في نفس العائلة؛ وإلا 0.
- skills: Jaccard موزون بـ IDF بين مهارات الإعلان ومهارات العميل (إن لم تُستخرج مهارات من الإعلان → 0.5).
- seniority: 1.0 تطابق، −0.25 لكل درجة فرق، 0.6 إن مجهولة.
- location: 1.0 مدينة أساسية، 0.7 مقبولة، 0.3 غير محددة، 0 مختلفة (تُستبعد من A/B وتدخل D فقط إن كانت ضمن المدن المقبولة… المدن خارج المدن المختارة لا تُستخدم أبدًا).
- recency: 1.0 (≤3 أيام) → 0.5 (14 يومًا).
- source_quality: 1.0 صاحب عمل مباشر (ATS/موقع الشركة)، 0.6 موقع وظائف، 0.3 وكالة توظيف.
- الطبقات: A ≥ 0.75 مع apply_mode=email؛ B 0.60–0.75 مع email؛ C من `companies` (last_hiring_seen_at ≤ 90 يومًا، careers_email_class ∈ {careers, posted}، لا تقديم من هذا العميل لها خلال 60 يومًا، company_contact_log هذا الأسبوع < 3)؛ C2 نفس الشروط لكن class=generic (info@/contact@) بسقف 3/يوم/عميل وتبريد 90 يومًا و< 2 عملاء/أسبوع؛ D كـA/B لكن في المدن المقبولة الثانوية.
- **التعلّم:** 👎 مع سبب "أقل من مستواي" ×3 على مسمى → وزنه ينزل درجة؛ "مدينة" → تُحذف المدينة من المقبولة؛ "مجال" → تُحذف العائلة؛ "شركة" → blocklist. 🎉 مقابلة → +0.1 على source_quality للمصدر وعلى وزن المسمى لهذا العميل.

### 3.8 المخطِّط (Planner) — 06:00 الرياض ثم كل ساعة
```
for customer in active_with_balance:
    daily_cap = min(ramp_cap(service_email_age_days), 22, balance)
    # ramp: day1-2:6, day3-5:12, day6-9:16, day10+:22 ; يُطبَّق فقط على الصناديق الجديدة
    pace_target = 17 * days_elapsed_in_period ; deficit = pace_target - sent_in_period
    target_today = 17 if deficit <= 0 else min(daily_cap, 17 + min(deficit, 5))
    taken = 0
    for tier in [A, B, C, D, C2]:
        cands = eligible(customer, tier) ordered by score desc, posted_at desc
        for c in cands:
            if taken >= target_today: break
            if violates(cooldown_60d(customer, company) or weekly_company_cap or unique_60d(customer, company, title_key)): continue
            enqueue(c, send_after=random_slot_in_window(taken))
            taken += 1
    if taken < 12: alert_admin(customer, reason=diagnose(customer))  # narrow_field | source_down | mail_link_broken | balance_zero
```
- نافذة الإرسال من `settings.send_window` (افتراضي الأحد–الخميس 08:00–16:30 Asia/Riyadh؛ رمضان 10:00–15:00؛ الإجازات الرسمية موقوفة). الفواصل بين رسائل نفس العميل 4–15 دقيقة عشوائية.
- وظيفة A جديدة تصل خلال اليوم → تُضاف فورًا لخطط العملاء المطابقين الذين لم تكتمل حصتهم.
- العميل الذي `mail_link_status=broken`: لا خطة، تذكير يومي، وتُسجَّل أيام الانقطاع لتمديد الاشتراك.

### 3.9 المُرسِل (Sender) — كل دقيقة
1. `SELECT ... FROM send_queue WHERE send_after <= now() AND (locked_until IS NULL OR locked_until < now()) FOR UPDATE SKIP LOCKED LIMIT 20`.
2. لكل عنصر: اختيار تنويع السيرة (لغة الإعلان + العنقود الأقرب)، PDF من `cv_variants.pdf` (وإلا بناء عبر Gotenberg وتخزينه).
3. توليد نص الإيميل بالمولّد التركيبي (3.10) والموضوع: "{المسمى} — {اسم العميل}" أو بالعربية "طلب توظيف: {المسمى} — {الاسم}". للفئة C2 (info@): الموضوع يبدأ بـ"طلب توظيف لوظيفة {المسمى} — يُرجى التحويل لقسم الموارد البشرية".
4. فحوصات نهائية: لا placeholder، لا مراجع في السيرة، فئة الإيميل مسموحة، قيد 60 يومًا، الرصيد > 0.
5. **معاملة واحدة:** إدراج `applications` + `ledger(debit, -1)` + تحديث `company_contact_log` → ثم الإرسال SMTP من صندوق العميل (smtp.gmail.com:587 STARTTLS، اسم المرسل = اسم العميل) → عند النجاح commit مع message_id؛ عند الفشل rollback وإعادة جدولة (1 دقيقة، 5، 15، ثم الغد) وتسجيل الخطأ.
6. أخطاء المصادقة (535) → `mail_link_status=broken` + إشعار العميل بخطوات إعادة الربط + إشعار أحمد.
7. الرسالة نص عادي (text/plain) + مرفق PDF (`CV - {الاسم} - {المسمى}.pdf`)، بلا روابط تتبع، بلا صور.

### 3.10 المولّد التركيبي للإيميل
مكتبة عبارات (ملف YAML في المستودع) بالعربية والإنجليزية: ≥ 20 افتتاحية، ≥ 15 جملة "لماذا أنا مناسب" تأخذ مهارتين متقاطعتين فعليتين `{skill_a}` `{skill_b}` ومسمى الوظيفة، ≥ 10 جمل عن المرفق، ≥ 10 خواتم، وتوقيع (الاسم، الهاتف، المدينة). الاختيار بـ `seed = hash(customer_id, company_id)`. فحص تشابه (rapidfuzz ratio) مع آخر 50 رسالة إلى نفس `apply_email` < 70%، وإلا إعادة توليد ببذرة مختلفة. الأسلوب: مهذب، قصير (80–130 كلمة)، بلا مبالغة، بلا ادعاءات غير موجودة في الملف.

### 3.11 قارئ الوارد (Inbox) — كل 60 دقيقة لكل عميل
- IMAP على صندوق الخدمة (imap.gmail.com:993)، الرسائل الجديدة فقط منذ آخر UID.
- **ارتداد:** من `mailer-daemon|postmaster` أو موضوع `Delivery Status Notification|Undeliverable` → استخراج العنوان الفاشل → `applications.bounced=true, counted=false` + `ledger(bounce_reversal, +1)` + `companies.careers_email_status='invalid'` + إعادة الفرصة لخطة اليوم/الغد.
- **رد شركة:** أي رسالة من نطاق شركة سبق الإرسال لها → إشعار العميل على تيليجرام (المرسل، الموضوع، أول 500 حرف، المرفقات كملفات) + `applications.reply_at`.
- **دعوة مقابلة:** كلمات `interview|مقابلة|schedule|موعد|assessment|اختبار` → `reply_kind='interview'` + زر تأكيد للعميل + إحصاء.
- **إيقاف من شركة:** رد يحتوي `unsubscribe|stop|إيقاف|لا ترسلوا` → `companies.suppressed=true`.
- الرسائل التسويقية العامة تُتجاهل.

### 3.12 المحفظة والضمان (billing)
- منتجات ابتدائية (الأسعار مؤقتة حتى يقرر أحمد): `CR100` رصيد 100، `CR300` رصيد 300، `SUB30` اشتراك 30 يومًا 510 تقديمًا + سيرة، `PKG_SUB_CV` (نفس SUB30 مع سيرة بلغتين)، `CV1` سيرة مستقلة.
- شراء = `orders` + `ledger(grant)`. الاشتراك = `subscriptions` أيضًا.
- يوميًا 06:00: لكل اشتراك انتهى `end_date`: إن `sent_counted ≥ 510` → إغلاق. وإلا وإن `extended_days = 0` → `extended_days=2`, `end_date += 2` وإشعار داخلي فقط. وإلا → `shortfall = 510 − sent_counted − customer_caused_days×17`, `compensation = shortfall × price/510` → `ledger(compensation)` + رسالة لأحمد بزر "تم التحويل" + رسالة للعميل بنبرة مسار ("أكملنا لك… وهذا حقك يرجع لك").
- الرصيد المشترى: لا ينتهي؛ عند 20% رسالة للعميل بأسلوب مسار تعرض الشحن؛ عند 0 توقف التقديم واستمرار الاكتشاف.
- التقديم المرتد لا يُحتسب (bounce_reversal).

### 3.13 التقارير والتنبيهات
- **تقرير العميل 19:00** (تيليجرام، نبرة مسار): عدد اليوم، جدول (الشركة، المسمى، المدينة، "أُرسل من بريدك 10:42")، الرصيد/التقدم الشهري بلا ذكر سقوف، قسم "استبعدنا لك" مع أزرار، أزرار 👎/🎉 لكل تقديم، الردود التي وصلت.
- **الأدمن (فوري عند الانحراف فقط):** مصدر ينتج 0 لأكثر من 3 ساعات مع معدل معتاد > 2/ساعة؛ عميل تحت 12؛ بريد عميل مفصول؛ ارتداد يومي > 3% أو شكوى؛ اشتراك دخل التمديد/التعويض؛ فشل Sender متكرر.
- **لوحة أرقام في بوت الأدمن:** العملاء النشطون، الرصيد الإجمالي، المرسَل اليوم/الشهر، معدل المقابلات لكل مصدر وطبقة، أفضل الشركات استجابة، الشركات الموقوفة، العملاء في خطر تعويض والمبلغ المتوقع، طاقة كل عائلة (وظائف/أسبوع، شركات بإيميل).

---

## 4. تفاصيل تكميلية

### 4.1 تسخين صندوق البريد الجديد
اليومان 1–2: 6/يوم، 3–5: 12، 6–9: 16، 10+: 20–22. المجموع في 30 يومًا ≈ 570 ≥ 510. يبدأ العدّ من تاريخ ربط البريد. إن طلب Gmail تأكيد الهاتف يتوقف الإرسال لهذا العميل مع رسالة له بالخطوة.

### 4.2 قوالب السيرة
4 قوالب HTML بصرية مختلفة (نفس المحتوى، تنسيق/خط/ترتيب أقسام مختلف)، تُوزَّع `template_id = customer_id % 4`. قسم المراجع يُحذف دائمًا ويُستبدل بسطر "المراجع متاحة عند الطلب / References available upon request".

### 4.3 استخراج الملف المهيكل (profile)
1. عند رفع السيرة: تحويل PDF/Docx إلى نص (pdfminer/python-docx) وتخزينه.
2. طلب استخراج عبر جسر n8n → جلسة Claude (مهمة مجدولة "Profile Extractor" كل 30 دقيقة تعالج الطابور) تُرجع JSON بالمخطط في 3.3 (`profiles`) — قواعد صارمة: السنوات تُحسب من التواريخ؛ لا اختراع مهارات؛ المسميات 5–10 بأوزان؛ المدن من النص أو من اختيار العميل.
3. إن لم يُعالج خلال 60 دقيقة → استخراج حتمي (أنماط: سنوات من التواريخ، مسميات من عناوين الخبرات مطابقة بالمعجم، مهارات بالمعجم) و`source='heuristic'`.
4. رسالة التأكيد للعميل بأزرار (سنوات ✔/✖، المسميات أضف/احذف، المدن، شركات لا يريدها). التقديم يبدأ بعد التأكيد أو بعد 24 ساعة بلا رد (بالقيم المستخرجة).

### 4.4 المعجم (taxonomy)
- تحميل ESCO (occupations + skills، اللغتان ar/en) مرة واحدة في `titles/skills` (ملف CSV في المستودع؛ التنزيل من موقع ESCO الرسمي).
- طبقة محلية: ملف YAML `taxonomy_local.yaml` بالعائلات (مثال: `chem_process`: مهندس كيميائي، مهندس عمليات، مهندس إنتاج، مهندس دعم فني، مهندس تحسين مستمر…؛ `quality`: مهندس جودة، QA/QC، مفتش جودة، مهندس ضبط جودة…؛ `hse`؛ `maintenance_ops`؛ `coatings`؛ `project_controls` …) مع aliases بالإملاءات الشائعة (كميائي/كيميائي).
- المسمى غير المعروف → `tagging_queue` → مهمة Claude اليومية "Taxonomy Tagger" تُرجع لكل مسمى: family, canonical (موجود أو جديد), aliases.

### 4.5 سجل المصادر والجامع (discovery)
أنواع المصادر وكيفية القراءة (تحقق من كل نقطة عند إضافتها وسجّل `terms_note`):
- **ats_greenhouse:** `https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true`
- **ats_lever:** `https://api.lever.co/v0/postings/{company}?mode=json`
- **ats_smartrecruiters:** `https://api.smartrecruiters.com/v1/companies/{companyIdentifier}/postings`
- **ats_workable:** `https://apply.workable.com/api/v3/accounts/{subdomain}/jobs` (POST) أو widget v1
- **ats_ashby:** `https://api.ashbyhq.com/posting-api/job-board/{name}`
- **ats_recruitee:** `https://{company}.recruitee.com/api/offers/`
- **ats_teamtailor:** `https://{company}.teamtailor.com/jobs.rss` أو JSON
- **ats_bamboohr:** `https://{sub}.bamboohr.com/careers/list`
- **ats_workday:** `https://{tenant}.wd{n}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs` (POST JSON بـ `limit/offset/searchText`) — شائع لدى الشركات الكبرى في السعودية.
- **ats_successfactors / ats_oracle:** صفحات Careers لها غالبًا RSS أو JSON عام؛ يُحدَّد لكل شركة عند إضافتها.
- **sitemap_jsonld:** `sitemap.xml` للوظائف (فرق الروابط الجديدة بـ lastmod) ثم قراءة `<script type="application/ld+json">` بنوع `JobPosting` من الصفحة الجديدة فقط. احترام `robots.txt`، User-Agent واضح `MasarJobsBot/1.0 (+contact)`، ≤ 1 طلب/ثانية لكل نطاق.
- **rss:** Google Alerts (يُنشئها أحمد من حسابه، RSS)، وأي موقع توظيف خليجي يقدم RSS رسميًا.
- **api مجانية للشركاء (بعد التحقق من الشروط):** Careerjet، Jooble — تُضاف فقط إن كانت مجانية بلا رسوم لكل طلب.
- **alert_mail:** اختياري: تنبيهات وظائف يحوّلها العميل إلى صندوق مجاني `masar.alerts+{id}@gmail.com` (Gmail plus addressing) — تُستخرج منها الشركات والمسميات لتغذية الطبقة C، ولا تُعد الوظيفة نفسها للتقديم إلا إن كان لها إيميل.
- **ممنوع:** LinkedIn/Bayt/Indeed/Glassdoor/Naukrigulf/GulfTalent/Sabbar بالكشط. يمكن قراءة صفحة وظيفة واحدة فقط إذا كانت تحمل JSON-LD عامًا ويسمح robots.txt، وإلا لا.
- **قائمة الشركات الابتدائية (يُحدَّد نظام كل منها في المرحلة 2):** أرامكو، SATORP، YASREF، ساسرف، سابك، معادن، سبكيم، التصنيع الوطنية (تصنيع)، المتقدمة للبتروكيماويات، بترورابغ، مرافق، أكوا باور، نيوم، البحر الأحمر الدولية، الشركة السعودية للكهرباء، المياه الوطنية، شركات الدهانات (الجزيرة، ناشيونال، هيمبل، جوتن السعودية، سيجما)، الزجاج (UFG، Obeikan)، الأنابيب (المنيف، Future Pipe، Amiantit، الأنابيب السعودية، Perma-Pipe)، الأسمنت (اليمامة، السعودية، الجنوبية)، الأغذية والمشروبات (المراعي، صافولا، بيبسي السعودية، كوكاكولا)، الأدوية (SPIMACO، جمجوم)، المقاولون والاستشاريون (Worley، KBR، Jacobs، Fluor، Bechtel، Hill، Parsons، AECOM، Larsen & Toubro)، الفنادق (Accor) للصيانة، الطيران (طيبة لتشغيل المطارات)، تشغيل وصيانة (Nesma، Almabani، Alfanar، Al Yamama، Initial Saudi)، الوكالات (Eram Talent، Elbait، QS Quest، Hire Fellows، JVI) بدرجة source_quality 0.3.

### 4.6 صحة المصادر
كل مصدر: `avg_per_day` متحرك 7 أيام. إن `last_count=0` لثلاث دورات متتالية و`avg_per_day > 2` → تنبيه؛ 3 أخطاء متتالية → `enabled=false` + تنبيه. الجلسة الأسبوعية "Source Curator" تصلح المعطّل وتضيف 30–50 شركة.

### 4.7 تقدير الطاقة (داخلي فقط)
لكل عائلة: وظائف A/B بإيميل خلال 30 يومًا ÷ 30 = عرض يومي؛ عدد الشركات بإيميل صالح ووظّفت العائلة خلال 90 يومًا × 0.43 = سعة C اليومية الإجمالية؛ العملاء النشطون في العائلة × 17 = الطلب. النسبة تظهر في لوحة الأدمن مع توصية "أضف شركات للعائلة X".

### 4.8 تصنيف إيميلات الشركات
- `careers`: يبدأ بـ careers|career|hr|recruit|recruitment|jobs|talent|hiring|vacancies|cv|resume|employment|توظيف.
- `posted`: أي إيميل ورد في نص إعلان وظيفة.
- `generic`: info|contact|admin|office|mail|hello|enquiry|inquiry.
- `banned`: sales|support|billing|noreply|no-reply|marketing|finance|accounts|press|media|legal.
- `generic` يُستخدم فقط في C2 بشروطه، وموضوع رسالة يسهّل التحويل، ويُقاس معدل الرد/الارتداد لهذه الفئة كل 60 يومًا؛ إن كان الرد 0 والارتداد > 5% يُخفَّض سقفها.

### 4.9 الخصوصية والأمان
- نص الموافقة في التسجيل (بنبرة مسار) يشمل: التقديم نيابة عنه من صندوق الخدمة، قراءة وارد صندوق الخدمة لإيصال الردود، الاحتفاظ بالبيانات 90 يومًا بعد الانتهاء، الحذف بزر.
- كلمات مرور التطبيق مشفرة (Fernet/libsodium) بمفتاح في `.env` فقط، لا في القاعدة ولا Git. النسخ الاحتياطي مشفر.
- حذف العميل: حذف الملف، السير، كلمة المرور، وإيقاف كل شيء فورًا؛ تبقى صفوف `applications` بلا محتوى شخصي (للتدقيق المالي) 12 شهرًا.
- لا مراجع في أي سيرة مُرسلة. لا مشاركة بيانات مع أي طرف غير أصحاب العمل.

### 4.10 منتج السيرة المستقل
تدفق في n8n: "أبغى سيرة ذاتية" → دفع (يسجله أحمد كطلب `CV1`) → رفع سيرة أو أسئلة موجهة (الاسم، التعليم، الخبرات بتواريخها، المهارات، اللغة المطلوبة) → استخراج/بناء بجلسة Claude (نفس مكوّن التنويعات) → PDF بقالب من القوالب الأربعة → إرسال على تيليجرام مع جولة تعديل واحدة. للمشتركين شهريًا: تُرسل تلقائيًا بعد تأكيد الملف كهدية.

---

## 5. ما يجب ألا يُفعل (قائمة تحقق للمنفّذ)
- لا كشط لمواقع التوظيف. لا حسابات نيابة عن أحد. لا CAPTCHA.
- لا API مدفوعة لكل عملية (ولا "مجانية" بحد ثم رسوم).
- لا تخزين أسرار في المحادثة أو Git.
- لا إرسال خارج النافذة، لا sales@/support@، لا مراجع، لا تكرار.
- لا استخدام Claude في المسار الساخن (لكل وظيفة/إيميل).
- لا حذف النظام القديم قبل التفوق 3 أيام.
- لا ذكر سقوف أو تقديرات للعميل.

---

## 6. المرحلة 0 — الإصلاحات الفورية في النظام الحالي (تُنفَّذ أولًا، خلال يومين)

كلها في n8n و/أو برومبتات الشاردات، بلا خادم:

1. **مفتاح التكرار + منع التكرار 60 يومًا** — في `Y3mccwWalUtkkjav`: أضف قبل `Quality Check` عقدة Data Table (Job Applications Tracker) تجلب صفوف نفس `customerId` خلال آخر 60 يومًا، ثم في الكود: `company_key` و`title_key` بالتطبيع في 3.4؛ إن وُجد صف بنفس (company_key, title_key) → `qualityPassed=false, reason='duplicate_60d'`. أيضًا حدّث برومبتات الشاردات الثلاث: التكرار يُحسب بـ(شركة مطبَّعة + مسمى مطبَّع) لا بالرابط، وروابط `/mobile/` تُطبَّع.
2. **الاستبعاد القاطع** — في برومبتات الشاردات الثلاث (الخطوة 2-ب): استبعد أي وظيفة: سنوات مطلوبة > خبرة العميل + 2؛ Manager/Director/Head/رئيس/مدير لمن خبرته < 6؛ "Saudi national" لغير السعودي؛ تخصص أكاديمي مشترط مختلف؛ عائلة مهنية مختلفة (برمجيات/كهرباء/مدني لمهندس كيميائي). ومرّر في حمولة الويبهوك `customerYears`, `customerNationality`, `jobYearsReq`, `jobSeniority` ليتحقق منها الكود ثانيةً.
3. **حذف المراجع** — في `Quality Check`: قبل بناء السيرة، احذف من `cv` أي قسم مفاتيحه references/المراجع، وأي سطر في bullets/summary يحتوي رقم هاتف بنمط `\+?9665\d{8}` أو إيميل ليس إيميل العميل. أضف سطرًا ثابتًا "References available upon request".
4. **نافذة الإرسال** — في `Y3mccwWalUtkkjav`: بعد `Quality OK?` أضف عقدة كود تحسب: إن كان الوقت الحالي (Asia/Riyadh) خارج الأحد–الخميس 08:00–16:30 → احسب أقرب بداية نافذة وأضف عقدة Wait (Resume: At Specified Time) قبل الإرسال. غيّر جدولة الشاردات إلى الأحد–الخميس 05:00–13:30 UTC فقط: `20 5-13/2 * * 0-4`، `20 6-12/2 * * 0-4`، `50 5-13/2 * * 0-4`.
5. **تصنيف الإيميلات** — في `Quality Check`: `banned` → رفض؛ `generic` مسموح فقط مع موضوع يبدأ بـ"طلب توظيف لوظيفة … — يُرجى التحويل لقسم الموارد البشرية" وبحد 3/يوم للعميل. وفي برومبت "Company Email Finder": الأولوية careers@/hr@/recruitment@ ثم الإيميل المذكور في الإعلان، وsales@/support@ ممنوعة.
6. **الـCC تبقى** كما هي في النظام القديم.
7. اختبار: فعّل اشتراك أحمد التجريبي، راقب تشغيلتين، تأكد من صفر تكرار، صفر وظائف غير مؤهلة، صفر مراجع، والإرسال داخل النافذة. أبلغ أحمد.

---

## 7. تعديلات التسجيل (n8n Onboarding Router `8TCvk5BsrMPosr9q`) — المرحلة 5
تُضاف كخطوات في state machine الحالي، بنبرة مسار:
1. **الموافقة** (بعد الترحيب وقبل رفع السيرة): نص قصير + زر "موافق"، يُسجَّل `consent_at` عبر Core API.
2. **صندوق بريد الخدمة** (بعد تأكيد الملف): شرح مصور: أنشئ Gmail جديدًا باسمك الحقيقي (مثال firstname.lastname.career@gmail.com) → فعّل التحقق بخطوتين → أنشئ "كلمة مرور تطبيق" → الصقها هنا. البوت يرسلها لـCore (`POST /customers/{id}/mail-link`) الذي يختبر SMTP+IMAP فورًا ويرسل رسالة تجريبية من الصندوق إلى نفسه، ثم يحذف الرسالة من ذاكرة n8n. رسالة نجاح. خيار: "تبغى نحوّل الردود لبريدك الشخصي؟" → خطوات تفعيل التحويل في Gmail (يقوم بها العميل).
3. **تأكيد الملف المهيكل** بأزرار (4.3).
4. **اختيار نوع المساعدة** (بصياغة غير تجارية): "شهر كامل" / "عدد محدد من الفرص" / "سيرة ذاتية فقط" → يُسجَّل كطلب معلّق حتى يعتمد أحمد الدفع من بوت الأدمن (كما الآن).
5. **بعد التفعيل:** رسالة "بدأنا"، وتقرير 19:00 يوميًا.
6. **أزرار دائمة:** حالتي، جتني مقابلة، غير مناسب (على كل تقديم)، تواصل معنا، أوقف مؤقتًا، احذف بياناتي.

---

## 8. مهام Claude المجدولة بعد v5 (تُنشأ في المرحلة 3–5، كلها عبر جسر n8n)
| المهمة | الجدولة | ما تفعله |
|---|---|---|
| Profile Extractor | كل 30 دقيقة | تقرأ طابور الاستخراج من Core (عبر n8n)، تُرجع JSON الملف |
| CV Variant Generator | أسبوعيًا + عند تأكيد ملف جديد | كما الآن، تكتب عبر Core |
| Taxonomy Tagger | يوميًا 04:00 | تُوسم `tagging_queue` دفعة واحدة |
| Source Curator | أسبوعيًا الجمعة | تصلح المصادر المعطّلة، تضيف 30–50 شركة (نظام التوظيف + careers@)، تحدّث `terms_note` |
| Daily Audit | يوميًا 20:00 | عينة 20 تقديمًا: أهلية، جودة النص، تكرار؛ تبلّغ أحمد بأي خرق |
| Build Continuation (مؤقتة أثناء البناء) | يوميًا | تكمل خطة البناء من `PLAN.md` في المستودع وتبلّغ أحمد |

---

## 9. خطة التنفيذ (بالترتيب، مع معايير القبول)

### المرحلة 0 (اليوم 1–2): الإصلاحات الفورية — القسم 6.
**قبول:** تشغيلتان بلا تكرار/بلا غير مؤهل/بلا مراجع/داخل النافذة.

### المرحلة 1 (اليوم 2–5): البنية التحتية
1. **GitHub:** عبر متصفح أحمد أنشئ (أو استخدم) حساب GitHub، مستودع خاص `masar-core`، وتوكن Fine-grained (repo: contents, actions) — يُحفظ في `.env` محليًا على الخادم فقط. هيكل المستودع:
```
masar-core/
  PLAN.md  (حالة البناء: تم/التالي/يحتاج قرار)  docker-compose.yml  Caddyfile  .env.example
  deploy/bootstrap.sh  deploy/autodeploy.sh (cron كل 5 دقائق: git pull && docker compose up -d --build عند تغيّر HEAD)
  core/ (FastAPI app: identity, billing, profile, discovery, taxonomy, matching, planning, sending, inbox, reporting, bridge)
  core/collectors/ (greenhouse.py, lever.py, smartrecruiters.py, workable.py, ashby.py, recruitee.py, teamtailor.py, bamboohr.py, workday.py, sitemap_jsonld.py, rss.py, alert_mail.py)
  data/ (taxonomy_local.yaml, esco/*.csv, phrases_ar.yaml, phrases_en.yaml, cv_templates/1..4.html, cities.yaml, dictionaries/*.yaml, companies_seed.csv)
  migrations/ (alembic)  tests/  scripts/
```
2. **الخادم:** عبر متصفح أحمد: حساب Hetzner Cloud (أو بديل)، خادم CX22/CPX21 Ubuntu 24.04، Backups مفعّلة، مفتاح SSH يُولَّد على جهاز أحمد (`device_bash`: `ssh-keygen`) ويُرفع للمزود. ثم من جهاز أحمد: `ssh root@IP 'bash -s' < deploy/bootstrap.sh` (يثبّت Docker، يستنسخ المستودع بالتوكن، ينشئ `.env` من القالب، يشغّل compose، يضبط autodeploy cron وufw وfail2ban وunattended-upgrades).
3. **compose:** postgres:16 (volume)، gotenberg/gotenberg:8، core (uvicorn)، core-scheduler (APScheduler)، caddy (TLS تلقائي على نطاق فرعي مجاني مثل sslip.io أو نطاق أحمد إن اشترى) — أو cloudflared tunnel بدل caddy إن كانت المنافذ مقيدة.
4. **جسر n8n:** وركفلوهات: `Core - Call` (ويبهوك يستقبل {path, method, body} ويستدعي Core بتوكن ويعيد النتيجة — تستخدمه جلسات Claude)، `Core - Notify Admin` (يستقبل من Core ويرسل تيليجرام)، `Core - Notify Customer` (يستقبل {chatId, text, buttons, files} ويرسل عبر بوت مسار).
5. **مراقبة:** UptimeRobot (مجاني) على `/health`؛ نسخة Postgres يومية `pg_dump` مشفرة إلى تخزين المزود أو مستودع خاص.
**قبول:** `/health` يعمل عبر HTTPS، تحديث في GitHub يظهر على الخادم خلال 5 دقائق، Gotenberg يحوّل HTML تجريبيًا، n8n يستدعي Core بنجاح.

### المرحلة 2 (الأسبوع 2): الاكتشاف والمعجم
1. جداول taxonomy + تحميل ESCO + `taxonomy_local.yaml` بالعائلات الابتدائية (chem_process, quality, hse, maintenance_ops, coatings, production, project_controls, lab_chemistry, water_treatment, supply_chain, admin, sales_excluded).
2. الجامع لكل نوع مصدر + المطبّع + مفتاح التكرار + companies.
3. Source Curator (جلسة Claude) لأول 60 شركة من 4.5: تحديد النظام والرابط والتحقق يدويًا من كل نقطة.
4. تشغيل الجامع كل 30 دقيقة، وقياس `metrics_hourly`.
5. استيراد الوظائف/الشركات الحالية من Data Tables (بعد التطبيع) كبذرة.
**قبول:** ≥ 60 شركة بمصدر مباشر يعمل؛ ≥ 200 وظيفة جديدة مصنّفة أسبوعيًا للعائلات المستهدفة؛ تكرار < 1%؛ عينة 50 وظيفة يدوية: الحقول المستخرجة صحيحة ≥ 90%.

### المرحلة 3 (الأسبوع 3): المطابقة والمحفظة والملف
1. profiles + استخراج (Profile Extractor عبر الجسر + heuristic) + رسالة التأكيد بالأزرار.
2. الاستبعاد القاطع + الدرجة + الطبقات + opportunities.
3. billing: products, orders, ledger, subscriptions، وواجهات لبوت الأدمن (عميل جديد = order + grant).
4. تشغيل بالتوازي مع القديم على أحمد: مقارنة يومية (فريدة/مستبعدة/A/B) وتقرير لأحمد.
**قبول:** لأحمد: ≥ 17 فرصة/يوم مخططة من A/B/C بدرجات مفسَّرة؛ دقة عينة 50 فرصة ≥ 90%؛ دفتر الرصيد يوازن ريالًا بريال.

### المرحلة 4 (الأسبوع 4): الإرسال والوارد
1. mail-link (SMTP/IMAP test + تشفير)، Sender، المولّد التركيبي، قوالب السيرة الأربعة، تنويعات السيرة عبر Core.
2. Inbox reader (ارتداد/رد/مقابلة/إيقاف).
3. أحمد ينشئ صندوق الخدمة التجريبي ويعطي كلمة مرور التطبيق (تُلصق في الخادم لا في المحادثة)؛ أول أسبوع الإرسال إلى عناوين أحمد فقط (وضع `DRY_RUN_TO=` في `.env`) ثم إرسال حقيقي بتسخين.
4. بعد تفوق الجديد 3 أيام متتالية (عدد + دقة + صفر أخطاء): تعطيل الشاردات الثلاث ومهمة الاكتشاف ومزامنة الرغبات والواتشدوجات (`update_trigger enabled=false` ثم حذف بعد أسبوع)، وتحويل `Y3mccwWalUtkkjav` إلى غير نشط.
**قبول:** فشل إرسال < 1%؛ الارتداد يُكتشف خلال ساعة ويُعوَّض؛ 17–22 إيميلًا يوميًا لأحمد أسبوعًا كاملًا داخل النافذة؛ صفر تكرار.

### المرحلة 5 (الأسبوع 5): التقارير والتسجيل
1. تقرير 19:00 + أزرار 👎/🎉 + "استبعدنا لك" + الردود.
2. تعديلات Onboarding (القسم 7) + الموافقة + ربط البريد + تأكيد الملف + أنواع المساعدة.
3. لوحة أرقام الأدمن + تنبيهات الانحراف + الضمان/التعويض (تمديد يومين + حساب).
4. مهام Claude الدائمة (القسم 8) وحذف القديمة.
**قبول:** عميل تجريبي ثانٍ (سيرة مختلفة العائلة، يمكن أن تكون تجريبية من أحمد) يمر بالتسجيل كاملًا حتى أول تقديم دون تدخل يدوي.

### المرحلة 6 (الأسبوع 6): المنتجات والإطلاق
1. منتج السيرة المستقل (4.10) + الباقات في الكتالوج + الأسعار من أحمد.
2. نص شروط الاشتراك القصير (الضمان، أيام الانقطاع بسبب العميل، الخصوصية).
3. وثائق التشغيل: `RUNBOOK.md` (ماذا تفعل عند كل تنبيه)، `PLAN.md` نهائي.
4. أول 10 عملاء حقيقيين بمراقبة يومية من Daily Audit.
**قبول:** 10 عملاء × 7 أيام: كل واحد ≥ 17/يوم (بعد التسخين)، صفر تعويض، صفر شكاوى spam، معدل مقابلات مقاس.

---

## 10. متى تسأل أحمد (وإلا استمر)
اسأل فقط عندما: تحتاج دفعًا (إنشاء الخادم)، أو كلمة مرور تطبيق/توكن يجب أن يلصقها هو، أو موافقة على إطفاء النظام القديم، أو قرار أسعار، أو صلاحية على جهازه/متصفحه تُرفض. كل ما عدا ذلك: قرر وفق هذا الملف، نفّذ، وأبلغ.

**قائمة ما يُطلب من أحمد (اطلبها دفعة واحدة في البداية):**
1. تأكيد مزود الخادم وطريقة الدفع (Hetzner مقترح).
2. حساب GitHub (أو الإذن بإنشائه من متصفحه) وتوكن.
3. صندوق Gmail تجريبي جديد + كلمة مرور تطبيق (تُلصق في الخادم عبر أمر يُعطى له، لا في المحادثة).
4. أسعار مبدئية للمنتجات (أو الإبقاء على المؤقتة).
5. قائمة شركات يعرفها في مجاله (اختياري).

---

## 11. ملاحظات ختامية للمنفّذ
- ابدأ كل جلسة بقراءة `PLAN.md` في المستودع (إن وُجد) وهذا الملف، ثم `list_triggers` و`search_workflows` لتعرف الحالة الفعلية؛ لا تفترض.
- كل تغيير في n8n: احفظ نسخة (إصدار) قبله؛ كل تغيير في الكود: commit صغير برسالة واضحة.
- اختبر بالأرقام لا بالانطباع: بعد كل مرحلة، شغّل `scripts/report.py` الذي يطبع المقاييس في معايير القبول وأرسلها لأحمد.
- الفلسفة التي تحكم كل قرار غامض: **الصدق مع العميل، السمعة قبل العدد، لا رسوم لكل عملية، لا تجاوز لأي موقع.**
