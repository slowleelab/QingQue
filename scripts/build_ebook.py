#!/usr/bin/env python3
"""生成单文件电子书: docs/ebook/lumio-book.html

把 docs/book/ 全部章节合并为一个自包含 HTML (内联 CSS + 左侧目录 + 锚点导航),
推送到仓库后可用公共 CDN 访问, 无需 GitHub Pages 配置:

    https://cdn.jsdelivr.net/gh/slowleelab/lumio@main/docs/ebook/lumio-book.html

用法: uv tool run --from "mkdocs-material" python3 scripts/build_ebook.py
"""
from __future__ import annotations

import re
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parents[1]
BOOK = ROOT / "docs" / "book"
OUT = ROOT / "docs" / "ebook" / "lumio-book.html"

# (目录标题, 章节文件路径, 是否新部分)
CHAPTERS: list[tuple[str, str, bool]] = [
    ("序言", "00-preface.md", True),
    ("阅读路径与目录", "README.md", False),
    ("01 整体架构", "01-architecture-overview.md", True),
    ("02 配置系统", "02-configuration-system.md", False),
    ("03 Bot 自助问答", "03-bot-self-service.md", True),
    ("04 坐席辅助引擎", "04-assist-engine.md", False),
    ("05 RAG 检索全链路", "05-rag-pipeline.md", False),
    ("06 会话状态机", "06-session-state-machine.md", False),
    ("07 MCP 工具集成", "07-mcp-tool-integration.md", False),
    ("08 错误处理", "chapters/08-error-handling.md", True),
    ("09 RAG 摄入管线", "chapters/09-rag-ingestion.md", False),
    ("10 可观测性", "chapters/10-observability.md", False),
    ("11 安全合规", "chapters/11-security-compliance.md", False),
    ("12 数据层", "chapters/12-data-layer.md", False),
    ("13 部署", "chapters/13-deployment.md", False),
    ("14 测试策略", "chapters/14-testing-strategy.md", False),
    ("15 上下文工程", "chapters/15-context-engineering.md", True),
    ("16 客户记忆与知识图谱", "chapters/16-customer-memory-and-kg.md", False),
    ("17 工具调用与确认状态机", "chapters/17-tool-calling-and-confirmation.md", False),
    ("附录 A 术语表", "appendix/A-glossary.md", True),
    ("附录 B Sprint 时间线", "appendix/B-sprint-timeline.md", False),
    ("附录 C 故障排查", "appendix/C-troubleshooting.md", False),
]

MD = markdown.Markdown(
    extensions=["tables", "fenced_code", "toc", "sane_lists", "attr_list"],
    extension_configs={"toc": {"permalink": False, "slugify": lambda v, sep="-": re.sub(r"\s+", "-", v.strip())}},
)


def strip_frontmatter(text: str) -> str:
    """去掉 mkdocs frontmatter (--- ... --- 头块)."""
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return parts[2]
    return text


def render_chapter(title: str, rel_path: str) -> tuple[str, str]:
    """渲染章节 → (html, anchor_id)."""
    raw = (BOOK / rel_path).read_text(encoding="utf-8")
    body = strip_frontmatter(raw)
    anchor = re.sub(r"[^\w\u4e00-\u9fff]+", "-", title).strip("-")
    html = MD.convert(body)
    MD.reset()
    return (
        f'<section class="chapter" id="{anchor}">\n'
        f'<h1 class="chapter-title">{title}</h1>\n{html}\n</section>',
        anchor,
    )


def build() -> None:
    toc_items: list[str] = []
    sections: list[str] = []
    for title, rel_path, is_part in CHAPTERS:
        html, anchor = render_chapter(title, rel_path)
        sections.append(html)
        cls = ' class="part"' if is_part else ""
        toc_items.append(f'<li{cls}><a href="#{anchor}">{title}</a></li>')

    toc = "\n".join(toc_items)
    content = "\n".join(sections)

    page = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Lumio 技术深度剖析 — 灵智银行信用卡智能客服</title>
