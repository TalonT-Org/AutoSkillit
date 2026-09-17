"""Root instruction contract for atomic review publication."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.small

_ROOT_AGENTS = Path(__file__).parents[2] / "AGENTS.md"


def _github_api_discipline() -> str:
    text = _ROOT_AGENTS.read_text()
    start = text.index("### **3.3. GitHub API Call Discipline**")
    end = text.index("### **3.4.", start)
    return re.sub(r"\s+", " ", text[start:end]).lower()


def test_review_finding_publication_is_unconditionally_batch_only() -> None:
    section = _github_api_discipline()

    assert "batch-only review findings" in section
    assert "post /pulls/{n}/reviews" in section
    assert "comments[]" in section
    assert "never post findings individually" in section
    assert "never a file-level comment fallback" in section
    assert "batch of one is not a permitted workaround" in section

    for failure_exception in (
        "unless the batch call fails",
        "if the batch call fails",
        "when the batch call fails",
        "after the batch call fails",
    ):
        assert failure_exception not in section
