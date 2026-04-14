---
name: bundle-local-report
categories: [rendering]
---

# bundle-local-report

Convert a research markdown report into a self-contained `report.html` with inlined
mermaid diagrams and inserted plot images from `yaml:figure-spec` blocks.

## Critical Constraints

**NEVER:**
- Raise a fatal error on missing diagrams or missing visualization-plan — log and continue.
- Use the ESM mermaid build — ESM triggers CORS under `file://`; always use the UMD bundle (`mermaid.min.js`).
- Exit without emitting `html_path = ` (even empty) as your final output — the recipe `capture:` block expects it.

**ALWAYS:**
- Emit `html_path = {path}` (or `html_path = ` if report_path is absent) as your final output.
- Use `{AUTOSKILLIT_TEMP}` as the base for temp files.
- Using ONLY classDef styles from the mermaid skill (no invented colors).

## Arguments

Positional (space-separated, injected by recipe):
1. `research_dir`             — absolute path to the research directory
2. `report_path`              — absolute path to the markdown report (README.md post-finalize)
3. `all_diagram_paths`        — comma-separated list of exp-lens diagram paths (may be empty)
4. `visualization_plan_path`  — absolute path to visualization-plan.md (may be empty string)

Output: `html_path = {absolute_path_to_report.html}` as your final output.

## Steps

### Step 0 — Parse arguments

Capture positional args:
- `$1` → `research_dir`
- `$2` → `report_path`
- `$3` → `all_diagram_paths` (comma-separated, may be empty)
- `$4` → `visualization_plan_path` (may be empty string)

If `report_path` does not exist, emit `html_path = ` (empty) immediately
(graceful non-fatal exit — the pipeline continues to begin_archival).

### Step 1 — Write and execute the embedded renderer

Write the Python renderer block below to `{AUTOSKILLIT_TEMP}/bundle-local-report-render.py`
in the current working directory (the worktree), then execute it:

```bash
python3 {AUTOSKILLIT_TEMP}/bundle-local-report-render.py "$1" "$2" "$3" "$4"
```

Capture the stdout line `html_path = ...` and emit it as the structured output token
as your final output.

**ALWAYS** emit `html_path = ` (even empty) as your final output — the recipe `capture:`
block expects it.

**NEVER** raise a fatal error on missing diagrams or missing visualization-plan — log and
continue.

**ALWAYS** use the UMD bundle (`mermaid.min.js`), never the ESM build — ESM triggers CORS
under `file://`.

### Embedded renderer

