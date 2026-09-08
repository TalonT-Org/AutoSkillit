"""Tests for the reusable REQ-CNST-010 file-length checker."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("infra"), pytest.mark.small]

REPO_ROOT = Path(__file__).resolve().parents[2]
_CHECK_SCRIPT = REPO_ROOT / "scripts" / "check_file_lengths.py"


def _load_check_module():
    spec = importlib.util.spec_from_file_location("check_file_lengths", _CHECK_SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _write_module(src_root: Path, line_count: int, name: str = "candidate.py") -> Path:
    path = src_root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("value = 1\n" * line_count, encoding="utf-8")
    return path


def _configured_module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    mod = _load_check_module()
    monkeypatch.setattr(mod, "SRC_ROOT", tmp_path / "src" / "autoskillit")
    return mod


def test_file_at_hard_cap_passes_without_exemption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = _configured_module(tmp_path, monkeypatch)
    path = _write_module(mod.SRC_ROOT, 750)

    assert mod.check_file(path) is None


def test_oversized_file_without_exemption_reports_hard_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = _configured_module(tmp_path, monkeypatch)
    path = _write_module(mod.SRC_ROOT, 751)

    message = mod.check_file(path)

    assert message is not None
    assert "750-line hard cap" in message


def test_exemption_without_predicate_is_voided(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = _configured_module(tmp_path, monkeypatch)
    path = _write_module(mod.SRC_ROOT, 751)
    monkeypatch.setitem(
        mod._LINE_LIMIT_EXEMPTIONS,
        "candidate.py",
        mod.LineLimitExemption(800, "REQ-CNST-010-E1: temporary rationale"),
    )

    message = mod.check_file(path)

    assert message is not None
    assert "voided" in message


def test_false_exemption_predicate_reports_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = _configured_module(tmp_path, monkeypatch)
    path = _write_module(mod.SRC_ROOT, 751)
    monkeypatch.setitem(
        mod._LINE_LIMIT_EXEMPTIONS,
        "candidate.py",
        mod.LineLimitExemption(
            800,
            "REQ-CNST-010-E2: verifiable rationale",
            predicate=lambda: False,
        ),
    )

    message = mod.check_file(path)

    assert message is not None
    assert "returned False" in message


def test_verified_exemption_within_limit_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = _configured_module(tmp_path, monkeypatch)
    path = _write_module(mod.SRC_ROOT, 751)
    monkeypatch.setitem(
        mod._LINE_LIMIT_EXEMPTIONS,
        "candidate.py",
        mod.LineLimitExemption(
            800,
            "REQ-CNST-010-E3: verifiable rationale",
            predicate=lambda: True,
        ),
    )

    assert mod.check_file(path) is None


def test_file_exceeding_exemption_limit_reports_ceiling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = _configured_module(tmp_path, monkeypatch)
    path = _write_module(mod.SRC_ROOT, 801)
    monkeypatch.setitem(
        mod._LINE_LIMIT_EXEMPTIONS,
        "candidate.py",
        mod.LineLimitExemption(
            800,
            "REQ-CNST-010-E4: verifiable rationale",
            predicate=lambda: True,
        ),
    )

    message = mod.check_file(path)

    assert message is not None
    assert "exemption ceiling of 800" in message


def test_exemption_limit_above_absolute_cap_is_rejected_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = _configured_module(tmp_path, monkeypatch)
    path = _write_module(mod.SRC_ROOT, 751)
    monkeypatch.setitem(
        mod._LINE_LIMIT_EXEMPTIONS,
        "candidate.py",
        mod.LineLimitExemption(
            1200,
            "REQ-CNST-010-E5: invalid ceiling",
            predicate=lambda: True,
        ),
    )

    message = mod.check_file(path)

    assert message is not None
    assert "1000-line absolute maximum" in message


def test_main_reports_violations_and_is_silent_on_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    mod = _configured_module(tmp_path, monkeypatch)
    oversized = _write_module(mod.SRC_ROOT, 751, "oversized.py")
    compliant = _write_module(mod.SRC_ROOT, 750, "compliant.py")

    assert mod.main([str(oversized)]) == 1
    failure_output = capsys.readouterr().out
    assert "File-length violations (REQ-CNST-010)" in failure_output
    assert "oversized.py" in failure_output
    assert "Total: 1 violation(s)" in failure_output

    assert mod.main([str(compliant)]) == 0
    assert capsys.readouterr().out == ""


def test_main_is_silent_for_empty_and_missing_inputs(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    mod = _load_check_module()

    assert mod.main([]) == 0
    assert capsys.readouterr().out == ""

    assert mod.main([str(tmp_path / "missing.py")]) == 0
    assert capsys.readouterr().out == ""


def test_main_aggregates_multiple_violations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    mod = _configured_module(tmp_path, monkeypatch)
    first = _write_module(mod.SRC_ROOT, 751, "first.py")
    second = _write_module(mod.SRC_ROOT, 751, "second.py")

    assert mod.main([str(first), str(second)]) == 1
    output = capsys.readouterr().out
    assert "first.py" in output
    assert "second.py" in output
    assert "Total: 2 violation(s)" in output


def test_path_outside_source_root_is_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = _configured_module(tmp_path, monkeypatch)
    path = _write_module(tmp_path / "outside", 751)

    assert mod.check_file(path) is None
