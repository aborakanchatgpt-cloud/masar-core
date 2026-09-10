# PLAN.md — حالة البناء الحيّة وبروتوكول العمل الذاتي (اقرأه كاملًا قبل أي عمل)

> آخر تحديث: 2026-09-09 — كاتبه: INTEGRATOR (دمج شجرة واحدة من دفعات hotfix/B6/B7/B2-close المحلية غير المدفوعة — الخادم لا يزال معلَنًا معطّلًا، **لم يُدفع شيء بعد**). هذا الملف هو **مصدر الحقيقة الوحيد** لحالة المشروع — لكنه يحمل الآن فقط البروتوكول + الحالة الحالية المختصرة لكل بند + مؤشرات. **السجل التاريخي الكامل (كل تشغيلة، وكل فقرة مراجعة مفصّلة لكل بند) انتقل إلى `docs/PLAN_LOG.md`** — هذا الملف تجاوز حد `repo_write` (25,000 حرف) فنُقل التاريخ حرفيًا هناك دون حذف شيء؛ اقرأه لأي تفصيل تاريخي. المرجع التفصيلي للتصميم: `docs/EXECUTION_GUIDE.md`.

## 0. البروتوكول (لكل جلسة مجدولة)

يوجد دوران يعملان بالتناوب بلا تدخّل بشري:

| الدور | من | متى | ماذا يفعل |
|---|---|---|---|
| **EXECUTOR** (المنفّذ) | جلسة مجدولة بنموذج Sonnet | **كل ساعة** (الدقيقة :05) | يقرأ هذا الملف و`REVIEW.md`، يطبّق أولاً أي تصحيحات مفتوحة من المراجع، ثم يأخذ **أول بند `TODO`/`WIP` في القسم 2** وينفذه كاملاً مع اختباره ويرفع الملفات ويحدّث القسمين 2 و3. **لا يتوقف بعد بند واحد**: يجدّد القفل ويأخذ البند التالي، ويستمر حتى تنتهي البنود أو يصل إلى `BLOCKED` أو تنتهي الجلسة. |
| **REVIEWER** (المراجع) | جلسة مجدولة بالنموذج الافتراضي (Fable) | **كل ساعتين** (الدقيقة :35) | يتحقق من كل بند `DONE` لم يُراجع بعد مقابل معيار قبوله فعلياً (فحص حي عبر n8n → Core، قراءة الكود من GitHub). إن كان المنفّذ يعمل (قفل حي) لا يلمس `PLAN.md` بل يكتب تصحيحاته المرقّمة في `REVIEW.md` فقط؛ وإن لم يكن هناك قفل يحدّث `PLAN.md` مباشرة (إعادة بند إلى `TODO` مع التصحيحات، ترتيب البنود، القسم 1). يرسل تقريرًا واحدًا لأحمد مساءً. |

