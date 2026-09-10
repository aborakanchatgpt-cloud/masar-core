"""
جامع عام (fallback) — يقرأ صفحة وظائف أي موقع ويستخرج بيانات schema.org
JobPosting المُضمّنة كـ JSON-LD (ممارسة SEO شائعة جدًا لظهور الوظائف بـ Google
Jobs، ويعمل مع أغلب أنظمة ATS تقريبًا — بما فيها الشركات التي تستضيف صفحات
وظائفها على Teamtailor أو BambooHR أو Workday أو صفحة مخصّصة، حين لا تتوفر
واجهة JSON عامة موثّقة لتلك المنصة تحديدًا).

هذا الجامع هو الحل المعتمد بالدليل لمثل هذه الحالات (بدل جامع مخصص لكل نظام
لا يوفّر API عام)، وهو أيضًا مصدر جيد لاكتشاف روابط وظائف إضافية عبر أي موقع
شركة عادي غير مرتبط بأي ATS معروف.

B2: يحترم robots.txt فعليًا قبل الجلب (Disallow لمسار الصفحة المطلوبة تحت
 User-agent: * أو المطابق لاسمنا) — إن مُنع الجلب يرفع ValueError بدل الجلب.
يستخدم http_client المشترك (مراجعة B2 R8/R9: User-Agent موحّد + تحديد معدّل
لكل مضيف + تراجع أُسّي عند 429/5xx).

تنفيذ B2-close (تغطية سعودية مغلقة المصدر بلا كشط، البند 2 المتروك من
B2b — راجع docs/reports/B2b-executor.md §7 و docs/reports/B2-close-executor.md):
حتى هذا التعديل كان `fetch_jobs(url)` يعامل `url` دائمًا كصفحة وظائف مفردة
واحدة يستخرج منها JSON-LD مباشرة — لا يفهم فعليًا `sitemap.xml` حقيقيًا (ملف
XML بمخطط `<urlset>`/`<sitemapindex>` قياسي حسب sitemaps.org) رغم اسم الوحدة.
كثير من الشركات السعودية الكبرى (وبعض بوابات التوظيف الحكومية) تنشر
`sitemap-jobs.xml` أو تُدرج روابط الوظائف ضمن `sitemap.xml` العام دون أن
تعرض واجهة JSON API — الحل المتوافق مع القسم 4.5 من الدليل: قراءة الـsitemap
نفسه (احترام robots.txt)، انتقاء روابط الوظائف منه (فرز بكلمات مفتاحية
شائعة بالمسار: job/career/vacancy/... أو الكل إن لم توجد إشارة، بحد أقصى
`MAX_JOB_PAGES` رابطًا لكل جولة لتفادي جلب مئات الصفحات لكل مصدر)، ثم قراءة
JSON-LD من كل رابط كالسابق تمامًا وتجميع كل الوظائف الموجودة. `sitemapindex`
(ملف يشير لعدة sitemaps فرعية) مدعوم أيضًا بعمق واحد فقط (حتى
`MAX_SUBSITEMAPS` ملفًا فرعيًا) — يكفي لأغلب المواقع الفعلية بلا مخاطرة
بجلب غير محدود. الكشف تلقائي بالكامل: يُفحص محتوى الاستجابة الفعلي (لا
امتداد الرابط فقط، لأن بعض المواقع تُقدّم `sitemap.xml` بمسار بلا `.xml`
ظاهر) — إن لم يكن XML بجذر `<urlset>`/`<sitemapindex>` يُعامَل كصفحة وظائف
مفردة كما كان السلوك السابق تمامًا (توافق رجعي كامل، لا كسر لأي مصدر sitemap_jsonld
حالي بالملف `data/sources_seed.csv` الذي يشير فعليًا لصفحة وظائف مفردة).
"""
from __future__ import annotations

import json
import re
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

from app.collectors.http_client import USER_AGENT, get as http_get

_JSONLD_SCRIPT_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)

# تنفيذ B2-close: أنماط sitemap.xml (sitemaps.org) — <urlset>/<sitemapindex>
# بجذر المستند، و<loc> لكل رابط مُدرَج.
_SITEMAP_ROOT_RE = re.compile(r"<\s*(urlset|sitemapindex)\b", re.IGNORECASE)
_LOC_RE = re.compile(r"<loc>\s*([^<\s][^<]*?)\s*</loc>", re.IGNORECASE)

