"""Focused edge cases for the acceptance-policy checker."""

from __future__ import annotations

import pytest

from scripts import check_policy_relaxation as check

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


def test_policy_surface_rejects_unknown_kind() -> None:
    with pytest.raises(check.UnsupportedSurfaceShape, match="unknown surface kind"):
        check.PolicySurface("p.py", "LIMITS", "unknown")


def test_policy_approval_rejects_nonpositive_issue() -> None:
    with pytest.raises(check.UnsupportedSurfaceShape, match="approval issue must be positive"):
        check.PolicyRelaxationApproval("p.py", "LIMITS", None, "1", "2", 0, "reviewer")


def test_exemption_rejects_unknown_keyword() -> None:
    surface = check.PolicySurface("p.py", "EXEMPTIONS", "exemption_map")

    with pytest.raises(check.UnsupportedSurfaceShape, match="unexpected exemption argument"):
        check.extract_surface_values(
            'EXEMPTIONS = {"entry": Exemption(limit=1, owner="team")}',
            surface,
        )


@pytest.mark.parametrize("error", [OSError("denied"), UnicodeError("invalid text")])
def test_main_reports_source_read_errors(
    error: Exception,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail_evaluation(*_args: object) -> list[str]:
        raise error

    monkeypatch.setattr(check, "evaluate", fail_evaluation)

    assert check.main(["--staged", "--repo-root", "."]) == 1
    assert capsys.readouterr().err == (
        f"{check.HUMAN_REQUIRED_MARKER} unable to read policy sources: {error}\n"
    )
