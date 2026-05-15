"""Tests for report renderer exit behavior."""

import pytest


def test_renderer_exits_nonzero_on_too_few_args(monkeypatch: pytest.MonkeyPatch) -> None:
    """renderer.main() must exit non-zero when called with fewer than 3 arguments."""
    import autoskillit.report.renderer as _renderer

    monkeypatch.setattr("sys.argv", ["renderer.py"])
    with pytest.raises(SystemExit) as exc_info:
        _renderer.main()
    assert exc_info.value.code != 0, (
        f"renderer.main() must exit non-zero with too few args, got exit code {exc_info.value.code}"
    )


def test_renderer_exits_nonzero_on_missing_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """renderer.main() must exit non-zero when report_path does not exist."""
    import autoskillit.report.renderer as _renderer

    nonexistent_report = tmp_path / "nonexistent" / "report.md"
    monkeypatch.setattr(
        "sys.argv",
        ["renderer.py", str(nonexistent_report), str(tmp_path / "out.html")],
    )
    with pytest.raises(SystemExit) as exc_info:
        _renderer.main()
    assert exc_info.value.code != 0, (
        f"renderer.main() must exit non-zero when report does not exist, got exit code {exc_info.value.code}"
    )
