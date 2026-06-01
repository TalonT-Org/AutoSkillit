"""Tests for report renderer exit behavior."""

import pytest

import autoskillit.report.renderer as _renderer

pytestmark = [pytest.mark.small]


def test_renderer_exits_nonzero_on_too_few_args(monkeypatch: pytest.MonkeyPatch) -> None:
    """renderer.main() must exit non-zero when called with fewer than 3 arguments."""
    monkeypatch.setattr("sys.argv", ["renderer.py"])
    with pytest.raises(SystemExit) as exc_info:
        _renderer.main()
    assert exc_info.value.code != 0, (
        "renderer.main() must exit non-zero with too few args, "
        f"got exit code {exc_info.value.code}"
    )


def test_renderer_exits_nonzero_on_missing_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """renderer.main() must exit non-zero when report_path does not exist."""
    nonexistent_report = tmp_path / "nonexistent" / "report.md"
    monkeypatch.setattr(
        "sys.argv",
        ["renderer.py", str(nonexistent_report), str(tmp_path / "out.html")],
    )
    with pytest.raises(SystemExit) as exc_info:
        _renderer.main()
    assert exc_info.value.code != 0, (
        "renderer.main() must exit non-zero when report does not exist, "
        f"got exit code {exc_info.value.code}"
    )