**قواعد ملزمة للدورين:**
1. **الوصول:** لا SSH ولا توكنات في أي جلسة. الكود يُرفع عبر وركفلو n8n `GitHub - Commit File` (القسم 4). الأوامر على الخادم عبر وركفلو n8n `Core - Call` (`v7xKPShYWHJwqxvs`) إلى نقاط `/admin/*`. الخادم يسحب كل commit خلال دقيقتين ويعيد البناء ويطبّق الترحيلات تلقائيًا.
2. **قفل:** قبل البدء اكتب في القسم 5 سطر `LOCK: <الدور> <وقت UTC>`; وعند الانتهاء احذفه. القفل صالح **55 دقيقة** من آخر تجديد: المنفّذ **يجدّد وقت القفل عند بدء كل بند** (رفع `PLAN.md`). منفّذ يجد قفل `EXECUTOR` عمره < 55 دقيقة يتوقف فورًا (زميله يعمل)؛ قفل أقدم من 55 دقيقة = جلسة ماتت، احذفه وتابع. المراجع الذي يجد قفل `EXECUTOR` حيًا لا يكتب في `PLAN.md` إطلاقًا (يكتب في `REVIEW.md` فقط).
3. **حجم التشغيلة:** بند تلو بند بلا توقف. قبل كل بند: جدّد القفل، واقرأ `REVIEW.md` وطبّق أي تصحيح حالته `OPEN` أولاً (ثم اكتب في القسم 3: `applied R<n>`). إن لم يكتمل بند، اكتب حالته `WIP` مع ما أُنجز بدقة وما تبقّى، حتى تكمله التشغيلة التالية من حيث توقّفت. ملف `REVIEW.md`: يكتبه المراجع فقط (سطور `R<n> — OPEN|APPLIED — البند — التصحيح بدقة`)، والمنفّذ يقرأه ولا يعدّله.
4. **ممنوع:** حذف بيانات؛ لمس وركفلوهات n8n القديمة غير المذكورة هنا؛ تعديل المهام المجدولة؛ إطفاء النظام القديم؛ أي كشط لمواقع توظيف؛ أي API برسوم لكل عملية؛ عرض سقوف أو تقديرات للعملاء.
5. **متى تُبلّغ أحمد (عبر `ZiVlsBibVps3pxGB` body `{"message": "..."}`):** سر يجب أن يدخله، دفع، إطفاء القديم، أو فشل متكرر ثلاث مرات لنفس البند. **كل رسالة تحتاج فعلاً منه تبدأ حرفياً بـ `نحتاجك فورا — `** ثم ماذا يفعل بالضبط (خطوات مرقّمة قصيرة، رابط إن وُجد، وأين يكتب السر: في n8n أو في `.env` على الخادم — لا في الشات أبدًا). لا ترسل الرسالة نفسها مرتين؛ إن كانت مُرسلة سابقًا (مسجّلة في القسم 3) فلا تكررها إلا بعد 24 ساعة. غير ذلك: المراجع يرسل تقريرًا واحدًا مساءً يبدأ بـ `تقرير مسار اليومي`.
6. **الاختبار قبل `DONE`:** كل بند له "معيار قبول" رقمي؛ لا يُعلّم `DONE` إلا بدليل (مخرجات فحص/استعلام) يُكتب في القسم 3، وسطر مختصر بالقسم 2 يشير للتقرير الكامل بـ`docs/reports/`.
7. **الأمان عند الكسر:** إن أظهر `/health` خطأًا بعد commit، المراجع يعيد آخر نسخة سليمة من الملف المعني عبر `GitHub - Commit File` (محتوى الإصدار السابق) ويعلّم البند `FAILED` مع السبب.
8. **حجم هذا الملف:** يجب أن يبقى تحت 20,000 حرف (حد `repo_write` الفعلي 25,000). أي فقرة سجل/مراجعة تاريخية جديدة تكتمل قصتها (بند صار DONE ومُراجعًا، أو WIP قديم اكتمل) تُنقَل إلى `docs/PLAN_LOG.md` بدل إبقائها هنا — أبقِ هنا سطرًا واحدًا مختصرًا فقط + مؤشرًا.

## 1. الحالة الآن (يحدّثها المراجع)

- ✅ المرحلة 0 والمرحلة 1 (البنية) مكتملتان: خادم Hetzner `masar-core-1` (`https://62.238.117.20.sslip.io`)، Postgres+Gotenberg+Core+core-scheduler تعمل، n8n ذاتي على `n8n.62.238.117.20.sslip.io`، Masar MCP bridge نشط (`core/app/mcp_bridge.py`).
- ⚠️ المستودع **عام مؤقتًا** (يُعاد خاصًا بعد إكمال B1). n8n Cloud التجريبي شبه منتهٍ — الجسر الفعلي الآن هو Masar MCP bridge.
- 🔴 **الخادم معلَن معطّلًا مؤقتًا وقت كتابة هذا السطر.** أربع دفعات نُفِّذت محليًا بمعزل عن بعضها (hotfix، B6، B7، B2-close) ثم **دُمجت شجرة واحدة متّسقة محليًا بجلسة INTEGRATOR** (`/home/claude/integrate`، بلا دفع أيضًا). أول جلسة تجد الخادم حيًا يجب أن تدفع بترتيب `/mnt/user-data/outputs/push-all/ORDER.txt` (hotfix أولًا، ثم `run_queue.sh`، ثم B6، ثم migration 0012 وB7، ثم B2-close، ثم `PLAN_LOG.md` وأخيرًا `PLAN.md`) قبل أخذ أي بند TODO جديد.
- ✅ **hotfix مدموج (INTEGRATOR):** سطرا تعليق فقدا `# ` بـ`mail_api.py`/`matching.py` (انكسار صامت وصل production مرتين) أُصلحا؛ `run_queue.sh` اكتسب ضامن فحص نحوي قبل أي commit مستقبلي.
- ✅ **B6 مدموج محليًا (INTEGRATOR، غير منشور):** تصليب حمل 1,500 عميل. تفاصيل: القسم 2 أدناه + `docs/reports/B6-executor.md`.
- ✅ **B7 مدموج محليًا (INTEGRATOR، غير منشور):** الكتالوج والباقات + migration `0012`. تفاصيل: القسم 2 أدناه + `docs/PLAN_LOG.md` §4.
- ⚠️ **B2 (الاكتشاف الحقيقي):** كان REJECT (مراجعة 2026-09-07). فحص B2-close (2026-09-09، محلي) وجد R10/R11/R14 **مُصلَحة فعليًا بالكود مسبقًا** (سلسلة commits سابقة لم تُحدَّث حالتها بهذا الملف)، وR12 عولجت سابقًا بـB2b. أُصلح R13 (الوحيد المتبقي فعليًا) بهذه الجلسة + تغطية RSS سعودية جديدة. **لا يزال WIP رسميًا حتى مراجعة قبول تنشر وتتحقق حيًا** — راجع القسم 2 أدناه وتقرير `docs/reports/B2-close-executor.md`.
- 🎯 **الهدف الزمني:** جاهزية استقبال أول عميل حقيقي بحلول **18 سبتمبر 2026** (B0–B7).

