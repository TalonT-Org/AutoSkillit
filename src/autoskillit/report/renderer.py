"""Self-contained HTML renderer for bundle-local-report.

Args (positional):
    1 research_dir           — directory containing README.md
    2 report_path            — path to the markdown report
    3 all_diagram_paths      — comma-separated diagram paths (may be empty)
    4 visualization_plan_path — path to visualization-plan.md (may be empty)

Writes:
    {research_dir}/report.html
    {research_dir}/mermaid.min.js  (copied from assets)
"""

from __future__ import annotations

import html as _html
import shutil
import sys
from pathlib import Path
from typing import Any, cast

import regex as re

from autoskillit.core import FigureSpec, atomic_write, pkg_root

VALIDATION_KEYWORDS = {
    "treatment",
    "outcome",
    "hypothesis",
    "H0",
    "H1",
    "IV",
    "DV",
    "causal",
    "confound",
    "mechanism",
    "effect",
    "comparison",
    "baseline",
    "threshold",
}

HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Research Report</title>
<!-- mermaid {mermaid_version} -->
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
       max-width: 900px; margin: 2rem auto; padding: 0 1rem; color: #1a1a1a; }}
article.report h1, h2, h3 {{ margin-top: 2rem; }}
pre.mermaid {{ background: #f8f8f8; padding: 1rem; border-radius: 4px; overflow-x: auto; }}
img {{ max-width: 100%; height: auto; display: block; margin: 1rem auto; }}
</style>
</head>
<body>
<article class="report">
{mermaid_section}
{body_html}
</article>
<script src="mermaid.min.js"></script>
<script>mermaid.initialize({{startOnLoad: true}});</script>
</body>
</html>
"""

_FENCE = "```"


def _count_keywords(text: str) -> int:
    return sum(1 for kw in VALIDATION_KEYWORDS if kw in text)


def _extract_mermaid_blocks(text: str) -> list[str]:
    """Return list of mermaid diagram source strings from a markdown file."""
    return re.findall(rf"{_FENCE}mermaid\r?\n(.*?)\r?\n?{_FENCE}", text, re.DOTALL)


def _validate_diagram_paths(paths_str: str) -> list[str]:
    """Return mermaid source strings for diagrams with ≥2 validation keywords."""
    validated: list[str] = []
    if not paths_str.strip():
        return validated
    for raw in paths_str.split(","):
        p = Path(raw.strip())
        if not p.exists():
            sys.stderr.write(f"Warning: diagram path not found, skipping: {p}\n")
            continue
        content = p.read_text(encoding="utf-8")
        if _count_keywords(content) >= 2:
            blocks = _extract_mermaid_blocks(content)
            validated.extend(blocks)
    return validated


def _parse_figure_specs(viz_plan_path: str) -> list[FigureSpec]:
    """Parse yaml:figure-spec blocks from visualization-plan.md."""
    specs: list[FigureSpec] = []
    if not viz_plan_path:
        return specs
    viz_plan = Path(viz_plan_path)
    if not viz_plan.exists():
        return specs
    text = viz_plan.read_text(encoding="utf-8")
    raw_blocks = re.findall(rf"{_FENCE}yaml:figure-spec\r?\n(.*?)\r?\n?{_FENCE}", text, re.DOTALL)
    for block in raw_blocks:
        spec: dict[str, Any] = {}
        for line in block.splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                spec[k.strip()] = v.strip()
        if spec:
            specs.append(cast(FigureSpec, spec))
    return specs


def _insert_images(html_body: str, specs: list[FigureSpec]) -> str:
    """Insert <img> tags after heading matches for each figure-spec."""
    for spec in specs:
        section = spec.get("report_section", "")
        title = spec.get("figure_title", "")
        img_path = spec.get("image_path", "")
        if not section or not img_path:
            continue
        img_tag = f'<img src="{_html.escape(img_path)}" alt="{_html.escape(title)}">'
        pattern = rf"(<h[1-6][^>]*>.*?{re.escape(section)}.*?</h[1-6]>)"
        html_body = re.sub(
            pattern,
            lambda m, t=img_tag: m.group(1) + "\n" + t,
            html_body,
            count=1,
            flags=re.IGNORECASE,
        )
    return html_body


def _markdown_to_html(md_text: str) -> str:
    """Convert markdown to HTML using markdown-it-py."""
    try:
        from markdown_it import MarkdownIt  # type: ignore[import]

        md = MarkdownIt()
        rendered = md.render(md_text)
        rendered = re.sub(
            r'<pre><code class="language-mermaid">(.*?)</code></pre>',
            lambda m: f'<pre class="mermaid">{_html.unescape(m.group(1))}</pre>',
            rendered,
            flags=re.DOTALL,
        )
        return rendered
    except ImportError:
        paragraphs = md_text.strip().split("\n\n")
        return "".join(f"<p>{_html.escape(p).replace(chr(10), ' ')}</p>\n" for p in paragraphs)


def _find_mermaid_assets() -> tuple[Path | None, str]:
    """Locate mermaid.min.js and read the VERSION string using pkg_root()."""
    mermaid_dir = pkg_root() / "assets" / "mermaid"
    js_path = mermaid_dir / "mermaid.min.js"
    if js_path.exists():
        ver_file = mermaid_dir / "VERSION"
        version = ver_file.read_text().strip() if ver_file.exists() else "unknown"
        return js_path, version
    return None, "unknown"


def main() -> None:
    if len(sys.argv) < 3:
        sys.stdout.write("html_path = \n")
        sys.stdout.flush()
        sys.exit(0)

    research_dir = Path(sys.argv[1])
    research_dir.mkdir(parents=True, exist_ok=True)
    report_path = Path(sys.argv[2])
    all_diagram_paths = sys.argv[3] if len(sys.argv) > 3 else ""
    viz_plan_path = sys.argv[4] if len(sys.argv) > 4 else ""

    if not report_path.exists():
        sys.stdout.write("html_path = \n")
        sys.stdout.flush()
        sys.exit(0)

    validated_diagrams = _validate_diagram_paths(all_diagram_paths)
    mermaid_section = "\n".join(
        f'<pre class="mermaid">{_html.escape(src)}</pre>' for src in validated_diagrams
    )
    specs = _parse_figure_specs(viz_plan_path)
    md_text = report_path.read_text(encoding="utf-8")
    body_html = _markdown_to_html(md_text)
    if specs:
        body_html = _insert_images(body_html, specs)
    mermaid_js_src, mermaid_version = _find_mermaid_assets()
    rendered_html = HTML_TEMPLATE.format(
        mermaid_version=mermaid_version,
        mermaid_section=mermaid_section.replace("{", "{{").replace("}", "}}"),
        body_html=body_html.replace("{", "{{").replace("}", "}}"),
    )
    out_html = research_dir / "report.html"
    atomic_write(out_html, rendered_html)
    dest_js = research_dir / "mermaid.min.js"
    if mermaid_js_src and mermaid_js_src.exists():
        shutil.copy2(mermaid_js_src, dest_js)

    sys.stdout.write(f"html_path = {out_html}\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