# روابط تحمل إحدى هذه الكلمات بالمسار تُعامَل أولويةً كصفحات وظائف — فرز
# رخيص يمنع محاولة قراءة JSON-LD من مئات الصفحات غير المتعلقة بالوظائف
# (مقالات مدونة، صفحات منتج...) حين يكون sitemap.xml عامًا للموقع كله لا
# مخصصًا للوظائف وحدها.
_JOB_URL_HINT_RE = re.compile(
    r"job|career|vacan|position|hiring|recruit|opening|"
    r"وظيف|توظيف|تدريب|شاغر",
    re.IGNORECASE,
)

MAX_JOB_PAGES = 40
MAX_SUBSITEMAPS = 5


def _robots_parser(url: str, timeout: float) -> RobotFileParser | None:
    """يجلب ويحلّل robots.txt لنطاق `url` مرة واحدة. `None` يعني: تعذّر
    الجلب/التحليل (لا يوجد الملف، أو خطأ شبكة) — يُعامَل كسماح ضمني، لا حجب،
    بنفس سلوك urllib.robotparser القياسي حين لا يوجد robots.txt أصلًا."""
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    parser = RobotFileParser()
    parser.set_url(robots_url)
    try:
        response = http_get(robots_url, timeout=timeout)
        if response.status_code >= 400:
            return None
        parser.parse(response.text.splitlines())
        return parser
    except Exception:  # noqa: BLE001 — تعذّر قراءة robots.txt لا يعني حجبًا
        return None


def _robots_allow(url: str, timeout: float, parser: RobotFileParser | None = "unset") -> bool:  # type: ignore[assignment]
    """يتحقق من السماح بجلب `url`. إن لم يُمرَّر `parser` جاهزًا (القيمة
    الافتراضية الخاصة `"unset"` تُميّز "لم يُمرَّر شيء" عن `None` الصريحة
    التي تعني "جُرِّب الجلب وتعذّر")، يُجلب ويُحلَّل robots.txt للنطاق أولًا —
    يسمح هذا لمن يستدعي عدة عناوين بنفس النطاق (زحف sitemap) بجلب robots.txt
    مرة واحدة فقط وتمرير نفس الكائن لكل رابط تال، بدل إعادة الجلب لكل صفحة."""
    if parser == "unset":
        parser = _robots_parser(url, timeout)
    if parser is None:
        return True
    return parser.can_fetch(USER_AGENT, url)


def _iter_jobposting_nodes(data):
    """يمشي على أي بنية JSON-LD (كائن مفرد، قائمة، أو @graph) ويُرجع عقد JobPosting فقط."""
    if isinstance(data, list):
        for item in data:
            yield from _iter_jobposting_nodes(item)
        return
    if not isinstance(data, dict):
        return

    node_type = data.get("@type")
    type_names = node_type if isinstance(node_type, list) else [node_type]
    if any(str(t).lower() == "jobposting" for t in type_names if t):
        yield data

    graph = data.get("@graph")
    if graph:
        yield from _iter_jobposting_nodes(graph)


def _extract_location(job_location) -> str | None:
    if isinstance(job_location, list):
        job_location = job_location[0] if job_location else None
    if not isinstance(job_location, dict):
        return None
    address = job_location.get("address")
    if isinstance(address, dict):
        parts = [address.get("addressLocality"), address.get("addressCountry")]
        joined = ", ".join(p for p in parts if p)
        return joined or None
    return None


def _jobs_from_html(page_url: str, html: str) -> list[dict]:
    """يستخرج كل عقد JobPosting (schema.org) من صفحة HTML مفردة — المنطق
    الأصلي للوحدة، بلا تغيير، مُستخرَج بدالة مستقلة ليُعاد استخدامه لكل
    صفحة وظيفة نُكتشَف عبر sitemap.xml أيضًا."""
    jobs: list[dict] = []
    for raw_block in _JSONLD_SCRIPT_RE.findall(html):
        try:
            data = json.loads(raw_block.strip())
        except (json.JSONDecodeError, ValueError):
            continue
        for node in _iter_jobposting_nodes(data):
            hiring_org = node.get("hiringOrganization") or {}
            job_url = node.get("url") or page_url
            jobs.append(
                {
                    "external_id": node.get("identifier", {}).get("value")
                    if isinstance(node.get("identifier"), dict)
                    else node.get("identifier"),
                    "title": node.get("title"),
                    "url": urljoin(page_url, job_url) if job_url else page_url,
                    "location": _extract_location(node.get("jobLocation")),
                    "updated_at": node.get("datePosted"),
                    "company_name_hint": hiring_org.get("name") if isinstance(hiring_org, dict) else None,
                    "raw": node,
                }
            )
    return jobs


