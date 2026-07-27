"""Focused tests for pipeline module cascades and context-ledger integration routes."""

from __future__ import annotations

from pathlib import Path

import pytest

import tests._test_filter as test_filter
from tests._test_filter import (
    LAYER_CASCADE_CONSERVATIVE,
    MODULE_CASCADE_PIPELINE,
    FilterMode,
    FullRunReason,
    build_test_scope,
)

pytestmark = [pytest.mark.medium]

_TEST_DIRS = (
    "arch",
    "contracts",
    "pipeline",
    "server",
    "execution",
    "infra",
    "cli",
    "fleet",
    "smoke_utils",
)


def _tests_root(tmp_path: Path) -> Path:
    tests_root = tmp_path / "tests"
    for directory in _TEST_DIRS:
        (tests_root / directory).mkdir(parents=True)
    return tests_root


def test_pipeline_module_cascade_has_exact_context_keys() -> None:
    assert set(MODULE_CASCADE_PIPELINE) == {"context", "context_admission_ledger"}
    assert MODULE_CASCADE_PIPELINE["context_admission_ledger"] == frozenset({"pipeline", "server"})
    assert MODULE_CASCADE_PIPELINE["context"] == frozenset(
        {
            "pipeline",
            "execution",
            "server",
            "infra",
            "cli",
            "fleet",
            "smoke_utils",
        }
    )


def test_ledger_module_uses_narrow_pipeline_server_route(tmp_path: Path) -> None:
    result = build_test_scope(
        changed_files={"src/autoskillit/pipeline/context_admission_ledger.py"},
        mode=FilterMode.CONSERVATIVE,
        tests_root=_tests_root(tmp_path),
    )

    assert isinstance(result, set)
    dir_names = {path.name for path in result if path.is_dir()}
    assert {"pipeline", "server"} <= dir_names
    assert not ({"execution", "infra", "cli", "fleet", "smoke_utils"} & dir_names)


def test_tool_context_module_uses_complete_declared_route(tmp_path: Path) -> None:
    result = build_test_scope(
        changed_files={"src/autoskillit/pipeline/context.py"},
        mode=FilterMode.CONSERVATIVE,
        tests_root=_tests_root(tmp_path),
    )

    assert isinstance(result, set)
    dir_names = {path.name for path in result if path.is_dir()}
    assert MODULE_CASCADE_PIPELINE["context"] <= dir_names


def test_ledger_reexport_closure_stays_narrow(tmp_path: Path) -> None:
    tests_root = _tests_root(tmp_path)
    pipeline_root = tmp_path / "src" / "autoskillit" / "pipeline"
    pipeline_root.mkdir(parents=True)
    (pipeline_root / "__init__.py").write_text(
        "from .context_admission_ledger import DefaultContextAdmissionLedger\n",
        encoding="utf-8",
    )
    (pipeline_root / "context_admission_ledger.py").write_text(
        "class DefaultContextAdmissionLedger: ...\n",
        encoding="utf-8",
    )

    result = build_test_scope(
        changed_files={"src/autoskillit/pipeline/context_admission_ledger.py"},
        mode=FilterMode.CONSERVATIVE,
        tests_root=tests_root,
        cwd=tmp_path,
    )

    assert isinstance(result, set)
    dir_names = {path.name for path in result if path.is_dir()}
    assert {"pipeline", "server"} <= dir_names
    assert "execution" not in dir_names


def test_content_aware_path_keeps_ledger_route(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(test_filter, "check_bucket_a_content_aware", lambda *_args: False)

    result = build_test_scope(
        changed_files={"src/autoskillit/pipeline/context_admission_ledger.py"},
        mode=FilterMode.CONSERVATIVE,
        tests_root=_tests_root(tmp_path),
        cwd=tmp_path,
        base_ref="develop",
    )

    assert isinstance(result, set)
    dir_names = {path.name for path in result if path.is_dir()}
    assert {"pipeline", "server"} <= dir_names
    assert "execution" not in dir_names


def _materialize_pipeline_fail_open_route(tests_root: Path) -> set[Path]:
    expected = {tests_root / entry for entry in LAYER_CASCADE_CONSERVATIVE["pipeline"]}
    for path in expected:
        if path.suffix:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
    return expected


def test_unknown_pipeline_module_uses_full_fail_open_route(tmp_path: Path) -> None:
    tests_root = _tests_root(tmp_path)
    expected = _materialize_pipeline_fail_open_route(tests_root)
    result = build_test_scope(
        changed_files={"src/autoskillit/pipeline/future_module.py"},
        mode=FilterMode.CONSERVATIVE,
        tests_root=tests_root,
    )

    assert isinstance(result, set)
    assert expected <= result


def test_content_aware_unknown_pipeline_module_uses_full_fail_open_route(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(test_filter, "check_bucket_a_content_aware", lambda *_args: False)
    tests_root = _tests_root(tmp_path)
    expected = _materialize_pipeline_fail_open_route(tests_root)

    result = build_test_scope(
        changed_files={"src/autoskillit/pipeline/future_module.py"},
        mode=FilterMode.CONSERVATIVE,
        tests_root=tests_root,
        cwd=tmp_path,
        base_ref="develop",
    )

    assert isinstance(result, set)
    assert expected <= result


@pytest.mark.parametrize(
    "changed_file",
    ["tests/conftest.py", "src/autoskillit/server/_factory.py"],
)
def test_global_composition_files_remain_bucket_a(
    tmp_path: Path,
    changed_file: str,
) -> None:
    assert (
        build_test_scope(
            changed_files={changed_file},
            mode=FilterMode.CONSERVATIVE,
            tests_root=_tests_root(tmp_path),
        )
        is FullRunReason.BUCKET_A
    )