## 2. التالي (يأخذ المنفّذ أول TODO بالترتيب — لا يقفز)

### B0 — DONE (2026-09-06) — نقل n8n إلى الخادم. تفاصيل كاملة: `docs/PLAN_LOG.md`.

### B1 — WIP — نقاط الإدارة والعودة إلى مستودع خاص
باقٍ: (1) تأكيد `git fetch` عبر `ops git-remote-ssh`؛ (2) اختبار `repo_write` → commit+push من الخادم → إعادة نشر تلقائية؛ (3) جعل المستودع خاصًا ثم commit تجريبي للتأكد أن السحب يعمل؛ (4) تحديث القسم 4 هنا. `/admin/ops` و`/admin/deploy-key` منجزان ومُختبَران. تفاصيل: `docs/PLAN_LOG.md`.

### B2 — WIP (فُحص وأُغلق محليًا 2026-09-09، بانتظار نشر + مراجعة قبول) — الاكتشاف الحقيقي
**الحالة الفعلية بعد `docs/reports/B2-close-executor.md`:** R10 (dedup_key بـ`apply_url`)، R11 (`extract_country_code` لا يرجع لنص الوصف إلا حين الموقع فارغ)، R12 (تصنيف العائلة، عبر B2b سابقًا)، R14 (`dup_ratio_24h_in_region` بهوية `apply_url`، مُعرَّض بـ`GET /admin/stats`) — **كلها محقَّقة ومختبرة محليًا (293 اختبارًا خضراء)**. R13 (أولوية أقوى إشارة أقدمية بدل أول تطابق بالقائمة عند "intern") **أُصلح بهذه الجلسة**. تغطية سعودية مغلقة المصدر بلا كشط: `core/app/collectors/sitemap_jsonld.py` صار يفهم `sitemap.xml`/`sitemapindex` حقيقيين (لا صفحة مفردة فقط)، `scripts/verify_sources.py` جديد، و4 مصادر RSS سعودية/خليجية جديدة موثّقة (Samir Group، Chalhoub Group، Supersub، Qureos Inc) أُضيفت لـ`data/sources_seed.csv`. **لم يُنشر على الخادم الحي بعد** (الخادم كان معلَنًا معطّلًا هذه الجلسة) — البند يبقى WIP حتى: (1) دفع/نشر فعلي، (2) `recompute-region`/`merge-locations-cleanup` تُشغَّل حيًا إن لم تكن (كانت مُشغَّلة مسبقًا حسب سجل B2 الأصلي)، (3) مراجع مستقل يتحقق حيًا من الأرقام. التفاصيل الكاملة والأدلة: `docs/reports/B2-close-executor.md`؛ تاريخ المراجعتين الأصليتين وB2b الكامل: `docs/PLAN_LOG.md`.

### B3 — WIP (منفّذ محليًا، فحص حي جزئي 2026-09-08) — المطابقة والملف والمحفظة
جداول `customers/profiles/products/orders/ledger/subscriptions/opportunities/applications/feedback`، الاستبعاد القاطع، الدرجة، الطبقات A/B/C/C2/D. **معيار الحمل المتبقّي:** خطة يومية 1,500×3,000 وظيفة في < 5 دقائق (`scripts/bench_planner.py`) — لم يُختبر رقميًا كاملاً بعد. تفاصيل: `docs/reports/B3-executor.md`، `docs/PLAN_LOG.md`.

### B4 — WIP (تصحيحات F1/F2/F3 نُفِّذت ونُشرت حيًّا 2026-09-09، بانتظار مراجعة قبول) — الإرسال والوارد
mail-link (SMTP/IMAP+Fernet)، `send_queue` بـSKIP LOCKED، Sender متوازٍ، المولّد التركيبي، 4 قوالب CV، Inbox reader. ثلاث جولات مراجعة سابقة (REJECT أوفلاين → ACCEPT-WITH-FIXES حي → تصحيحات F1/F2/F3) — كل التفاصيل والبلوكرات المحلولة: `docs/PLAN_LOG.md`، `docs/reports/B4-*.md`.

