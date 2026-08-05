#!/usr/bin/env python3
"""生成电子书 PDF 版 (docs/ebook/lumio-book.pdf)。

用 headless Chromium (Playwright) 渲染单文件 HTML 电子书为 PDF:
- PDF 是手机浏览器原生渲染格式, 不依赖 HTTP content-type
- 打印前注入样式: 隐藏 sticky 顶栏与左侧目录, 章节分页, 强制浅色主题

用法:
    poetry run python scripts/build_ebook_pdf.py
"""

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
HTML_PATH = ROOT / "docs/ebook/lumio-book.html"
PDF_PATH = ROOT / "docs/ebook/lumio-book.pdf"

# 打印注入样式: 去掉导航, 章节独立分页, 强制浅色 (避免深色主题打印)
PRINT_CSS = """
@page { size: A4; margin: 16mm 14mm; }
header, #toc { display: none !important; }
.layout { max-width: none !important; padding: 0 !important; }
main { max-width: 100% !important; }
.chapter { break-before: page; }
.chapter:first-of-type { break-before: auto; }
:root {
  --bg: #ffffff !important; --fg: #1a1a1a !important;
  --code-bg: #f6f8fa !important; --border: #e1e4e8 !important;
  --muted: #6a737d !important;
}
body { background: #ffffff !important; }
pre, code { background: var(--code-bg) !important; }
"""


def main() -> None:
    if not HTML_PATH.exists():
        raise SystemExit(f"未找到 HTML 电子书: {HTML_PATH} (先运行 scripts/build_ebook.py)")

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(HTML_PATH.resolve().as_uri())
        page.wait_for_load_state("networkidle")
        page.add_style_tag(content=PRINT_CSS)
        page.pdf(path=str(PDF_PATH), format="A4", print_background=True)
        browser.close()

    size_kb = PDF_PATH.stat().st_size / 1024
    print(f"✅ PDF 电子书已生成: {PDF_PATH} ({size_kb:.0f} KB)")


if __name__ == "__main__":
    main()
