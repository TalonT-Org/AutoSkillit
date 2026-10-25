"""Keep the server receipt path and filename formatter in their owners."""

from __future__ import annotations

from pathlib import Path

import pytest

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


def test_receipt_filename_formatter_has_only_the_three_declared_call_sites() -> None:
    assert _files_containing("github_review_receipt_filename") == {
        "core/types/github/_type_github_review.py",
        "server/tools/tools_pr_ops.py",
        "smoke_utils/review/_publication.py",
    }