### B5 — WIP — التقارير والتسجيل
تقرير 19:00، أزرار 👎/🎉، "استبعدنا لك"، onboarding بـn8n، الضمان/التعويض، لوحة الأدمن. ثلاثة أجزاء نُفِّذت ونُشرت حيًّا 2026-09-09 بانتظار مراجعة قبول: **B5a** (تقرير+ضمان+لوحة أدمن، `docs/reports/B5a-executor.md`)، **B5b** (بوتا تيليجرام n8n، بلا تفعيل بعد — يحتاج اعتمادات المالك، `docs/reports/B5b-executor.md`)، **B5c** (فجوات B5a/B2b: grace/outage منفصلان، `send_queue_id`، نقاط customers_api، `docs/reports/B5c-executor.md`، `pytest` 259→272). باقي B5 (حذف النظام القديم بعد 3 أيام تفوّق) لم يُبدأ. تفاصيل كاملة: `docs/PLAN_LOG.md`.

### B6 — WIP (نُفِّذ محليًا 2026-09-09، دُمج بجلسة INTEGRATOR، بانتظار دفع + مراجعة قبول حيّة) — تصليب الحمل لـ 1,500 عميل
فهارس Postgres على send_queue (claim/completed_at)، applications/company_cooldowns (تبريد)، opportunities (مخطَّط)، mail_links (تدقيق/تراجع) — migration `0011_b6_load` مُسلسَلة بعد `0010_b5c_gaps`؛ `DB_POOL_SIZE`/`DB_MAX_OVERFLOW`/`DB_STATEMENT_TIMEOUT_MS`/`DB_LOCK_TIMEOUT_MS` قابلة للضبط عبر env (`discovery.get_engine`، مدموجة سابقًا)؛ تقسيم قارئ الوارد عبر 15 قسمًا بالتكات + متابعة UID تزايدية + تراجع أُسّي للأخطاء (`inbox.py`)؛ حدود/وتيرة إرسال (`SEND_WORKERS`/`SEND_MAILBOX_MIN_INTERVAL_SECONDS`/`SEND_GLOBAL_RATE_PER_SECOND`) بـ`sender.py`؛ `docker-compose.yml` اكتسب حدود ذاكرة صريحة لكل خدمة (تجمع 4GB الخادم) وقيم Postgres (`shared_buffers`/`work_mem`/`max_connections=120`)؛ `send_stats_api.py` + أدوات اختبار حمل اصطناعي جديدة. التقرير الكامل (أدلة EXPLAIN قبل/بعد، بند بند): `docs/reports/B6-executor.md`. **باقٍ قبل DONE:** دفع فعلي على الخادم الحي، `alembic upgrade head` هناك، وتشغيل اختبار الحمل الاصطناعي (1,500 عميل × 17 = 25,500 صف) للتحقق من معيار < 6 ساعات على العتاد الفعلي (قِيس محليًا فقط حتى الآن).

### B7 — WIP (نُفِّذ محليًا 2026-09-09، دُمج بجلسة INTEGRATOR، بانتظار دفع + قناة دفع فعلية) — المنتجات والإطلاق
الكتالوج والباقات (أسعار مؤقتة `NULL`/"يُحدَّد لاحقًا")، نص الشروط، `RUNBOOK.md`، خطة أول 10 عملاء. `core/app/catalog.py` + migration `0012_b7_catalog` (مُسلسَلة أخيرًا بعد `0011_b6_load` — الملاحظة السابقة هنا عن غياب 0012 لم تعد صحيحة بعد دمج B6): `GET /catalog`، `POST /admin/orders`، `GET /admin/orders?customer_id=`. `docs/TERMS_AR.md`/`docs/RUNBOOK.md`/`docs/LAUNCH_CHECKLIST.md` جديدة. `core/tests/test_catalog.py` (13). التفاصيل الكاملة (نُقلت هنا من نسخة B7 الأصلية الأطول): `docs/PLAN_LOG.md` §4. **باقٍ قبل DONE:** لا قناة دفع فعلية بعد (الطلبات تُفعَّل يدويًا بعد دفع خارج النظام، كحال `POST /subscriptions`)، ولا دفع/نشر حيّ.

## 3. سجل التشغيلات (سطر واحد مختصر لكل تشغيلة جديدة — السجل الكامل التفصيلي في `docs/PLAN_LOG.md`)

