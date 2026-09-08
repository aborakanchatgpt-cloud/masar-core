# PLAN.md — حالة البناء الحيّة وبروتوكول العمل الذاتي (اقرأه كاملًا قبل أي عمل)

> آخر تحديث: 7 سبتمبر 2026 19:15 UTC — كاتبه: المراجع (Fable، مراجعة B2 #2). هذا الملف هو **مصدر الحقيقة الوحيد** لحالة المشروع. المرجع التفصيلي للتصميم: `docs/EXECUTION_GUIDE.md`.

## 0. البروتوكول (لكل جلسة مجدولة)

يوجد دوران يعملان بالتناوب بلا تدخل بشري:

| الدور | من | متى | ماذا يفعل |
|---|---|---|---|
| **EXECUTOR** (المنفّذ) | جلسة مجدولة بنموذج Sonnet | **كل ساعة** (الدقيقة :05) | يقرأ هذا الملف و`REVIEW.md`، يطبّق أولًا أي تصحيحات مفتوحة من المراجع، ثم يأخذ **أول بند `TODO`/`WIP` في القسم 2** وينفّذه كاملًا مع اختباره ويرفع الملفات ويحدّث القسمين 2 و3. **لا يتوقف بعد بند واحد**: يجدّد القفل ويأخذ البند التالي، ويستمر حتى تنتهي البنود أو يصل إلى `BLOCKED` أو تنتهي الجلسة. |
| **REVIEWER** (المراجع) | جلسة مجدولة بالنموذج الافتراضي (Fable) | **كل ساعتين** (الدقيقة :35) | يتحقق من كل بند `DONE` لم يُراجع بعد مقابل معيار قبوله فعليًا (فحص حي عبر n8n → Core، قراءة الكود من GitHub). إن كان المنفّذ يعمل (قفل حي) لا يلمس `PLAN.md` بل يكتب تصحيحاته المرقّمة في `REVIEW.md` فقط؛ وإن لم يكن هناك قفل يحدّث `PLAN.md` مباشرة (إعادة بند إلى `TODO` مع التصحيحات، ترتيب البنود، القسم 1). يرسل تقريرًا واحدًا لأحمد مساءً. |

**قواعد ملزمة للدورين:**
1. **الوصول:** لا SSH ولا توكنات في أي جلسة. الكود يُرفع عبر وركفلو n8n `GitHub - Commit File` (القسم 4). الأوامر على الخادم عبر وركفلو n8n `Core - Call` (`v7xKPShYWHJwqxvs`) إلى نقاط `/admin/*`. الخادم يسحب كل commit خلال دقيقتين ويعيد البناء ويطبّق الترحيلات تلقائيًا.
2. **قفل:** قبل البدء اكتب في القسم 5 سطر `LOCK: <الدور> <وقت UTC>`; وعند الانتهاء احذفه. القفل صالح **55 دقيقة** من آخر تجديد: المنفّذ **يجدّد وقت القفل عند بدء كل بند** (رفع `PLAN.md`). منفّذ يجد قفل `EXECUTOR` عمره < 55 دقيقة يتوقف فورًا (زميله يعمل)؛ قفل أقدم من 55 دقيقة = جلسة ماتت، احذفه وتابع. المراجع الذي يجد قفل `EXECUTOR` حيًّا لا يكتب في `PLAN.md` إطلاقًا (يكتب في `REVIEW.md` فقط).
3. **حجم التشغيلة:** بند تلو بند بلا توقف. قبل كل بند: جدّد القفل، واقرأ `REVIEW.md` وطبّق أي تصحيح حالته `OPEN` أولًا (ثم اكتب في القسم 3: `applied R<n>`). إن لم يكتمل بند، اكتب حالته `WIP` مع ما أُنجز بدقة وما تبقى، حتى تكمله التشغيلة التالية من حيث توقفت. ملف `REVIEW.md`: يكتبه المراجع فقط (سطور `R<n> — OPEN|APPLIED — البند — التصحيح بدقة`)، والمنفّذ يقرأه ولا يعدّله.
4. **ممنوع:** حذف بيانات؛ لمس وركفلوهات n8n القديمة غير المذكورة هنا؛ تعديل المهام المجدولة؛ إطفاء النظام القديم؛ أي كشط لمواقع توظيف؛ أي API برسوم لكل عملية؛ عرض سقوف أو تقديرات للعملاء.
5. **متى تُبلغ أحمد (عبر `ZiVlsBibVps3pxGB` body `{"message": "..."}`):** سر يجب أن يدخله، دفع، إطفاء القديم، أو فشل متكرر ثلاث مرات لنفس البند. **كل رسالة تحتاج فعلًا منه تبدأ حرفيًا بـ `نحتاجك فورا — `** ثم ماذا يفعل بالضبط (خطوات مرقّمة قصيرة، رابط إن وُجد، وأين يكتب السر: في n8n أو في `.env` على الخادم — لا في الشات أبدًا). لا ترسل الرسالة نفسها مرتين؛ إن كانت مُرسلة سابقًا (مسجّلة في القسم 3) فلا تكررها إلا بعد 24 ساعة. غير ذلك: المراجع يرسل تقريرًا واحدًا مساءً يبدأ بـ `تقرير مسار اليومي`.
6. **الاختبار قبل `DONE`:** كل بند له "معيار قبول" رقمي؛ لا يُعلَّم `DONE` إلا بدليل (مخرجات فحص/استعلام) يُكتب في القسم 3.
7. **الأمان عند الكسر:** إن أظهر `/health` خطأً بعد commit، المراجع يعيد آخر نسخة سليمة من الملف المعني عبر `GitHub - Commit File` (محتوى الإصدار السابق) ويعلّم البند `FAILED` مع السبب.

## 1. الحالة الآن (يحدّثها المراجع)

- ✅ المرحلة 0 (إصلاحات النظام القديم) و**المرحلة 1 (البنية)** مكتملتان ومُختبرتان: خادم Hetzner `masar-core-1` (IP `62.238.117.20`، CX23، Ubuntu 24.04، backups مفعّلة)، الرابط `https://62.238.117.20.sslip.io` (HTTPS صالح عبر Caddy+sslip.io)، Postgres + Gotenberg + Core + core-scheduler تعمل، الترحيل `0001` مطبّق، cron: autodeploy كل دقيقتين + backup 03:00.
- ✅ n8n: اعتماد `Masar Core Admin Token` (Header Auth، id `JptuLYJ1gcUGVAz8`)، وركفلو `Core - Call` مربوط ومنشور، اختبار `/admin/ping` → `{"ok":true}`.
- ⚠️ المستودع `aborakanchatgpt-cloud/masar-core` **عام مؤقتًا** (لأن الخادم يسحب بلا توكن). يُعاد خاصًا بعد بند B1.
- ⚠️ **n8n Cloud تجريبي ينتهي ~10 سبتمبر 2026** (~905/1000 تنفيذ مستهلكة — اقتصد في تنفيذ الوركفلوهات: لا تكرر نداءات فحص بلا حاجة). البند B0 يحسم النقل.
- ✅ وركفلو `GitHub - Commit File` (`K65rLAVzarrtH4SV`) مربوط باعتماد GitHub ومنشور ومُختبر (commit `0590a8c`). الحلقة الذاتية مفعّلة: المنفّذ كل ساعة، المراجع كل ساعتين.
- ⚠️ **المهام المجدولة الجديدة لا تبدأ** (7/7 تشغيلات بقيت PENDING بلا أي نداء أداة؛ مهام النظام القديم تعمل). قرار المراجع 6 سبتمبر: التنفيذ يُدار من جلسة Cowork التفاعلية (Fable يراجع، Sonnet كوكيل فرعي ينفّذ) حتى يُحل الخلل؛ مهمتا Executor/Reviewer المجدولتان معطّلتان مؤقتًا.
- ⚠️ **n8n Cloud شبه منتهي** (تجربة 1000 تنفيذ؛ بقي القليل). أوقفنا الوركفلو `CjlFHF1JG3GzYLbz` (مراقب كل 15 دقيقة) ومهمتي Discovery وPipeline Watchdog القديمتين مؤقتًا لحماية الرصيد. **الجسر البديل:** Masar MCP bridge داخل Core (`/mcp/<token>`) — انظر القسم 4.
- ✅ **B0 منجز:** n8n ذاتي يعمل على `https://n8n.62.238.117.20.sslip.io` (HTTP 200 + TLS صالح، probe 22:19Z). أحمد أنشأ حساب المالك (7 سبتمبر). باقي: استيراد الوركفلوهات/الاعتمادات (دليل `docs/N8N_SELF_HOST.md`).
- ✅ **B1a منجز:** ops runner عبر ملفات (`/admin/ops` + `deploy/ops/run_queue.sh` ينفّذه cron المضيف كل دقيقتين): ps/logs/restart/up/migrate/backup-now/psql(قراءة)/sys/git/deploy-key/git-remote-ssh/commit… بدون docker.sock.
- ✅ **B1b منجز:** MCP bridge (`core/app/mcp_bridge.py`) بأدوات repo_read/repo_list/repo_write/ops/job/wait/core_call. أُضيف مفتاح النشر (Deploy Key) في GitHub بصلاحية قراءة/كتابة (read/write)، ونُفّذ `git-remote-ssh` وأعاد `ok` بتاريخ 2026-09-07 05:32Z؛ والموصّل المخصص "Masar Core" نشط الآن في Claude. باقي: جعل المستودع خاصًا بعد تأكيد أول commit عبر الجسر (B1).
- ⚠️ **B2 (الاكتشاف الحقيقي) لا يزال WIP بعد مراجعتين.** المراجعة الثانية (2026-09-07، `docs/reports/B2-review-2.md`) وجدت عيبين جديدين عاليي الأثر لم يُكتشَفا سابقًا فوق فشل معياري القبول الرقميين: (1) تكرار حقيقي ~55% (وليس 42.8% كما أُعلن) بسبب تعدّد مواقع لنفس `apply_url` داخل نفس الجلبة (Eram Talent/Hudson Manpower)؛ (2) تلوّث ~17% من `jobs_in_region`/`jobs_sa` بوظائف موقعها الحقيقي خارج الخليج (رجوع `extract_country_code()` لنص الوصف بلا شرط). `family_classified_pct_in_region` لا يزال 35.9% (الهدف ≥80%). الحكم: **REJECT** — يبقى B2 = WIP مع قائمة تصحيحات R10-R15 (انظر التقرير).
- 🎯 **الهدف الزمني:** جاهزية استقبال أول عميل حقيقي بحلول **18 سبتمبر 2026** (B0–B7). كل دور يذكر في سطر سجله إن كان الجدول على المسار أم متأخرًا ولماذا.

## 2. التالي (يأخذ المنفّذ أول TODO بالترتيب — لا يقفز)

### B0 — DONE (2026-09-06 22:19Z) — نقل n8n إلى الخادم
1. أضف خدمة `n8n` إلى `docker-compose.yml` (صورة `docker.n8n.io/n8nio/n8n:latest`، volume `n8n_data:/home/node/.n8n`، شبكة `masar_public`، متغيرات: `N8N_HOST=n8n.${MASAR_DOMAIN}`, `WEBHOOK_URL=https://n8n.${MASAR_DOMAIN}/`, `N8N_PROTOCOL=https`, `GENERIC_TIMEZONE=Asia/Riyadh`, `N8N_ENCRYPTION_KEY=${N8N_ENCRYPTION_KEY}`, `DB_TYPE=postgresdb` + إعدادات Postgres لقاعدة منفصلة `n8n` تُنشأ بترحيل/سكربت init)، وأضف في `Caddyfile` مضيفًا ثانيًا `n8n.{$MASAR_DOMAIN}` → `reverse_proxy n8n:5678`. أضف `N8N_ENCRYPTION_KEY` إلى `.env.example` ولّده في `bootstrap.sh` إن غاب.
2. معيار القبول: `https://n8n.62.238.117.20.sslip.io` يفتح صفحة إعداد المالك (Owner setup). **لا تنشئ الحساب** — أحمد ينشئه.
3. أبلغ أحمد بالخطوات: إنشاء حساب المالك، تصدير الوركفلوهات من Cloud (Settings → Download/Export) واستيرادها، إعادة إدخال اعتمادات Telegram/Gmail/Header Auth، ثم **اختبار اتصال موصّل n8n في تطبيق Claude بالنسخة الذاتية** (Settings → n8n MCP/API في n8n الذاتي). إن لم يتصل الموصّل: القرار المسجّل هو إبقاء أصغر خطة Cloud كجسر فقط ونقل البوتات الثقيلة ذاتيًا — أبلغ أحمد ولا تطفئ Cloud.

### B1 — WIP — نقاط الإدارة والعودة إلى مستودع خاص
**ما تبقى فقط (بعد أن يضيف أحمد مفتاح النشر والموصّل):** (1) `ops git-remote-ssh` والتحقق أن `git fetch` يعمل؛ (2) اختبار `repo_write` عبر الموصّل → commit+push من الخادم → إعادة نشر تلقائية (`ops/.deploy_needed`)؛ (3) جعل المستودع خاصًا ثم commit تجريبي للتأكد أن السحب ما زال يعمل؛ (4) تحديث القسم 4 هنا. البنود 1–3 الأصلية أدناه نُفّذت بتصميم أفضل (بلا docker.sock).
1. في Core أضف `/admin/ops` (POST، محمي بالتوكن) بأوامر من قائمة مسموحة فقط: `health`, `migrate`, `restart`, `logs` (آخر 200 سطر لخدمة محددة), `run-collector`, `seed-esco`, `backup-now`, `deploy-now`. التنفيذ عبر `subprocess` لسكربتات في `deploy/ops/*.sh` (كل أمر سكربت مستقل، لا تمرير نصوص حرة). الحاوية تحتاج وصولًا لسوكت Docker للأوامر restart/logs: أضف `/var/run/docker.sock:/var/run/docker.sock` لخدمة core فقط مع تحذير في التعليقات.
2. أضف `/admin/deploy-key` (POST): يولّد مفتاح ed25519 في `/opt/masar-core/.deploy_key` (مرة واحدة) ويعيد المفتاح العام؛ و`/admin/git-remote-ssh` يبدّل remote إلى `git@github.com:aborakanchatgpt-cloud/masar-core.git` مع `core.sshCommand` يشير للمفتاح.
3. معيار القبول: عبر `Core - Call`: `{"path":"/admin/ops","method":"POST","body":{"cmd":"health"}}` يعيد حالة الحاويات؛ `/admin/deploy-key` يعيد مفتاحًا عامًا. ثم أبلغ أحمد: أضف المفتاح كـ Deploy Key (قراءة) في GitHub ثم اجعل المستودع خاصًا؛ بعد تأكيده نفّذ `git-remote-ssh` وتحقق أن autodeploy ما زال يسحب (commit تجريبي).

### B2 — WIP — الاكتشاف الحقيقي (المرحلة 2 حسب الدليل §9)
**حكم المراجعة الثانية (2026-09-07، `docs/reports/B2-review-2.md`): REJECT — لا يُعلَّم DONE بعد.** الأدلة الحيّة: تكرار حقيقي ≈55% (بهوية `apply_url`، أعلى من 42.8% المُعلَن) بسبب تعدّد مواقع لنفس الإعلان داخل نفس الجلبة؛ تلوّث ≈17% من `jobs_in_region`/`jobs_sa` بوظائف خارج الخليج فعليًا (خلل رجوع لنص الوصف بلا شرط بـ`extract_country_code`)؛ `family_classified_pct_in_region` لا يزال 35.9% (الهدف ≥80%)؛ `sources_active`=55 (الهدف ≥60 شكليًا، لكن مقبول حسب معيار النتيجة: ≥200 وظيفة فريدة/يوم داخل النطاق متحقق بفارق كبير). البنية والامتثال سليمان.

**تصحيحات مطلوبة قبل إعادة الطرح للمراجعة (R10-R15 كاملة بالتقرير، أهمها):**
1. [حرج] إصلاح `dedup_key` في `core/app/discovery.py:run_round()` بحيث يعتمد `apply_url` وحده (بلا `location_text`) لمصادر ATS التي تضمن رابطًا واحدًا لكل وظيفة (greenhouse/lever/ashby/smartrecruiters/workable) — لمعالجة تكرار نفس الإعلان بعدة مواقع (تفصيل R10).
2. [حرج] تعديل `extract_country_code()` بـ`core/app/collectors/field_extractor.py` ليتوقف عن الرجوع لنص العنوان/الوصف حين يكون `location_text` معبّأً ويذكر دولة/مدينة محدَّدة (تفصيل R11).
3. [عالٍ] دمج توسعة `taxonomy_local.yaml` (مقتطف YAML جاهز بالتقرير) وتعديل `classify_family()` ليفرّق بين "غير مصنّف حقيقي" و"مستبعد عمدًا" (تفصيل R12).
4. [متوسط] إصلاح بقايا خلل الأقدمية (انحدار R2 جزئي، 6 صفوف) بإعطاء الأولوية لأقوى إشارة أقدمية موجودة بدل أول تطابق (تفصيل R13).
5. [متوسط] إعادة تعريف `dup_ratio_24h_in_region` بصيغة `apply_url`-محورية بعد إصلاح البند 1 (SQL كامل بالتقرير، تفصيل R14).

### B3 — WIP (منفَّذ محليًا، فحص حي جزئي 2026-09-08، بانتظار معيار الحمل الرقمي) — المطابقة والملف والمحفظة (المرحلة 3)
حسب الدليل §3.3، §3.6، §3.7، §3.12: جداول `customers/profiles/products/orders/ledger/subscriptions/opportunities/applications/feedback`، الاستبعاد القاطع، الدرجة، الطبقات A/B/C/C2/D، دفتر الرصيد بمعاملة واحدة مع الإدراج، وواجهات `/customers`, `/wallet`, `/plan/{customer}/today`. **معيار الحمل:** خطة يومية لـ 1,500 ملف × 3,000 وظيفة تكتمل في < 5 دقائق (قِسها بسكربت `scripts/bench_planner.py` ببيانات اصطناعية).

**مراجعة حيّة جزئية (2026-09-08، `docs/reports/B3B4-live-review.md`):** فحص السلامة الحي فقط ضمن نطاق مراجعة B3/B4 (وجود بيانات مطابقة/تخطيط عبر `psql`، استجابة نقطة التخطيط) — **نجح**. لم يُختبر بعد معيار القبول الرقمي الكامل (حمل `scripts/bench_planner.py` لـ1,500×3,000)، ولم تجرِ مراجعة كاملة مقابل كل معايير القسم أعلاه (تنفيذ B3 موثّق سابقًا في `docs/reports/B3-executor.md` دون مراجعة رسمية مقابلة). لا يُعلَّم DONE حتى تُجرى تلك المراجعة الكاملة.

### B4 — WIP (نُشر حيًّا وتحقّق منه مراجع حي 2026-09-08: ACCEPT-WITH-FIXES، بانتظار حل NEEDS-OWNER) — الإرسال والوارد (المرحلة 4)
حسب الدليل §3.9–§3.11 و§4.1: mail-link (SMTP/IMAP test + تشفير Fernet بمفتاح من `.env`)، `send_queue` بـ `SKIP LOCKED`، Sender بعمال متوازين (هدف 25,000 إيميل/يوم = 50/دقيقة في نافذة 8.5 ساعة، مع فواصل لكل عميل)، المولّد التركيبي، 4 قوالب CV، Inbox reader (1,500 صندوق/ساعة بـ 20 عاملًا). التسخين لكل صندوق جديد. **الاختبار على صندوق أحمد التجريبي فقط** (`DRY_RUN_TO` في `.env`) حتى يعتمد المراجع الجودة.

**مراجعة أوفلاين (2026-09-08، `docs/reports/B4-offline-review.md`): REJECT** — فجوتا بنية تحتية حاسمتان (لا `MAIL_FERNET_KEY` يصل للحاوية، لا خدمة `mailpit`) + صفر اختبارات B4 رغم توثيق داخلي يدّعي وجودها. **تصحيحات المنفّذ (2026-09-08، `docs/reports/B4-executor-fixes.md`، commit تالٍ لهذا السطر) طُبِّقت محليًا وكل بوابات الجودة (compileall/imports/pytest/migrations/compose config) خضراء**، لكن لم تُنشَر بعد على `masar-core-1` الحي ولم يعتمدها المراجع — يبقى B4 **WIP لا DONE** حتى: (1) نشر فعلي (commit عبر جسر MCP/n8n) + `docker compose up -d --build` يلتقط `mailpit`/`cv_data`/متغيّرات البريد الجديدة، (2) `alembic upgrade head` يطبّق `0006_b4_fixes` على قاعدة الإنتاج، (3) مراجع حيّ يتحقق من فحوصات §"فحوصات لا تزال تحتاج خادمًا حيًّا" بتقرير المراجعة الأوفلاين (mail-link حقيقي، `mailpit` يستقبل رسالة كاملة بمرفق PDF، معدّل تصريف تحت حمل، سلوك F1 عبر دورات autodeploy حقيقية متتالية).

**مراجعة حيّة (2026-09-08، `docs/reports/B3B4-live-review.md`): ACCEPT-WITH-FIXES** — النشر الحي تحقّق (v0.3.0، `0006_b4_fixes` مطبَّقة، `mailpit` صحّي)؛ تدفّق sink كامل عبر `/admin/mail/load-test` + `/admin/mail/send-now` نجح (≥10 رسائل بمرفق PDF في mailpit) مع idempotency مؤكَّدة (تشغيل ثانٍ بلا تكرار). وُجد بلوكر NEEDS-OWNER: `create_mail_link` يجري تحقّق SMTP/IMAP حقيقيًا بمعزل تام عن DRY_RUN/MAIL_SINK_SMTP، وmailpit لا يوفّر IMAP — يستحيل الوصول لـ`mail_links.status='ok'` وبالتالي اختبار المسار الحقيقي الكامل (بما فيه CC للعميل عبر mailpit وقارئ الوارد) دون حساب Gmail حقيقي أو نقطة تجاوز تطويرية جديدة؛ يبقى B4 **WIP لا DONE** حتى يُحل هذا. ملاحظتان إضافيتان: (أ) `core-scheduler` يظهر "unhealthy" في `docker ps` بسبب فحص HEALTHCHECK موروث من Dockerfile المشترك يستهدف منفذ HTTP لا تفتحه هذه الخدمة (غير ضار فعليًا، مُوثَّق كـMedium)؛ (ب) `sender.send_tick()` يتجاوز نافذة الإرسال (الأحد-الخميس 08:00-16:30 بتوقيت الرياض) بالكامل متى كان `MAIL_SINK_SMTP` مفعّلًا (وهو الافتراضي الحالي في compose) — ملاحظة تشغيلية للانتباه عند الانتقال للإنتاج الحقيقي.

### B2b — TODO (غير حاجز) — متابعات B2: توسعة التصنيف والمصادر
لا يحجب بقية البنود ولا يُعطى أولوية على B3+ إلا بعد إنهائها. عند التقاطه:
1. توسعة `data/taxonomy_local.yaml` بشكل دوري (Source Curator) اعتمادًا على `GET /admin/unclassified-sample` (وليس فقط دفعة R12 لمرة واحدة) حتى `family_classified_pct_in_region` (بعد تصحيح المقياس بـR12) ≥ 80%.
2. جامع جديد لمصادر سعودية مغلقة فعليًا (`sitemap_jsonld`/RSS لمواقع توظيف حكومية سعودية كطاقات/جدارة إن وُجد RSS رسمي، أو صفحات وظائف شركات سعودية كبرى بـJSON-LD مضمّن) بدل الاستمرار بإضافة شركات Greenhouse عالمية هامشية (معدّل نجاح الدفعات السابقة يتراجع بسرعة: 13→8→6→11 مرشّحًا أعطوا 5→1→0→1 مصادر ناجية فقط).

### B5 — TODO — التقارير والتسجيل (المرحلة 5)
تقرير 19:00، أزرار 👎/🎉، "استبعدنا لك"، تعديلات onboarding في n8n (الدليل §7)، الضمان/التعويض، لوحة الأدمن، مهام Claude الدائمة (الدليل §8) وحذف القديمة بعد التفوق 3 أيام.

### B6 — TODO — تصليب الحمل لـ 1,500 عميل
فهارس Postgres على (customer_id, planned_for), (posting_key), (company_id, week_start), (send_after, locked_until); `pool_size` مناسب؛ تقسيم IMAP على دفعات؛ حدود لكل نطاق مرسل؛ مراقبة CPU/RAM عبر `/admin/stats` وتنبيه عند > 70% لمدة 30 دقيقة (توصية ترقية إلى CX33)؛ اختبار حمل اصطناعي: 1,500 عميل × 17 = 25,500 صف في `send_queue` تُعالَج (بوضع dry-run) في < 6 ساعات.

### B7 — TODO — المنتجات والإطلاق (المرحلة 6)
الكتالوج والباقات (أسعار مؤقتة)، منتج السيرة المستقل، نص الشروط، `RUNBOOK.md`، أول 10 عملاء.

## 3. سجل التشغيلات (يضيف كل دور سطرًا: التاريخ/الدور/البند/النتيجة/الدليل)

- 2026-09-05 — REVIEWER (Fable, Cowork) — المرحلة 1 — DONE — `/health` و`/admin/ping` عبر n8n ناجحان؛ cron مثبّت؛ backups مفعّلة.
- 2026-09-06 21:13Z — REVIEWER (Fable, Cowork) — بروتوكول — المهام المجدولة الجديدة لا تبدأ (PENDING دائم، حتى مهمة اختبار بسطر واحد)؛ عُطّلت مؤقتًا، والتنفيذ يُدار من جلسة Cowork بوكلاء Sonnet.
- 2026-09-06 22:19Z — EXECUTOR (Sonnet subagent) + REVIEWER — B0 — DONE — n8n ذاتي: compose + Caddy + db-init + backup؛ إصلاحان من المراجع: (1) إزالة تمرير N8N_ENCRYPTION_KEY عبر البيئة (تعارض مع مفتاح volume)، (2) Caddy لا يرى Caddyfile الجديد (bind-mount inode) → autodeploy يعيد إنشاء حاوية caddy عند تغيّر الملف. الدليل: probe 200 + HTML n8n. الجدول على المسار.
- 2026-09-06 22:11Z — EXECUTOR (Sonnet subagent) — B1a — DONE — ops runner بالطابور: `/admin/ops logs caddy 80` → exit 0 وسجلات فعلية.
- 2026-09-07 05:05Z — EXECUTOR (Sonnet subagent) — B1b — DONE (ينتظر أحمد) — MCP bridge + deploy-key + commit من المضيف؛ `GET /admin/mcp-url` يعيد الرابط؛ `deploy-key` أعاد مفتاحًا عامًا. رُسل لأحمد طلب "نحتاجك فورا" (مفتاح النشر + الموصّل).
- 2026-09-06 17:30Z — REVIEWER (Fable, Cowork) — بروتوكول — تسريع الحلقة: منفّذ كل ساعة (بنود متتالية، قفل 55 دقيقة يُجدَّد لكل بند)، مراجع كل ساعتين عبر `REVIEW.md` عند وجود قفل حي؛ قاعدة رسائل `نحتاجك فورا — `؛ هدف زمني 18 سبتمبر.
- 2026-09-07 05:40Z — EXECUTOR (Sonnet subagent) — B1 — first commit via Masar MCP bridge (repo_write) — OK (job 53442b4a72b34846 exit_code 0، sha 70770d9f8e6974d49e72c9661c1bb22acc2505fd، تحقّق عبر raw.githubusercontent.com)
- 2026-09-07 19:15Z — REVIEWER (مراجعة مستقلة #2، Fable) — B2 — REJECT (يبقى WIP) — أدلة حيّة عبر `ops psql` مباشرة على الإنتاج + قراءة كود `discovery.py`/`field_extractor.py`: تكرار حقيقي ≈55% (بهوية apply_url؛ 1432 رابطًا لديه >1 dedup_key، 3528/3804 صفًا متأثرة) أسوأ من الرقم المُعلَن 42.8%؛ تلوّث ≈17.1% من jobs_in_region/jobs_sa بوظائف خارج الخليج فعليًا (651/3804 صفًا، مثال: Elastic بموقع "Romania" وcountry_code="OM")؛ family_classified 35.9%/80%. عيب seniority R2 مُصلَح غالبًا بالكود (حدود كلمة صريحة مؤكَّدة) مع أثر متبقٍّ ضئيل (0.16%). sources_active=55/60 مقبول حسب معيار النتيجة (≥200 وظيفة/يوم متحقق بفارق كبير). التقرير الكامل + قائمة R10-R15 وSQL الموصى به: `docs/reports/B2-review-2.md` (commit `a3461f2`). أُضيف B2b (غير حاجز) بعد B4 لمتابعات التصنيف/المصادر.
- 2026-09-08 — REVIEWER (Fable، مراجعة أوفلاين بلا وصول خادم حي) — B4 — REJECT — قراءة كود كاملة (لا `ops`/n8n، الخادم الحي غير متاح لهذه الجلسة): `python -m compileall`/سلسلة ترحيل Postgres 16 محلية كاملة (0001→0005) خضراء، لكن `docker-compose.yml` لا يمرّر `MAIL_FERNET_KEY` لأي حاوية (كل تشفير/فك تشفير سرّ بريد يفشل دومًا بالإنتاج) ولا يعرّف خدمة `mailpit` إطلاقًا (لا مسار sink قابل للتفعيل)؛ صفر اختبارات B4 رغم أن `pacing.py`/`inbox.py` يذكران صراحة أسماء ملفات اختبار غير موجودة؛ فجوة idempotency حقيقية (F1: انهيار عملية بين نجاح SMTP و`_mark_success` يترك صفًّا عالقًا قابلًا لإرسال مزدوج)؛ `opportunities.status='sent'` يُضبط وقت البناء لا الإرسال الفعلي؛ حارس تحميل راوترات B4 بـmain.py يلتقط `ImportError` فقط (SyntaxError واحد أسقط الخدمة كاملة فعليًا سابقًا، مُوثَّق بـgit log). التقرير الكامل: `docs/reports/B4-offline-review.md`.
- 2026-09-08 — EXECUTOR (Sonnet subagent) — B4 (تصحيحات المراجعة الأوفلاين) — WIP (تصحيحات محلية كاملة، بانتظار نشر حي) — طبّق كل Critical/High وMedium بالتقرير + Low 2/3 (تُرك Low 1 async-blocking لمعالجة موحّدة لاحقًا مع نفس النمط بـcustomers_api.py): `MAIL_FERNET_KEY`/`MAIL_SINK_SMTP`/`MAIL_SINK_API_URL`/`DRY_RUN_TO`/`MAIL_LIVE`/`CV_DATA_DIR` لكلتا core/core-scheduler + خدمة `mailpit` (شبكة داخلية فقط) + volume `cv_data` مشترك (`docker-compose.yml`، تحقّق: `docker compose config --quiet` ونصّ Python/yaml خضراء، كل depends_on/volume موجود)؛ idempotency فعلي بـsender.py (`_existing_application` قبل أي SMTP جديد لصفّ مُستعاد، `_claim_due_batch` يستعيد فعليًا صفوف 'sending' العالقة كما تصف وثائقها أصلًا)؛ `send_builder.py` يضبط `opportunities.status='queued'` لا `'sent'` وقت البناء (migration `0006_b4_fixes` يضيف الحالة لقيد CHECK + فهرسان جديدان)؛ `main.py` يلتقط `Exception` لا `ImportError` فقط مع `logger.exception`؛ 3 ملفات اختبار جديدة (`test_pacing.py` 45، `test_inbox_classifier.py` 36، `test_sender_idempotency.py` 5 ضد Postgres 16 محلي حقيقي — لا SQLite/تزييف). الدليل: `python -m compileall`/`import app.main,app.scheduler_main`/`pytest -q` (108→194، 0 فشل) + `alembic upgrade head`/`downgrade -1`/`upgrade head` نظيفة + `docker compose config --quiet` (exit 0). التقرير الكامل: `docs/reports/B4-executor-fixes.md`. **لم يُنشَر على الخادم الحي بعد ولم يعتمده مراجع** — يبقى B4 WIP حتى نشر + تحقّق حي (راجع فحوصات §"لا تزال تحتاج خادمًا حيًّا" بتقرير المراجعة).
- 2026-09-08 — REVIEWER (مراجعة حيّة مستقلة، بلا وصول SSH، عبر جسر MCP فقط) — B3/B4 — ACCEPT-WITH-FIXES (B4) / فحص سلامة جزئي ناجح (B3) — التقرير الكامل: `docs/reports/B3B4-live-review.md`. أبرز الأدلة: نشر v0.3.0 حي، `alembic` عند `0006_b4_fixes`، `mailpit` صحّي ويستقبل رسائل حقيقية بمرفقات PDF عبر تدفّق `/admin/mail/load-test`→`/admin/mail/send-now`، idempotency مؤكَّدة بتشغيل ثانٍ بلا تكرار. بلوكر NEEDS-OWNER: تحقّق `create_mail_link` SMTP/IMAP حقيقي يمنع اختبار مسار mail-link الكامل بلا حساب Gmail حقيقي (mailpit بلا IMAP). أثناء الجلسة اكتُشف remote git المضيف قد رجع إلى HTTPS (يكسر push غير التفاعلي) — أُصلح عبر `ops git-remote-ssh` (أعاد `ok`)، تحقّق `ops git` لاحقًا نظيف بلا commit عالق.

## 4. الواجهات المتاحة للجلسات

- **Core - Call** (`v7xKPShYWHJwqxvs`): `execute_workflow` production، `triggerNodeName: "Incoming Request"`، body: `{"path": "/admin/ping", "method": "GET", "body": {}}` → يعيد استجابة Core.
- **GitHub - Commit File** (`K65rLAVzarrtH4SV`، `execute_workflow` production، `triggerNodeName: "Commit Request"`): body: `{"path": "core/app/x.py", "content": "<نص الملف كاملًا>", "message": "..."}` → ينشئ/يحدّث الملف في `main` (يجلب sha تلقائيًا). للملفات الكبيرة أرسلها واحدًا واحدًا.
- **قراءة الكود:** `https://raw.githubusercontent.com/aborakanchatgpt-cloud/masar-core/main/<path>` (عام الآن؛ بعد B1 عبر `/admin/ops cmd=cat`؟ لا — أضف `/admin/file?path=` للقراءة فقط ضمن المستودع).
- **GitHub - Read File** (`HEZbBbYAGxBEvdAz`، `triggerNodeName: "Read Request"`): body `{"path": "...", "ref"?: "main", "maxChars"?: N}` → `{ok, content, sha}` (بديل WebFetch).
- **HTTP - Probe** (`zjjF6JrOqB1udpRX`، `triggerNodeName: "Probe Request"`): body `{"url": "...", "maxChars"?: N}` → `{status, snippet}`.
- **Core /admin/ops** (عبر Core - Call): `POST /admin/ops {"cmd":"logs","args":["caddy","200"]}` → `{id}`; ثم `GET /admin/ops/<id>` → `{status, exit_code, output}` (ينفَّذ على المضيف خلال ≤ 2 دقيقة). الأوامر: ps, logs, restart, up, caddy-reload, deploy-log, backup-log, sys, env-keys, migrate, backup-now, git, script <name>, psql <select>, deploy-key, git-remote-ssh, git-remote-https, commit <stage> <msg>, stage-clean.
- **Masar MCP bridge (الأفضل — بلا n8n):** موصّل Claude مخصص على `https://<domain>/mcp/<token>` (الرابط من `GET /admin/mcp-url`). أدوات: `repo_read{path}`, `repo_list{dir,pattern}`, `repo_write{files:[{path,content}],message}` (يُرفع commit من الخادم ويعيد النشر تلقائيًا), `ops{cmd,args}`, `job{id}`, `wait{seconds}`, `core_call{path,method,body}`, `server_time`.
- **إشعار أحمد:** `ZiVlsBibVps3pxGB` body `{"message": "..."}` — كل رسالة تحتاج فعله تبدأ بـ `نحتاجك فورا — `.
- **الرسائل للعملاء:** `S6AuI9VPaaQxhfhw`; إشعار الأدمن عبر Core: `UubD6Kba97XGJlVt`.

## 5. القفل

(فارغ)
