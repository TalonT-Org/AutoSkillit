"""Tests for the bundle-local-report skill renderer."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from autoskillit.report.renderer import (
    HTML_TEMPLATE,
    _count_keywords,
    _extract_mermaid_blocks,
    _find_mermaid_assets,
    _insert_images,
    _markdown_to_html,
    _parse_figure_specs,
    _validate_diagram_paths,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_renders_minimal_report(tmp_path: Path) -> None:
    """One-paragraph markdown + zero diagrams → HTML has mermaid init + paragraph."""
    research_dir = tmp_path / "research"
    research_dir.mkdir()
    report = research_dir / "README.md"
    report.write_text("Hello world paragraph.\n")
    viz_plan = research_dir / "visualization-plan.md"
    viz_plan.write_text("")

    _, mermaid_version = _find_mermaid_assets()

    mermaid_section = ""
    body_html = _markdown_to_html(report.read_text())
    html = HTML_TEMPLATE.format(
        mermaid_version=mermaid_version,
        mermaid_section=mermaid_section,
        body_html=body_html,
    )
    out_html = research_dir / "report.html"
    out_html.write_text(html, encoding="utf-8")

    assert out_html.exists()
    html_content = out_html.read_text()
    assert "mermaid.initialize" in html_content
    assert "Hello world paragraph" in html_content


def test_renders_with_mermaid_diagram(tmp_path: Path) -> None:
    """markdown + one valid exp-lens diagram → HTML body has <pre class='mermaid'>."""
    research_dir = tmp_path / "research"
    research_dir.mkdir()
    report = research_dir / "README.md"
    report.write_text("# Report\n\nBody text.\n")

    diag = tmp_path / "diag.md"
    diag.write_text(
        "```mermaid\ngraph LR\n  treatment --> outcome\n  hypothesis --> causal\n```\n"
    )

    validated = _validate_diagram_paths(str(diag))
    assert len(validated) == 1
    assert "treatment --> outcome" in validated[0]


def test_skips_invalid_mermaid_diagram(tmp_path: Path) -> None:
    """Diagram with <2 validation keywords is silently skipped."""
    diag = tmp_path / "diag_invalid.md"
    diag.write_text("```mermaid\ngraph LR\n  A --> B\n```\n")

    validated = _validate_diagram_paths(str(diag))
    assert len(validated) == 0


def test_images_inserted_from_figure_spec(tmp_path: Path) -> None:
    """figure-spec YAML → HTML has <img> with correct src/alt at section."""
    research_dir = tmp_path / "research"
    research_dir.mkdir()
    images_dir = research_dir / "images"
    images_dir.mkdir()
    (images_dir / "fig-1.png").write_bytes(b"\x89PNG")

    report = research_dir / "README.md"
    report.write_text("# Report\n\n## Results\n\nSome text here.\n")

    viz_plan = tmp_path / "visualization-plan.md"
    viz_plan.write_text(
        "```yaml:figure-spec\n"
        "figure_id: fig-1\n"
        "figure_title: Main Results\n"
        "report_section: Results\n"
        "image_path: images/fig-1.png\n"
        "```\n"
    )

    specs = _parse_figure_specs(str(viz_plan))
    assert len(specs) == 1

    body_html = _markdown_to_html(report.read_text())
    body_html = _insert_images(body_html, specs)

    assert 'src="images/fig-1.png"' in body_html
    assert 'alt="Main Results"' in body_html


def test_html_includes_mermaid_version_comment(tmp_path: Path) -> None:
    """Rendered HTML contains a <!-- mermaid {version} --> version comment (not 'unknown')."""
    research_dir = tmp_path / "research"
    research_dir.mkdir()
    report = research_dir / "README.md"
    report.write_text("# Report\n")

    _, mermaid_version = _find_mermaid_assets()

    mermaid_section = ""
    body_html = _markdown_to_html(report.read_text())
    html = HTML_TEMPLATE.format(
        mermaid_version=mermaid_version,
        mermaid_section=mermaid_section,
        body_html=body_html,
    )

    assert "<!-- mermaid " in html
    assert mermaid_version != "unknown", (
        "Mermaid version resolved to 'unknown' — _find_mermaid_assets() failed. "
        "The renderer must use pkg_root() from core.paths."
    )


def test_renderer_uses_importlib_not_dunder_file() -> None:
    """renderer.py must not use Path(__file__) for asset resolution.

    Path(__file__) resolves to the temp directory when running extracted scripts,
    causing _find_mermaid_assets() to always return (None, "unknown").
    The renderer module must use pkg_root() from core.paths instead.
    """
    renderer_path = _REPO_ROOT / "src/autoskillit/report/renderer.py"
    tree = ast.parse(renderer_path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "__file__":
            pytest.fail(
                "renderer.py uses __file__ for asset resolution. "
                "Use pkg_root() from core.paths instead."
            )


def test_count_keywords() -> None:
    text = "This diagram shows treatment and outcome with hypothesis H0 and H1"
    assert _count_keywords(text) >= 4


def test_extract_mermaid_blocks() -> None:
    md = "Some text\n```mermaid\ngraph LR\n  A --> B\n```\nMore text"
    blocks = _extract_mermaid_blocks(md)
    assert len(blocks) == 1
    assert "graph LR" in blocks[0]


def test_parse_figure_specs_empty() -> None:
    specs = _parse_figure_specs("")
    assert specs == []

    # Non-existent path also returns empty list
    specs = _parse_figure_specs("/nonexistent/path/visualization-plan.md")
    assert specs == []