آخر تشغيلتين (السجل الكامل من 2026-09-05 حتى الآن، حرفيًا، في `docs/PLAN_LOG.md` §1):

- 2026-09-09 — EXECUTOR (Sonnet subagent، sandbox، بلا SSH) — B5c — نُفِّذت ونُشرت حيًّا وتحقّقت، بانتظار مراجعة قبول — `docs/reports/B5c-executor.md`. `pytest` 259→272 (0 فشل).
- 2026-09-09 — EXECUTOR (Sonnet subagent، بيئة استنساخ محلي معزول، بلا SSH، بلا دفع) — B2-close — نُفِّذت محليًا فقط (بلا نشر)، بانتظار مراجعة قبول ونشر لاحق — `docs/reports/B2-close-executor.md`. R10/R11/R14 كانت مُصلَحة فعليًا بالكود مسبقًا (توثيق مُصحَّح)؛ R13 أُصلح فعليًا؛ `sitemap_jsonld.py` صار يفهم sitemap.xml حقيقي؛ `scripts/verify_sources.py` جديد؛ +4 مصادر RSS سعودية/خليجية موثّقة. `pytest` 272→293 (0 فشل). `alembic` up→down→up نظيف (لا ترحيل جديد). تقسيم `PLAN.md`/`docs/PLAN_LOG.md`.
- 2026-09-09 — INTEGRATOR (Sonnet subagent، فرع دمج محلي، بلا دفع) — دمج hotfix+B6+B7+B2-close شجرة واحدة (ترتيب: hotfix→B6→B7→B2-close؛ استُبعدت نسخة B6 القديمة من discovery/matching/mail_api/customers_api/guarantee/reports — سابقة لـB5c/hotfix). `main.py` يستورد `app.send_stats_api` و`app.catalog` معًا. تفاصيل كاملة: `docs/PLAN_LOG.md` §4. المخرجات: `/mnt/user-data/outputs/push-all/` + `ORDER.txt` + sha256.

## 4. الواجهات المتاحة للجلسات

- **Core - Call** (`v7xKPShYWHJwqxvs`): `execute_workflow` production، `triggerNodeName: "Incoming Request"`، body: `{"path": "/admin/ping", "method": "GET", "body": {}}` → يعيد استجابة Core.
- **GitHub - Commit File** (`K65rLAVzarrtH4SV`، `execute_workflow` production، `triggerNodeName: "Commit Request"`): body: `{"path": "core/app/x.py", "content": "<نص الملف كاملاً>", "message": "..."}` → ينشئ/يحدّث الملف في `main`. للملفات الكبيرة أرسلها واحدًا واحدًا.
- **قراءة الكود:** `https://raw.githubusercontent.com/aborakanchatgpt-cloud/masar-core/main/<path>` (عام الآن).
- **GitHub - Read File** (`HEZbBbYAGxBEvdAz`، `triggerNodeName: "Read Request"`): body `{"path": "...", "ref"?: "main", "maxChars"?: N}` → `{ok, content, sha}`.
- **HTTP - Probe** (`zjjF6JrOqB1udpRX`، `triggerNodeName: "Probe Request"`): body `{"url": "...", "maxChars"?: N}` → `{status, snippet}`.
- **Core /admin/ops** (عبر Core - Call): `POST /admin/ops {"cmd":"logs","args":["caddy","200"]}` → `{id}`; ثم `GET /admin/ops/<id>` → `{status, exit_code, output}`. الأوامر: ps, logs, restart, up, caddy-reload, deploy-log, backup-log, sys, env-keys, migrate, backup-now, git, script <name>, psql <select>, deploy-key, git-remote-ssh, git-remote-https, commit <stage> <msg>, stage-clean.
- **Masar MCP bridge (الأفضل — بلا n8n):** موصّل Claude مخصص على `https://<domain>/mcp/<token>` (الرابط من `GET /admin/mcp-url`). أدوات: `repo_read{path}`, `repo_list{dir,pattern}`, `repo_write{files:[{path,content}],message}`, `ops{cmd,args}`, `job{id}`, `wait{seconds}`, `core_call{path,method,body}`, `server_time`.
- **إشعار أحمد:** `ZiVlsBibVps3pxGB` body `{"message": "..."}` — كل رسالة تحتاج فعله تبدأ بـ `نحتاجك فورا —`.
- **الرسائل للعملاء:** `S6AuI9VPaaQxhfhw`; إشعار الأدمن عبر Core: `UubD6Kba97XGJlVt`.

## 5. القفل

(فارغ)