<style>
  :root {{
    --bg: #ffffff; --fg: #1a1a1a; --accent: #3f51b5;
    --code-bg: #f6f8fa; --border: #e1e4e8; --muted: #6a737d;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg: #0d1117; --fg: #e6edf3; --code-bg: #161b22;
      --border: #30363d; --muted: #8b949e;
    }}
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
    background: var(--bg); color: var(--fg); line-height: 1.7;
  }}
  header {{
    position: sticky; top: 0; z-index: 10;
    background: var(--accent); color: #fff; padding: 14px 24px;
    display: flex; justify-content: space-between; align-items: center;
  }}
  header h1 {{ margin: 0; font-size: 18px; }}
  header .sub {{ font-size: 12px; opacity: .85; }}
  .layout {{ display: flex; }}
  nav#toc {{
    width: 280px; min-width: 280px; height: calc(100vh - 56px);
    position: sticky; top: 56px; overflow-y: auto;
    border-right: 1px solid var(--border); padding: 16px 8px;
    font-size: 14px;
  }}
  nav#toc ul {{ list-style: none; padding: 0; margin: 0; }}
  nav#toc li {{ margin: 2px 0; }}
  nav#toc li.part {{ margin-top: 14px; font-weight: 600; }}
  nav#toc a {{ color: var(--fg); text-decoration: none; display: block; padding: 4px 10px; border-radius: 6px; }}
  nav#toc a:hover {{ background: var(--code-bg); }}
  main {{
    flex: 1; max-width: 900px; margin: 0 auto; padding: 32px 40px 120px;
  }}
  .chapter {{ border-bottom: 1px solid var(--border); padding-bottom: 24px; margin-bottom: 32px; }}
  h1.chapter-title {{ color: var(--accent); border-bottom: 2px solid var(--accent); padding-bottom: 8px; }}
  h2 {{ margin-top: 28px; }}
  h3 {{ margin-top: 20px; }}
  pre {{
    background: var(--code-bg); padding: 14px 16px; border-radius: 8px;
    overflow-x: auto; font-size: 13px; line-height: 1.5;
  }}
  code {{ font-family: "SF Mono", Menlo, Consolas, monospace; font-size: 13px; }}
  p code, li code {{ background: var(--code-bg); padding: 2px 5px; border-radius: 4px; }}
  table {{ border-collapse: collapse; width: 100%; margin: 16px 0; font-size: 14px; }}
  th, td {{ border: 1px solid var(--border); padding: 8px 12px; text-align: left; }}
  th {{ background: var(--code-bg); }}
  blockquote {{
    border-left: 4px solid var(--accent); margin: 16px 0; padding: 8px 16px;
    background: var(--code-bg); border-radius: 0 8px 8px 0;
  }}
  a {{ color: var(--accent); }}
  .mermaid {{ display: none; }}
  @media (max-width: 900px) {{
    .layout {{ flex-direction: column; }}
    nav#toc {{ width: 100%; height: auto; position: static; border-right: none; border-bottom: 1px solid var(--border); }}
    main {{ padding: 20px 16px; }}
  }}
</style>
</head>
<body>
<header>
  <h1>Lumio 技术深度剖析</h1>
  <span class="sub">灵智 · 银行信用卡智能客服 · 17 章 + 3 附录</span>
</header>
<div class="layout">
  <nav id="toc"><ul>{toc}</ul></nav>
  <main>
    <p style="color:var(--muted)">本电子书由 docs/book/ 自动生成 · 最新更新 2026-08-05</p>
    {content}
    <footer style="color:var(--muted);font-size:13px;margin-top:40px">
      <hr>Lumio (灵智) · Apache-2.0 · https://github.com/slowleelab/lumio
    </footer>
  </main>
</div>
</body>
</html>
"""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(page, encoding="utf-8")
    print(f"✅ 电子书已生成: {OUT} ({OUT.stat().st_size // 1024} KB, {len(CHAPTERS)} 章)")


if __name__ == "__main__":
    build()