```python
# bundle-local-report renderer
#!/usr/bin/env python3
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
import re
import shutil
import sys
from pathlib import Path

# Validation keywords for mermaid diagrams (mirrors compose-research-pr)
VALIDATION_KEYWORDS = {
    "treatment", "outcome", "hypothesis", "H0", "H1",
    "IV", "DV", "causal", "confound", "mechanism",
    "effect", "comparison", "baseline", "threshold",
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


def _count_keywords(text: str) -> int:
    return sum(1 for kw in VALIDATION_KEYWORDS if kw in text)


def _extract_mermaid_blocks(text: str) -> list[str]:
    """Return list of mermaid diagram source strings from a markdown file."""
    _T3 = chr(96) * 3
    return re.findall(rf"{_T3}mermaid\n(.*?){_T3}", text, re.DOTALL)


def _validate_diagram_paths(paths_str: str) -> list[str]:
    """Return mermaid source strings for diagrams with ≥2 validation keywords."""
    validated = []
    if not paths_str.strip():
        return validated
    for raw in paths_str.split(","):
        p = Path(raw.strip())
        if not p.exists():
            continue
        content = p.read_text()
        if _count_keywords(content) >= 2:
            blocks = _extract_mermaid_blocks(content)
            validated.extend(blocks)
    return validated


def _parse_figure_specs(viz_plan_path: str) -> list[dict]:
    """Parse yaml:figure-spec blocks from visualization-plan.md."""
    specs = []
    if not viz_plan_path or not Path(viz_plan_path).exists():
        return specs
    text = Path(viz_plan_path).read_text()
    _T3 = chr(96) * 3
    raw_blocks = re.findall(rf"{_T3}yaml:figure-spec\n(.*?){_T3}", text, re.DOTALL)
    for block in raw_blocks:
        spec = {}
        for line in block.splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                spec[k.strip()] = v.strip()
        if spec:
            specs.append(spec)
    return specs


def _insert_images(html: str, specs: list[dict]) -> str:
    """Insert <img> tags after heading matches for each figure-spec."""
    for spec in specs:
        section = spec.get("report_section", "")
        title = spec.get("figure_title", "")
        img_path = spec.get("image_path", "")
        if not section or not img_path:
            continue
        img_tag = f'<img src="{img_path}" alt="{title}">'
        # Insert after the first heading that contains the section name
        pattern = rf"(<h[1-6][^>]*>[^<]*{re.escape(section)}[^<]*</h[1-6]>)"
        html = re.sub(pattern, rf"\1\n{img_tag}", html, count=1, flags=re.IGNORECASE)
    return html


def _markdown_to_html(md_text: str) -> str:
    """Convert markdown to HTML using markdown-it-py."""
    try:
        from markdown_it import MarkdownIt  # type: ignore[import]
        md = MarkdownIt()
        # Render standard markdown; mermaid fenced blocks become <pre><code class="language-mermaid">
        html = md.render(md_text)
        # Convert mermaid code blocks to <pre class="mermaid">
        html = re.sub(
            r'<pre><code class="language-mermaid">(.*?)</code></pre>',
            lambda m: f'<pre class="mermaid">{m.group(1)}</pre>',
            html,
            flags=re.DOTALL,
        )
        return html
    except ImportError:
        # Fallback: minimal paragraph wrapping (no markdown-it-py installed)
        paragraphs = md_text.strip().split("\n\n")
        return "".join(f"<p>{p.replace(chr(10), ' ')}</p>\n" for p in paragraphs)


def _find_mermaid_assets() -> tuple[Path | None, str]:
    """Locate mermaid.min.js and read the VERSION string."""
    candidates = [
        Path(__file__).parent.parent.parent.parent
        / "src" / "autoskillit" / "assets" / "mermaid",
        # Walk up to find the assets/ directory relative to any working directory
        *[
            p / "src" / "autoskillit" / "assets" / "mermaid"
            for p in Path(__file__).parents
            if (p / "src" / "autoskillit" / "assets" / "mermaid").exists()
        ],
    ]
    for d in candidates:
        js = d / "mermaid.min.js"
        ver_file = d / "VERSION"
        if js.exists():
            version = ver_file.read_text().strip() if ver_file.exists() else "unknown"
            return js, version
    return None, "unknown"


def main() -> None:
    if len(sys.argv) < 3:
        print("html_path = ", flush=True)
        sys.exit(0)

    research_dir = Path(sys.argv[1])
    report_path = Path(sys.argv[2])
    all_diagram_paths = sys.argv[3] if len(sys.argv) > 3 else ""
    viz_plan_path = sys.argv[4] if len(sys.argv) > 4 else ""

    if not report_path.exists():
        print(f"html_path = ", flush=True)
        sys.exit(0)

    # 1. Validate and collect mermaid diagram sources
    validated_diagrams = _validate_diagram_paths(all_diagram_paths)

    # 2. Build the mermaid section (injected at top of report body)
    mermaid_section = "\n".join(
        f'<pre class="mermaid">{src}</pre>' for src in validated_diagrams
    )

    # 3. Parse figure specs
    specs = _parse_figure_specs(viz_plan_path)

    # 4. Convert report markdown → HTML
    md_text = report_path.read_text()
    body_html = _markdown_to_html(md_text)

    # 5. Insert figure images at section headings
    if specs:
        body_html = _insert_images(body_html, specs)

    # 6. Locate mermaid assets
    mermaid_js_src, mermaid_version = _find_mermaid_assets()

    # 7. Render full HTML
    html = HTML_TEMPLATE.format(
        mermaid_version=mermaid_version,
        mermaid_section=mermaid_section,
        body_html=body_html,
    )

    # 8. Write report.html
    out_html = research_dir / "report.html"
    out_html.write_text(html, encoding="utf-8")

    # 9. Copy mermaid.min.js as sibling
    dest_js = research_dir / "mermaid.min.js"
    if mermaid_js_src and mermaid_js_src.exists():
        shutil.copy2(mermaid_js_src, dest_js)

    print(f"html_path = {out_html}", flush=True)


if __name__ == "__main__":
    main()
```
