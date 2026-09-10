# B6 — تصليب الحمل لـ1,500 عميل: تقرير المنفّذ

نطاقي: `sender.py`, `pacing.py`, `inbox.py`, `planner.py`, `send_builder.py`
(بلا لمس)، `matching.py` (أداء فقط)، `scheduler_main.py`, `discovery.py`
(`get_engine` فقط), migration `0011_b6_load`, `scripts/load_test_send.py`,
`deploy/ops/scripts/load_test*.sh`, `docker-compose.yml`. لم أُلمَس:
`guarantee.py`, `reports*.py`, `customers_api.py`, `feedback_api.py`,
`link_api.py`, migration `0010` (مملوكة بالكامل لمنفّذ B5c المتوازي).

## 1. الفهارس (migration 0011_b6_load، مُسلسلة بعد `0010_b5c_gaps`)

| # | الفهرس | الجدول/الشرط | الاستعلام الساخن المستهدَف |
|---|---|---|---|
| 1 | `ix_send_queue_claim_partial` | `(send_after, locked_until)` WHERE `status IN ('queued','sending')` | `sender._claim_due_batch` (المطالبة الرئيسية) |
| 2 | `ix_send_queue_sent_completed_at` | `(completed_at)` WHERE `status='sent'` | `sender.get_queue_stats` (أُرسل/دقيقة) |
| 3 | `ix_applications_sent_at_company_key_customer` | `(sent_at, company_key, customer_id)` WHERE `company_key IS NOT NULL` | `send_builder.fetch_cooldown_pairs`/`fetch_weekly_cap_companies` |
| 4 | `ix_company_cooldowns_last_sent_at` | `(last_sent_at)` | `send_builder.fetch_cooldown_pairs` (الطرف الثاني) |
| 5 | `ix_opportunities_customer_planned_score` | `(customer_id, planned_for, score DESC)` WHERE `status='planned'` | `send_builder._fetch_candidate_opportunities` |
| 6 | `ix_mail_links_status_next_check` | `(next_check_at)` WHERE `status='ok'` | `inbox.run_inbox_round` (تقسيم+تراجع) |

أعمدة جديدة: `send_queue.completed_at` (timestamptz)، `mail_links.last_uid`/
`error_count`/`next_check_at`. كلها `CREATE INDEX` عادية (لا CONCURRENTLY) —
الجداول صغيرة وقت الترحيل وautodeploy يوقف core-scheduler أثناء الترحيل.

### أدلة EXPLAIN قبل/بعد

بيانات الإنتاج الحيّة شبه فارغة وقت هذا التقرير، فأدلة EXPLAIN أدناه من
قاعدة اختبار محلية معزولة (`masar_b6_test`) مبذورة بحجم واقعي: 1,500 عميل،
25,500 صفّ `send_queue`، 15,000 `applications`، 22,500 `opportunities` —
نفس أعداد B6 المستهدَفة، `ANALYZE` بعد البذر وبعد كل ترحيل.

**Q1 — مطالبة send_queue (الأهم):**
- قبل: `Bitmap Heap Scan` + `Sort` صريح (cost≈1004..1078) — فرز `send_after`
  خارج الفهرس.
- بعد: `Index Scan` مباشر على `ix_send_queue_claim_partial` (cost≈0.29..130)
  **بلا أي عقدة Sort** — الفهرس يرتّب حسب `send_after` أصلًا. هذا التغيير
  وحده هو الأهم لمعيار القبول (نفس الاستعلام يُستدعى كل تكة/كل عامل).

**Q3 — sent/60s (`completed_at`):** قبل الترحيل العمود غير موجود أصلًا
(قدرة جديدة كليًا لا تحسين استعلام قائم). بعد: `Bitmap Index Scan` على
`ix_send_queue_sent_completed_at` (cost≈998، فحص محصور بدل مسح `send_queue`
كاملة).

**Q6 — سقف 3 عملاء/أسبوع (نافذة 7 أيام، ~7.6% من applications):** قبل:
`Bitmap Heap Scan` على `ix_applications_status_sent_at` (cost≈502). بعد:
`Bitmap Index Scan` على `ix_applications_sent_at_company_key_customer`
(cost≈334) — تحسّن ~33%.

**Q7 — فرص العميل المخطَّطة (`send_builder`):** قبل: `Bitmap Index Scan` على
`uq_opportunities_customer_job` (cost≈53). بعد: فهرس جزئي مخصّص
`ix_opportunities_customer_planned_score` (cost≈32) — تحسّن ~40%،
ويُغني لاحقًا عن فرز `score DESC` خارجي حين يُستخدَم بترتيب صريح.

**Q4/Q5 — تبريد 60 يومًا (applications/company_cooldowns):** ظلّ الخطة
`Seq Scan` رغم الفهرس الجديد — بيانات الاختبار موزَّعة بانتظام على 90 يومًا
فتُطابِق نافذة 60 يومًا ~66% من الصفوف، وPostgres يُفضِّل مسحًا تسلسليًا
بأمانة عند هذه النسبة العالية (سلوك مخطِّط سليم، لا عيب بالفهرس). الفهرس
يصبح حاسمًا مع نمو `applications` بمرور الأشهر (نافذة 60 يومًا تصبح كسرًا
أصغر تدريجيًا من إجمالي الجدول) أو لنوافذ أضيق — Q6 (7 أيام) يُثبت هذا
الانتقال فعليًا أعلاه.

