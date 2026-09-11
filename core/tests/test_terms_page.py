"""اختبارات B9/A5 — GET /terms وعرض docs/TERMS_AR.md بـapp/link_api.py.

بلا قاعدة بيانات إطلاقًا (كل ما هنا: تحويل Markdown يدوي + قراءة ملف +
معالج المسار مباشرة) — بملف منفصل عمدًا عن test_link_api.py، لأن ذاك
الملف يُنفّذ pytest.skip على مستوى الوحدة كليًا بلا Postgres محلي، وهذا
كان سيُسقط حتى هذه الاختبارات النقية معه (نفس سبب الفصل الموثّق بأعلى
test_telegram_admin.py/telegram_onboarding.py)."""
from __future__ import annotations

import asyncio

from app import link_api


def _run(coro):
    return asyncio.run(coro)


def test_inline_md_bold_and_escaping():
    out = link_api._inline_md("نص **مهم** و<script>&كذا")
    assert "<strong>مهم</strong>" in out
    assert "<script>" not in out
    assert "&lt;script&gt;" in out
    assert "&amp;كذا" in out


def test_render_terms_html_headings_paragraph_and_lists():
    md = (
        "# العنوان الرئيسي\n"
        "\n"
        "فقرة عادية بها **تشديد**.\n"
        "\n"
        "## عنوان فرعي\n"
        "- عنصر أول\n"
        "- عنصر ثانٍ\n"
        "\n"
        "1. خطوة أولى\n"
        "2. خطوة ثانية\n"
    )
    html = link_api._render_terms_html(md)
    assert html.startswith("<h1>العنوان الرئيسي</h1>")
    assert "<p>فقرة عادية بها <strong>تشديد</strong>.</p>" in html
    assert "<h2>عنوان فرعي</h2>" in html
    assert "<ul><li>عنصر أول</li><li>عنصر ثانٍ</li></ul>" in html
    assert "<ol><li>خطوة أولى</li><li>خطوة ثانية</li></ol>" in html


def test_render_terms_html_switches_list_type_without_merging():
    # قائمة نقطية تتبعها مباشرة قائمة مرقّمة يجب ألا تندمجا بعنصر <ul> واحد.
    md = "- نقطة\n1. رقم\n"
    html = link_api._render_terms_html(md)
    assert "<ul><li>نقطة</li></ul>" in html
    assert "<ol><li>رقم</li></ol>" in html


def test_load_terms_html_reads_real_file(monkeypatch, tmp_path):
    terms_file = tmp_path / "TERMS_AR.md"
    terms_file.write_text("# شروط\n\nفقرة **تجريبية**.\n", encoding="utf-8")
    monkeypatch.setattr(link_api, "TERMS_MD_PATH", terms_file)
    html = link_api._load_terms_html()
    assert "<h1>شروط</h1>" in html
    assert "<strong>تجريبية</strong>" in html


def test_load_terms_html_missing_file_returns_friendly_message(monkeypatch, tmp_path):
    monkeypatch.setattr(link_api, "TERMS_MD_PATH", tmp_path / "missing.md")
    html = link_api._load_terms_html()
    assert "تعذّر" in html


def test_show_terms_route_renders_rtl_page_without_auth(monkeypatch, tmp_path):
    terms_file = tmp_path / "TERMS_AR.md"
    terms_file.write_text("# شروط خدمة مسار\n\nكلمات بسيطة.\n", encoding="utf-8")
    monkeypatch.setattr(link_api, "TERMS_MD_PATH", terms_file)

    response = _run(link_api.show_terms())

    body = response.body.decode("utf-8")
    assert response.status_code == 200
    assert 'dir="rtl"' in body
    assert 'name="robots" content="noindex, nofollow"' in body
    assert "<h1>شروط خدمة مسار</h1>" in body
    assert "كلمات بسيطة." in body


def test_terms_route_registered_without_auth_dependency():
    # /terms على router الأعلى (بلا admin_router) — لا Depends(require_admin_token).
    matching = [r for r in link_api.router.routes if getattr(r, "path", "") == "/terms"]
    assert len(matching) == 1
    assert matching[0].dependencies == []
