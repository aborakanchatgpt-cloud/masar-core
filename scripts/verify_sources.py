#!/usr/bin/env python3
"""
scripts/verify_sources.py — تنفيذ B2-close: يفحص مصادر `discovery.sources`
(أو أي ملف CSV بنفس صيغة `data/sources_seed.csv`: أعمدة
`company,type,url,country,terms_note`) فحصًا حيًا فعليًا واحدًا لكل مصدر، بلا
كتابة على قاعدة البيانات إطلاقًا — أداة تحقّق قبل الإضافة، بديل أسرع من تكرار
عشرات نداءات `GET /admin/discovery/probe` يدويًا (الدليل §4.6 "Source Curator").

لكل مصدر يطبع صفًا بجدول: الشركة | النوع | الرابط | حالة HTTP | robots.txt |
عدد الوظائف المكتشف | ملاحظة/خطأ.

**احترام robots.txt صارم (اشتراط التكليف)**: قبل أي جلب فعلي لمحتوى المصدر
(بخلاف robots.txt نفسه)، يتحقق السكربت من `can_fetch(User-Agent, url)` — أي
مصدر يمنعه robots.txt يُعلَّم `DISALLOWED` ولا يُجلَب محتواه إطلاقًا (لا عدّ
وظائف، لا أي طلب آخر لذلك الرابط)، بصرف النظر عن نوع المصدر (API رسمي أو
sitemap/RSS) — نفس المنطق المطبَّق فعليًا داخل
`core/app/collectors/sitemap_jsonld.py`، لكن مُطبَّق هنا على كل الأنواع
تحوّطًا (بعض واجهات ATS الرسمية قد تُستضاف على نطاق يحمل robots.txt عامًا
للموقع كله).

الاستخدام:
    cd core && DATA_DIR=../data python ../scripts/verify_sources.py
    cd core && python ../scripts/verify_sources.py --file /path/to/candidates.csv
    cd core && python ../scripts/verify_sources.py --only-type sitemap_jsonld,rss

لا يحتاج قاعدة بيانات (لا DATABASE_URL) — فحص شبكي فقط، لا كتابة، لا قراءة DB.
يطبع سطر JSON نهائي وحيد (آخر سطر) بنفس اتفاقية scripts/classify_report.py.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

sys.path.insert(0, "/app")
try:
    from app import discovery  # noqa: E402
    from app.collectors.http_client import USER_AGENT, get as http_get  # noqa: E402
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
    from app import discovery  # noqa: E402
    from app.collectors.http_client import USER_AGENT, get as http_get  # noqa: E402

DEFAULT_SOURCES_CSV = Path(__file__).resolve().parent.parent / "data" / "sources_seed.csv"
ROBOTS_TIMEOUT = 10.0
PROBE_TIMEOUT = 20.0

_robots_cache: dict[str, RobotFileParser | None] = {}


def _robots_allow(url: str) -> tuple[bool, str]:
    """يرجع (مسموح؟, ملاحظة). يخزّن كائن robotparser لكل نطاق (netloc) مؤقتًا
    داخل تشغيلة السكربت نفسها لتفادي إعادة جلب robots.txt لكل صف من نفس
    النطاق. تعذّر جلب/تحليل robots.txt (404، لا يوجد، خطأ شبكة) = سماح ضمني
    (سلوك urllib.robotparser القياسي حين لا يوجد الملف أصلًا) — لا حجب."""
    parsed = urlparse(url)
    netloc = parsed.netloc
    if netloc not in _robots_cache:
        robots_url = f"{parsed.scheme}://{netloc}/robots.txt"
        parser: RobotFileParser | None = RobotFileParser()
        parser.set_url(robots_url)
        try:
            response = http_get(robots_url, timeout=ROBOTS_TIMEOUT)
            if response.status_code >= 400:
                parser = None
            else:
                parser.parse(response.text.splitlines())
        except Exception:  # noqa: BLE001 — تعذّر القراءة = لا يوجد قيد معروف
            parser = None
        _robots_cache[netloc] = parser

    parser = _robots_cache[netloc]
    if parser is None:
        return True, "لا robots.txt موجود/مقروء — سماح ضمني"
    allowed = parser.can_fetch(USER_AGENT, url)
    return allowed, ("مسموح صراحة أو بلا قيد مطابق" if allowed else "ممنوع صراحة بـrobots.txt")


def read_candidates(csv_path: Path) -> list[dict]:
    rows: list[dict] = []
    with open(csv_path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            company = (row.get("company") or "").strip()
            source_type = (row.get("type") or "").strip()
            url = (row.get("url") or "").strip()
            if not company or not source_type or not url:
                continue
            rows.append(
                {
                    "company": company,
                    "type": source_type,
                    "url": url,
                    "country": (row.get("country") or "").strip(),
                    "terms_note": (row.get("terms_note") or "").strip(),
                }
            )
    return rows


def verify_one(row: dict) -> dict:
    company, source_type, url = row["company"], row["type"], row["url"]
    result = {**row, "http_status": None, "robots_allowed": None, "robots_note": "", "jobs_found": None, "error": None}

    if source_type not in discovery.DISPATCH:
        result["error"] = f"نوع مصدر غير مدعوم: {source_type}"
        return result

    allowed, robots_note = _robots_allow(url)
    result["robots_allowed"] = allowed
    result["robots_note"] = robots_note
    if not allowed:
        result["error"] = "تخطّي — ممنوع بـrobots.txt (لا جلب محتوى)"
        return result

    try:
        head_resp = http_get(url, timeout=ROBOTS_TIMEOUT, follow_redirects=True)
        result["http_status"] = head_resp.status_code
    except Exception as exc:  # noqa: BLE001
        result["http_status"] = None
        result["error"] = f"فشل GET أولي: {exc}"[:300]
        return result

    try:
        result["jobs_found"] = discovery.validate_source(source_type, url, timeout=PROBE_TIMEOUT)
    except Exception as exc:  # noqa: BLE001
        result["error"] = str(exc)[:300]

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--file", type=Path, default=DEFAULT_SOURCES_CSV, help="ملف CSV (افتراضيًا data/sources_seed.csv)")
    parser.add_argument("--only-type", type=str, default=None, help="فلترة أنواع مفصولة بفواصل (مثال: sitemap_jsonld,rss)")
    parser.add_argument("--limit", type=int, default=None, help="أقصى عدد صفوف تُفحص (اختياري، للتجربة السريعة)")
    args = parser.parse_args()

    rows = read_candidates(args.file)
    if args.only_type:
        wanted = {t.strip() for t in args.only_type.split(",") if t.strip()}
        rows = [r for r in rows if r["type"] in wanted]
    if args.limit:
        rows = rows[: args.limit]

    results: list[dict] = []
    for i, row in enumerate(rows, start=1):
        result = verify_one(row)
        results.append(result)
        print(
            f"[{i}/{len(rows)}] {row['company']:<28} {row['type']:<16} "
            f"http={result['http_status']!s:<5} robots={'OK' if result['robots_allowed'] else 'DENY':<4} "
            f"jobs={result['jobs_found']!s:<5} {result['error'] or ''}"
        )
        time.sleep(0.05)  # فسحة صغيرة إضافية بين الصفوف فوق ما يضبطه http_client لكل مضيف

    ok_count = sum(1 for r in results if r["error"] is None and (r["jobs_found"] or 0) > 0)
    denied_count = sum(1 for r in results if r["robots_allowed"] is False)
    error_count = sum(1 for r in results if r["error"] is not None and r["robots_allowed"] is not False)

    print("\n" + "=" * 100)
    print(f"{'الشركة':<28} {'النوع':<16} {'HTTP':<6} {'robots':<8} {'وظائف':<8} ملاحظة")
    print("=" * 100)
    for r in results:
        note = r["error"] or ""
        print(
            f"{r['company']:<28} {r['type']:<16} {str(r['http_status']):<6} "
            f"{'OK' if r['robots_allowed'] else 'DENY':<8} {str(r['jobs_found']):<8} {note}"
        )
    print("=" * 100)
    print(f"الإجمالي: {len(results)} | نجح (≥1 وظيفة، بلا خطأ): {ok_count} | ممنوع robots.txt: {denied_count} | خطأ آخر: {error_count}")

    summary = {
        "total": len(results),
        "ok": ok_count,
        "robots_denied": denied_count,
        "errors": error_count,
        "results": results,
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
