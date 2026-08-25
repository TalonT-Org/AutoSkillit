"""Smoke-utils tests relocated from the former monolith."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

pytestmark = [pytest.mark.medium]


def test_diagnose_merge_gate_writes_diagnosis_file(tmp_path: object) -> None:
    """callable with test_stdout/test_stderr writes diagnosis file with correct format."""
    from pathlib import Path

    from autoskillit.smoke_utils._merge_gate_diagnosis import diagnose_merge_gate

    output_dir = tmp_path  # type: ignore[union-attr]
    result = diagnose_merge_gate(
        test_stdout="FAILED tests/test_foo.py::test_bar - AssertionError\n1 failed in 0.5s",
        test_stderr="",
        output_dir=str(output_dir),
    )
    diag_path = Path(result["diagnosis_path"])
    assert diag_path.exists()
    content = diag_path.read_text()
    assert "failure_subtype = " in content
    assert "## Classification" in content
    assert "## Failed Tests" in content
    assert "## Structured Output" in content


def test_diagnose_merge_gate_extracts_failure_subtype(tmp_path: object) -> None:
    """Callable classifies failure_subtype from pytest output."""
    from autoskillit.smoke_utils._merge_gate_diagnosis import diagnose_merge_gate

    output_dir = tmp_path  # type: ignore[union-attr]

    result_det = diagnose_merge_gate(
        test_stdout="FAILED tests/test_foo.py::test_bar - AssertionError",
        test_stderr="",
        output_dir=str(output_dir),
    )
    from pathlib import Path

    content = Path(result_det["diagnosis_path"]).read_text()
    assert "failure_subtype = deterministic" in content

    result_timeout = diagnose_merge_gate(
        test_stdout="TimeoutError: timed out waiting for 30s",
        test_stderr="",
        output_dir=str(output_dir),
    )
    content_t = Path(result_timeout["diagnosis_path"]).read_text()
    assert "failure_subtype = timing_race" in content_t


def test_diagnose_merge_gate_structured_outer_timeout_wins_over_pytest_output(
    tmp_path: Path,
) -> None:
    """Structured timeout provenance must not be mistaken for a failing test."""
    from autoskillit.smoke_utils._merge_gate_diagnosis import diagnose_merge_gate

    artifact_path = "/tmp/test-check/raw-output.json"
    result = diagnose_merge_gate(
        test_stdout="FAILED tests/test_timeout.py::test_gate - AssertionError",
        test_stderr="",
        output_dir=str(tmp_path),
        failed_step="test_gate",
        timed_out=True,
        outer_timeout_seconds=900.0,
        raw_output_artifact_path=artifact_path,
    )

    content = Path(result["diagnosis_path"]).read_text()
    assert "failure_subtype = outer_timeout" in content
    assert "outer_timeout_seconds = 900.0" in content
    assert f"raw_output_artifact_path = {artifact_path}" in content


@pytest.mark.parametrize(
    ("captured_timed_out", "expected_subtype"),
    [("true", "outer_timeout"), ("false", "deterministic")],
)
def test_diagnose_merge_gate_run_python_normalizes_captured_timeout_values(
    tmp_path: Path,
    captured_timed_out: str,
    expected_subtype: str,
) -> None:
    """run_python coerces recipe-captured boolean and numeric timeout values."""
    from autoskillit.server.tools._execution_helpers import _import_and_call

    result = asyncio.run(
        _import_and_call(
            "autoskillit.smoke_utils.diagnose_merge_gate",
            args={
                "test_stdout": "FAILED tests/test_example.py::test_gate - AssertionError",
                "test_stderr": "",
                "output_dir": str(tmp_path),
                "failed_step": "test_gate",
                "timed_out": captured_timed_out,
                "outer_timeout_seconds": "900.0",
                "raw_output_artifact_path": "/tmp/test-check/raw-output.json",
            },
        )
    )

    assert result["success"] is True
    diagnosis = result["result"]
    assert isinstance(diagnosis, dict)
    diagnosis_path = diagnosis.get("diagnosis_path")
    assert isinstance(diagnosis_path, str)
    content = Path(diagnosis_path).read_text()
    assert f"failure_subtype = {expected_subtype}" in content
    if captured_timed_out == "true":
        assert "outer_timeout_seconds = 900.0" in content
        assert "raw_output_artifact_path = /tmp/test-check/raw-output.json" in content


def test_diagnose_merge_gate_dirty_tree_step(tmp_path: object) -> None:
    """When failed_step is dirty_tree, subtype must be dirty_tree not unknown."""
    from pathlib import Path

    from autoskillit.smoke_utils._merge_gate_diagnosis import diagnose_merge_gate

    output_dir = tmp_path  # type: ignore[union-attr]
    result = diagnose_merge_gate(
        test_stdout="",
        test_stderr="",
        output_dir=str(output_dir),
        failed_step="dirty_tree",
    )
    content = Path(result["diagnosis_path"]).read_text()
    assert "failure_subtype = dirty_tree" in content
    assert "failure_type = pre_test" in content


def test_diagnose_merge_gate_test_gate_empty_output(tmp_path: object) -> None:
    """test_gate with empty output means collection failed, not unknown."""
    from pathlib import Path

    from autoskillit.smoke_utils._merge_gate_diagnosis import diagnose_merge_gate

    output_dir = tmp_path  # type: ignore[union-attr]
    result = diagnose_merge_gate(
        test_stdout="",
        test_stderr="",
        output_dir=str(output_dir),
        failed_step="test_gate",
    )
    content = Path(result["diagnosis_path"]).read_text()
    assert "failure_subtype = no_test_output" in content
    assert "failure_type = test" in content


def test_diagnose_merge_gate_test_gate_with_output(tmp_path: object) -> None:
    """test_gate with FAILED lines still classifies as deterministic."""
    from pathlib import Path

    from autoskillit.smoke_utils._merge_gate_diagnosis import diagnose_merge_gate

    output_dir = tmp_path  # type: ignore[union-attr]
    result = diagnose_merge_gate(
        test_stdout="FAILED tests/test_x.py::test_y",
        test_stderr="",
        output_dir=str(output_dir),
        failed_step="test_gate",
    )
    content = Path(result["diagnosis_path"]).read_text()
    assert "failure_subtype = deterministic" in content
    assert "failure_type = test" in content


def test_diagnose_merge_gate_handles_empty_output(tmp_path: object) -> None:
    """callable with empty/absent test output returns graceful fallback."""
    from pathlib import Path

    from autoskillit.smoke_utils._merge_gate_diagnosis import diagnose_merge_gate

    output_dir = tmp_path  # type: ignore[union-attr]
    result = diagnose_merge_gate(test_stdout="", test_stderr="", output_dir=str(output_dir))
    diag_path = Path(result["diagnosis_path"])
    assert diag_path.exists()
    content = diag_path.read_text()
    assert "failure_subtype = no_test_output" in content
    assert "failure_type = test" in content


def test_diagnose_merge_gate_returns_ci_conclusion_failure(tmp_path: object) -> None:
    """Return dict has ci_conclusion='failure' and diagnosis_path pointing to existing file."""
    from pathlib import Path

    from autoskillit.smoke_utils._merge_gate_diagnosis import diagnose_merge_gate

    output_dir = tmp_path  # type: ignore[union-attr]
    result = diagnose_merge_gate(
        test_stdout="FAILED tests/test_x.py::test_y",
        test_stderr="",
        output_dir=str(output_dir),
    )
    assert result["ci_conclusion"] == "failure"
    assert Path(result["diagnosis_path"]).exists()


def test_diagnose_merge_gate_rejects_empty_output_dir() -> None:
    """diagnose_merge_gate must raise ValueError when output_dir is empty."""
    from autoskillit.smoke_utils._merge_gate_diagnosis import diagnose_merge_gate

    with pytest.raises(ValueError, match="output_dir must be absolute"):
        diagnose_merge_gate(test_stdout="FAILED test_foo", test_stderr="")
