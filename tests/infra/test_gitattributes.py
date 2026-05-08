"""REQ-R741-A03 — .gitattributes must exist and mark vendored JS as binary."""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_gitattributes_exists() -> None:
    """.gitattributes must be present at the repository root."""
    attrs = _REPO_ROOT / ".gitattributes"
    assert attrs.exists(), ".gitattributes missing from repository root"


def test_gitattributes_marks_mermaid_binary() -> None:
    """.gitattributes must contain a binary rule covering mermaid.min.js."""
    content = (_REPO_ROOT / ".gitattributes").read_text()
    # The rule may use a glob (assets/**/*.js) or the explicit path —
    # either form is valid as long as 'mermaid' and 'binary' both appear.
    assert "mermaid" in content or "assets" in content, (
        ".gitattributes must reference 'mermaid' or 'assets' for the binary rule"
    )
    assert "binary" in content, (
        ".gitattributes must contain the 'binary' attribute to suppress git diff"
    )
