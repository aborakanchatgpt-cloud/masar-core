# Masar Core

الخدمة الأساسية الجديدة لنظام "مسار" (بوت التقديم على الوظائف SaaS الخاص بأحمد) —
تحل تدريجيًا محل منطق n8n + Claude Code Remote الحالي، حسب خطة "مسار v5"
(القسم 9، المراحل 1-6).

## البنية (المرحلة 1 + بداية المرحلة 2)

- `core/` — خدمة FastAPI (`/health` عام + `/admin/ping` محمي بـ `CORE_ADMIN_TOKEN` عبر `core/app/auth.py`) + `core/app/collectors/` (8 جامعات: greenhouse, lever, ashby, smartrecruiters, workable, recruitee, sitemap_jsonld, rss, alert_mail + field_extractor للمطبّع الكامل) + `core/app/scheduler_main.py` (جدولة الجامع كل 30 دقيقة)
- `docker-compose.yml` — Postgres + Gotenberg + Core + Core Scheduler + Caddy (HTTPS تلقائي)
- `migrations/` — Alembic؛ الإصدار الأول ينشئ جداول المرحلة 2 (companies, sources, taxonomy_terms, jobs, metrics_hourly)
- `data/taxonomy_local.yaml` — مسودة أولى لمعجم العائلات المهنية المستهدفة (يُوسّعها Source Curator لاحقًا)
- `data/companies_seed.csv` — بداية حقيقية محقّقة (شركة واحدة مؤكدة فعليًا، Zeeco)؛ التوسّع جلسات Source Curator أسبوعية كما يحدد الدليل
- `scripts/import_esco.py` — استيراد مصطلحات ESCO المطابقة لعائلاتنا (يحتاج ملف CSV محلي)
- `deploy/bootstrap.sh` — إعداد خادم Ubuntu 24.04 جديد من الصفر (مرة واحدة): يثبت Docker، يشغّل الحزمة، يطبّق الترحيلات، **يولّد CORE_ADMIN_TOKEN وBACKUP_PASSPHRASE عشوائيًا ويطبعهما بالنهاية**، ويجدول autodeploy.sh (كل دقيقتين) وbackup.sh (يوميًا) تلقائيًا عبر cron
- `deploy/autodeploy.sh` — نموذج **سحب بالكامل**: كل دقيقتين (عبر cron) يسحب آخر تحديث من GitHub، يعيد البناء إن تغيّر شيء، ويطبّق ترحيلات قاعدة البيانات تلقائيًا — لا SSH ولا GitHub Actions تصل للخادم إطلاقًا
- `deploy/backup.sh` — نسخة احتياطية يومية مشفّرة من قاعدة البيانات (عبر cron)
- `PLAN.md` — سجل تقدّم حي لكل خطوات المرحلة 1 وما بعدها

## التشغيل على خادم جديد

```bash
git clone https://<TOKEN>@github.com/<GITHUB_USER>/masar-core.git /opt/masar-core
cd /opt/masar-core
bash deploy/bootstrap.sh
```

استبدل `<TOKEN>` بتوكن GitHub الخاص بك (يُدخل يدويًا، لا يُشارك مع أي جلسة
Claude) و`<GITHUB_USER>` باسم المستخدم/المنظمة الفعلي. بنهاية bootstrap.sh
ستحصل على رابط `/health`، مثال تحقق لـ `/admin/ping`، وقيمة `CORE_ADMIN_TOKEN`
لاستخدامها في اعتماد n8n ("Masar Core Admin Token").

راجع `PLAN.md` للخطوات المتبقية والحالة الحالية.
