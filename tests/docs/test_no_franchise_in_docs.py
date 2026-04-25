from pathlib import Path

DOCS_ROOT = Path(__file__).parents[2] / "docs"


def test_docs_no_franchise_references():
    """All docs/*.md files must use fleet terminology, not franchise."""
    hits = []
    for md_file in DOCS_ROOT.rglob("*.md"):
        text = md_file.read_text(encoding="utf-8", errors="ignore")
        if "franchise" in text.lower():
            lines = [
                f"  {md_file.relative_to(DOCS_ROOT)}:{i+1}: {line.rstrip()}"
                for i, line in enumerate(text.splitlines())
                if "franchise" in line.lower()
            ]
            hits.extend(lines)
    assert not hits, "Found franchise references in docs:\n" + "\n".join(hits)