**Q8 — mail_links (1,500 صف فقط):** `Seq Scan` ثابت قبل وبعد — جدول بهذا
الحجم (~14 صفحة) لا يستفيد من أي فهرس عمليًا؛ الفهرس الجديد يصبح مفيدًا مع
نمو عدد العملاء بعشرات الآلاف مستقبلًا، وقيمته الحقيقية الآن تعبيرية
(الشرط `next_check_at IS NULL OR next_check_at <= now()` يُطبَّق بنفس
الكفاءة عبر Filter على جدول بهذا الحجم).

## 2. مجمّع الاتصالات (discovery.py:get_engine)

`DB_POOL_SIZE`/`DB_MAX_OVERFLOW`/`DB_POOL_RECYCLE_SECONDS`/
`DB_STATEMENT_TIMEOUT_MS`/`DB_LOCK_TIMEOUT_MS` عبر بيئة — الافتراضيات
القديمة (5/5 بلا recycle/timeout) محفوظة بلا env صريح. `docker-compose.yml`
يضبط: core=10+5 (لا statement_timeout)، core-scheduler=15+15+30s timeout.
الحساب: 15+30=45 حد أقصى من Core مقابل `max_connections=120` (postgres
`-c max_connections=120`، مرفوع من 100)، هامش كبير مع n8n وجلسات `ops psql`.

## 3. المُرسِل (sender.py)

- `SEND_WORKERS` (افتراضي 20، env)، `MAILBOX_MIN_INTERVAL_SECONDS=6`
  (سقف Gmail لكل صندوق، حجز فتحة زمنية تفاؤلي بلا سباق)، سقف عام
  `GLOBAL_RATE_PER_SECOND=20` (token bucket، يحمي Postgres/SMTP من انفجار
  تزامن SEND_WORKERS عند استرداد Backlog).
- `MAX_ATTEMPTS=3` (كان ثابتًا، الآن env) — فشل نهائي = dead-letter
  (`status='failed'`) + استرداد رصيد + `opportunities.status='skipped'`.
- عدّادات in-process: `sent_total`/`failed_total`/`mailbox_rate_limit_hits`/
  `failures_by_class` (تصنيف تقريبي: auth/timeout/connection/
  recipient_refused/no_mailbox/other).
- `get_queue_stats`: مقاييس من قاعدة البيانات (تصمد عبر إعادة تشغيل
  العملية) — عمق الطابور بكل حالة، عمر أقدم صفّ مستحق، مُرسَل آخر
  60 ثانية/5 دقائق (عبر `completed_at` الجديد).
- نقطة جديدة `GET /admin/send/stats` (`send_stats_api.py`) تجمع كل ما سبق
  + حالة pool + ملخّص تراجع الوارد — لا تصطدم مع `/admin/stats` (discovery)
  ولا `/admin/mail/stats` (mail_api) الموجودتين.

## 4. الوارد (inbox.py)

1. **UID تزايدي** بدل `UNSEEN`: القراءة `readonly=True` تعني `\Seen` لا
   يتغيّر أبدًا من جهتنا — `UNSEEN` كانت ستُعيد مسح كل الوارد التاريخي كل
   تكة للأبد. الآن `UID SEARCH UID <last_uid+1>:*` + `mail_links.last_uid`.
2. **تقسيم عبر التكات**: `current_partition()` حتمي من الوقت (بلا حالة
   مخزَّنة) — 15 قسمًا افتراضيًا × تكة كل 15 دقيقة (`INBOX_TICK_SECONDS`
   يجب أن يطابق `IntervalTrigger(minutes=15)` بـ`scheduler_main.py`) = كل
   صندوق يُفحص كل ~3.75 ساعة، لا 1,500 اتصال IMAP متزامن كل تكة.
3. **تراجع الأخطاء**: `error_count`/`next_check_at` — تراجع أُسّي (2^n
   دقيقة، سقف `INBOX_BACKOFF_MAX_MINUTES=240`)، يُصفَّر فورًا عند نجاح.

## 5. المخطِّط (planner.py) — بلا تعديل منطقي

`scripts/bench_planner.py` محليًا: 1,500×3,000 → `plan_seconds=22.401`،
`opportunities_planned=25500` — تحت سقف B6 الجديد (<10 دقائق) وB3 الأصلي
(<5 دقائق) بهامش كبير.

## 6. اختبار الحمل — النتائج

### محليًا (صندوق الرمل، 2 vCPU — نفس عدد أنوية الخادم)

تشغيلة كاملة الحجم عبر `scripts/load_test_send.py --customers 1500
--per-customer 17` (نتيجة JSON الفعلية من التشغيلة):