def _is_sitemap_xml(content: str) -> bool:
    """كشف حقيقي (لا بامتداد الرابط) — يفحص أول 2000 حرف بحثًا عن جذر
    `<urlset>` أو `<sitemapindex>` (مخطط sitemaps.org القياسي)."""
    return bool(_SITEMAP_ROOT_RE.search(content[:2000]))


def _select_job_like_urls(urls: list[str], limit: int) -> list[str]:
    """يرجّح الروابط التي يبدو مسارها متعلقًا بوظيفة (كلمة مفتاحية شائعة)؛
    إن لم يُطابق أي رابط شيئًا (sitemap عام صغير مثلًا، أو أسلوب تسمية غير
    معروف)، يُرجع الروابط كما هي حتى الحد الأقصى بدل إرجاع قائمة فارغة —
    أفضل من تفويت مصدر فعلي بالكامل بسبب فرز حرفي زائد."""
    job_like = [u for u in urls if _JOB_URL_HINT_RE.search(u)]
    chosen = job_like if job_like else urls
    return chosen[:limit]


def _collect_job_page_urls(sitemap_url: str, content: str, timeout: float, _depth: int = 0) -> list[str]:
    """يحلّل محتوى sitemap.xml مُحمَّلًا مسبقًا ويرجع روابط صفحات الوظائف
    المرشَّحة. `sitemapindex` يُتبَع بعمق واحد فقط (حتى MAX_SUBSITEMAPS ملفًا
    فرعيًا) لتفادي زحف غير محدود."""
    kind_match = _SITEMAP_ROOT_RE.search(content[:2000])
    kind = kind_match.group(1).lower() if kind_match else "urlset"
    locs = [urljoin(sitemap_url, loc) for loc in _LOC_RE.findall(content)]

    if kind == "sitemapindex":
        if _depth >= 1 or not locs:
            return []
        collected: list[str] = []
        for sub_sitemap_url in locs[:MAX_SUBSITEMAPS]:
            if not _robots_allow(sub_sitemap_url, timeout):
                continue
            try:
                sub_response = http_get(sub_sitemap_url, timeout=timeout, follow_redirects=True)
                sub_response.raise_for_status()
            except Exception:  # noqa: BLE001 — sitemap فرعي واحد فاشل لا يُسقط البقية
                continue
            collected.extend(
                _collect_job_page_urls(sub_sitemap_url, sub_response.text, timeout, _depth=_depth + 1)
            )
            if len(collected) >= MAX_JOB_PAGES:
                break
        return _select_job_like_urls(collected, MAX_JOB_PAGES)

    return _select_job_like_urls(locs, MAX_JOB_PAGES)


def fetch_jobs(career_page_url: str, timeout: float = 20.0) -> list[dict]:
    """يجلب `career_page_url` ويستخرج كل عقد JobPosting (schema.org)
    الموجودة به. مدعوم الآن (تنفيذ B2-close): إن كان المحتوى فعليًا ملف
    sitemap.xml (جذر `<urlset>` أو `<sitemapindex>`، يُكتشَف من المحتوى لا
    الامتداد)، تُنتقى روابط صفحات الوظائف منه (حتى MAX_JOB_PAGES رابطًا)
    وتُقرأ كل واحدة بنفس منطق استخراج JSON-LD، وتُجمَّع كل الوظائف الموجودة
    عبر كل الصفحات. صفحة HTML مفردة (السلوك الأصلي) تبقى تعمل بلا أي تغيير.
    كل رابط يُفحص عبر robots.txt (بكائن مُحلَّل واحد لكل نطاق، بلا إعادة جلب
    الملف لكل صفحة) قبل الجلب — أي رابط ممنوع يُتخطّى بصمت (لا يُسقط الجولة)."""
    domain_robots = _robots_parser(career_page_url, timeout)
    if not _robots_allow(career_page_url, timeout, parser=domain_robots):
        raise ValueError(f"robots.txt يمنع الجلب: {career_page_url}")

    response = http_get(career_page_url, timeout=timeout, follow_redirects=True)
    response.raise_for_status()
    content = response.text

    if not _is_sitemap_xml(content):
        return _jobs_from_html(career_page_url, content)

    job_page_urls = _collect_job_page_urls(career_page_url, content, timeout)
    jobs: list[dict] = []
    for page_url in job_page_urls:
        if not _robots_allow(page_url, timeout, parser=domain_robots):
            continue
        try:
            page_response = http_get(page_url, timeout=timeout, follow_redirects=True)
            page_response.raise_for_status()
        except Exception:  # noqa: BLE001 — صفحة وظيفة واحدة فاشلة لا تُسقط بقية الـsitemap
            continue
        jobs.extend(_jobs_from_html(page_url, page_response.text))
    return jobs
