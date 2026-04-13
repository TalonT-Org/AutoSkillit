"""Tests for the bundle-local-report skill renderer."""

import re
import subprocess
import sys
from pathlib import Path

import pytest

SKILL_MD = Path("src/autoskillit/skills_extended/bundle-local-report/SKILL.md")
MERMAID_JS = Path("src/autoskillit/assets/mermaid/mermaid.min.js")
MERMAID_VERSION = Path("src/autoskillit/assets/mermaid/VERSION")


def _extract_renderer(tmp_path: Path) -> Path:
    """Extract the embedded Python renderer from SKILL.md to a temp file."""
    text = SKILL_MD.read_text()
    match = re.search(
        r"```python\n# bundle-local-report renderer\n(.*?)```",
        text,
        re.DOTALL,
    )
    assert match, "SKILL.md must contain a fenced python block starting with '# bundle-local-report renderer'"
    script = match.group(0).lstrip("```python\n").rstrip("\n```")
    out = tmp_path / "renderer.py"
    out.write_text(script)
    return out


def _run_renderer(
    renderer: Path,
    research_dir: Path,
    report_path: Path,
    diagram_paths: str,
    viz_plan_path: Path,
) -> tuple[int, str, str]:
    result = subprocess.run(
        [
            sys.executable,
            str(renderer),
            str(research_dir),
            str(report_path),
            diagram_paths,
            str(viz_plan_path),
        ],
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stdout, result.stderr


def test_renders_minimal_report(tmp_path: Path) -> None:
    """One-paragraph markdown + zero diagrams → HTML has mermaid init + paragraph."""
    research_dir = tmp_path / "research"
    research_dir.mkdir()
    report = research_dir / "README.md"
    report.write_text("Hello world paragraph.\n")
    viz_plan = research_dir / "visualization-plan.md"
    viz_plan.write_text("")
    # Copy mermaid assets so renderer can find them
    (research_dir / "mermaid.min.js").write_text("/* stub */")

    renderer = _extract_renderer(tmp_path)
    rc, stdout, stderr = _run_renderer(renderer, research_dir, report, "", viz_plan)
    assert rc == 0, stderr

    html_path = research_dir / "report.html"
    assert html_path.exists()
    html = html_path.read_text()
    assert "mermaid.initialize" in html
    assert "Hello world paragraph" in html


def test_renders_with_mermaid_diagram(tmp_path: Path) -> None:
    """markdown + one valid exp-lens diagram → HTML body has <pre class='mermaid'>."""
    research_dir = tmp_path / "research"
    research_dir.mkdir()
    report = research_dir / "README.md"
    report.write_text("# Report\n\nBody text.\n")
    viz_plan = research_dir / "visualization-plan.md"
    viz_plan.write_text("")
    (research_dir / "mermaid.min.js").write_text("/* stub */")

    diag = tmp_path / "diag.md"
    diag.write_text(
        "```mermaid\ngraph LR\n  treatment --> outcome\n  hypothesis --> causal\n```\n"
    )

    renderer = _extract_renderer(tmp_path)
    rc, stdout, stderr = _run_renderer(
        renderer, research_dir, report, str(diag), viz_plan
    )
    assert rc == 0, stderr

    html = (research_dir / "report.html").read_text()
    assert '<pre class="mermaid">' in html
    assert "treatment" in html


def test_skips_invalid_mermaid_diagram(tmp_path: Path) -> None:
    """Diagram with <2 validation keywords is silently skipped (no mermaid block in HTML)."""
    research_dir = tmp_path / "research"
    research_dir.mkdir()
    report = research_dir / "README.md"
    report.write_text("# Report\n")
    viz_plan = research_dir / "visualization-plan.md"
    viz_plan.write_text("")
    (research_dir / "mermaid.min.js").write_text("/* stub */")

    diag = tmp_path / "diag_invalid.md"
    diag.write_text("```mermaid\ngraph LR\n  A --> B\n```\n")  # no validation keywords

    renderer = _extract_renderer(tmp_path)
    rc, stdout, stderr = _run_renderer(
        renderer, research_dir, report, str(diag), viz_plan
    )
    assert rc == 0, stderr

    html = (research_dir / "report.html").read_text()
    assert '<pre class="mermaid">' not in html


def test_images_inserted_from_figure_spec(tmp_path: Path) -> None:
    """figure-spec YAML in viz plan → HTML has <img> with correct src/alt at section."""
    research_dir = tmp_path / "research"
    research_dir.mkdir()
    images_dir = research_dir / "images"
    images_dir.mkdir()
    (images_dir / "fig-1.png").write_bytes(b"\x89PNG")

    report = research_dir / "README.md"
    report.write_text("# Report\n\n## Results\n\nSome text here.\n")

    viz_plan = research_dir / "visualization-plan.md"
    viz_plan.write_text(
        "```yaml:figure-spec\n"
        "figure_id: fig-1\n"
        "figure_title: Main Results\n"
        "report_section: Results\n"
        "image_path: images/fig-1.png\n"
        "```\n"
    )
    (research_dir / "mermaid.min.js").write_text("/* stub */")

    renderer = _extract_renderer(tmp_path)
    rc, stdout, stderr = _run_renderer(
        renderer, research_dir, report, "", viz_plan
    )
    assert rc == 0, stderr

    html = (research_dir / "report.html").read_text()
    assert 'src="images/fig-1.png"' in html
    assert 'alt="Main Results"' in html


def test_html_includes_mermaid_version_comment(tmp_path: Path) -> None:
    """Rendered HTML contains a <!-- mermaid ... --> version comment."""
    research_dir = tmp_path / "research"
    research_dir.mkdir()
    report = research_dir / "README.md"
    report.write_text("# Report\n")
    viz_plan = research_dir / "visualization-plan.md"
    viz_plan.write_text("")
    (research_dir / "mermaid.min.js").write_text("/* stub */")

    renderer = _extract_renderer(tmp_path)
    rc, stdout, stderr = _run_renderer(
        renderer, research_dir, report, "", viz_plan
    )
    assert rc == 0, stderr

    html = (research_dir / "report.html").read_text()
    assert "<!-- mermaid " in html