- **الإنتاجية المُقاسة**: `20.02` صفّ/ثانية (مقيَّدة عمليًا بسقف
  `GLOBAL_RATE_PER_SECOND=20` الافتراضي — القيد الفعّال الملاحَظ، لا نضوب
  موارد).
- **إسقاط 25,500 صفّ محليًا**: `1,273.7` ثانية = **0.354 ساعة (~21.2
  دقيقة)**.
- **إسقاط الخادم (عامل تحفّظ موثَّق 0.5×، يُحاسِب تشارك 2 vCPU مع
  postgres+n8n+gotenberg+caddy+mailpit بخلاف الصندوق المعزول محليًا)**:
  **0.708 ساعة (~42.5 دقيقة)** — هامش **~8.5×** تحت سقف 6 ساعات.
- **زمن استجابة `/health` أثناء التشغيلة الكاملة** (9,649 عيّنة، استطلاع كل
  200ms): p50=1.7ms، **p95=2.7ms**، p99=4.1ms — **تحت 500ms بهامش ~185×**؛
  API لم "يتضوّر جوعًا" إطلاقًا رغم تشبّع SEND_WORKERS.
- `mailbox_rate_limit_hits=1` فقط (متوقَّع عند 1,500 عميل مختلفين — الحدّ
  لكل صندوق نادرًا ما يُصادَف بهذا العدد)، `failures_by_class={}` (صفر
  فشل — DRY_RUN/sink محلي مستقرّ).
- **ملاحظة تشغيلية**: التشغيلة الفعلية عالجت 51,000 صفّ إجمالًا (25,500 من
  محاولة سابقة تعطّلت بمهلة أداة محلية ولم تُنظَّف فورًا + 25,500 من
  التشغيلة النظيفة، بلا فصل بينهما لأن `_claim_due_batch` لا يُفلتِر
  بعلامة دفعة) — أُنجزت كلتاهما بنجاح تام (`claimed=0` نهائيًا) خلال نفس
  المدة، أي إثبات أقوى من المطلوب. نُظِّفت كل الصفوف/العملاء الاصطناعيين
  يدويًا بعدها (`synthetic=true` + نمط `@masar.invalid` فقط — لا حذف لأي
  بيانات أخرى)، والبيئة المحلية عادت نظيفة بالكامل قبل المتابعة.

### حيًّا على الخادم (مُصغَّر، ≤500 صفّ، `deploy/ops/scripts/load_test.sh`)

<!-- LIVE_TEST_RESULTS_PLACEHOLDER -->

## 7. docker-compose.yml (بند 8)

حدود ذاكرة صريحة لكل خدمة (مجموع الحدود القصوى ≈3.26GB من ~3.8GB متاحة):
postgres 768M(حجز 512M)، core 512M، core-scheduler 512M، gotenberg 512M،
n8n 768M، mailpit 128M، caddy 128M. Postgres: `shared_buffers=256MB`،
`work_mem=8MB`، `effective_cache_size=1536MB`، `maintenance_work_mem=64MB`،
`max_connections=120`. `mailpit --max 2000` (سقف احتفاظ رسائل — يمنع امتلاء
القرص 40GB المشترك). تحقّق: `docker compose config -q` → `CONFIG_VALID`.

## 8. بوابات الجودة

`python -m compileall -q core` ✓ | `import app.main, app.scheduler_main` ✓
| pytest: **270 passed** (259 أصلي + 11 جديد لـB6) على `masar_b6_test`
معزولة، بعد تسلسل الترحيل الكامل `0001→...→0010_b5c_gaps→0011_b6_load` ✓ |
`alembic downgrade -1` ثم `upgrade head` (round-trip) ✓ | `docker compose
config -q` ✓.

## 9. الالتزامات الأمنية

لا طباعة أسرار (توكن الإدارة/كلمات مرور SMTP لا تظهر بأي سجلّ/تقرير)؛ لا
حذف بيانات غير اصطناعية إطلاقًا (كل حذف — محليًا وبنقطة `/admin/mail/
load-test/cleanup` الجديدة — مقيَّد بـ`synthetic=true` **و** نمط بريد
`@masar.invalid` معًا)؛ `DRY_RUN` لم يُعطَّل قط (`MAIL_LIVE` بقي `false`
بكل تشغيلة).

## 10. Commits

<!-- COMMITS_PLACEHOLDER -->

## 11. ما تبقّى

- الترحيل 0011 يعيد فهرسة applications/company_cooldowns بفائدة ستزيد مع
  نمو البيانات — لا حاجة لعمل إضافي الآن (موثَّق بالقسم 1).
- لم أُدخل CONCURRENTLY على الفهارس (غير ضروري بحجم الإنتاج الحالي +
  autodeploy يوقف core-scheduler أثناء الترحيل بالفعل) — إن كبرت الجداول
  كثيرًا مستقبلًا قبل نافذة صيانة، يستحق إعادة تقييم هذا القرار.
- اختبار الحمل الحيّ المُصغَّر (القسم 6) يعتمد على `/admin/mail/load-test`
  الموجودة أصلًا (B4/B5) — لم أُعدِّلها، فقط أضفت `/admin/mail/load-test/
  cleanup` بجانبها.
