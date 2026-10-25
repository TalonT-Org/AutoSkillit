"""Keep the server receipt path and filename formatter in their owners."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from autoskillit.core import github_review_receipt_filename

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src" / "autoskillit"


def _files_containing(token: str) -> set[str]:
    return {
        path.relative_to(_SOURCE_ROOT).as_posix()
        for path in _SOURCE_ROOT.rglob("*.py")
        if token in path.read_text(encoding="utf-8")
    }


def test_receipt_filename_literal_is_owned_by_its_core_definition() -> None:
    assert _files_containing("batch_review_response_") == {
        "core/types/github/_type_github_review.py"
    }


def test_review_skill_receipt_names_match_the_core_formatter() -> None:
    skill = (_SOURCE_ROOT / "skills_extended" / "review-pr" / "SKILL.md").read_text()
    templates = re.findall(r"`(batch_review_response_[^`]*\.json)`", skill)
    assert templates
    for template in templates:
        assert template.replace("${pr_number}", "42").replace(
            "{pr_number}", "42"
        ) == github_review_receipt_filename(42)


def test_receipt_filename_formatter_has_only_the_three_declared_call_sites() -> None:
    assert _files_containing("github_review_receipt_filename") == {
        "core/types/github/_type_github_review.py",
        "server/tools/tools_pr_ops.py",
        "smoke_utils/review/_publication.py",
    }
