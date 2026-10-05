"""Subprocess tests for the scripts/e2e/e2e_select.py command line the E2E workflow runs."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import production_interpreter_env

pytestmark = [pytest.mark.layer("infra"), pytest.mark.medium]

REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = REPO_ROOT / "scripts" / "e2e" / "e2e_select.py"
REPOSITORY = "TalonT-Org/AutoSkillit"
_CANARY_MATRIX = '{"include":[{"test":"canary","kind":"canary","timeout_minutes":37}]}'
_SHARED_HARNESS_MATRIX = (
    '{"include":[{"test":"canary","kind":"canary","timeout_minutes":37},'
    '{"test":"headless-smoke","kind":"recipe","timeout_minutes":47}]}'
)
_HEADLESS_SMOKE_MATRIX = (
    '{"include":[{"test":"headless-smoke","kind":"recipe","timeout_minutes":47}]}'
)


def _run(
    *args: str, tmp_path: Path, event_name: str, payload: object
) -> subprocess.CompletedProcess[str]:
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(payload), encoding="utf-8")
    env = production_interpreter_env()
    env["GITHUB_EVENT_NAME"] = event_name
    env["GITHUB_EVENT_PATH"] = str(event_path)
    env["GITHUB_REPOSITORY"] = REPOSITORY
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _outputs(stdout: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in stdout.splitlines())


def _pull_request(*, changed_files: int, additions: int, labels: tuple[str, ...] = ()) -> dict:
    return {
        "pull_request": {
            "number": 5230,
            "head": {"sha": "0" * 40, "repo": {"full_name": REPOSITORY}},
            "changed_files": changed_files,
            "additions": additions,
            "deletions": 0,
            "labels": [{"name": label} for label in labels],
        }
    }


def _select(tmp_path: Path, changed_paths: Path, event_name: str, payload: object):
    return _run(
        "select",
        "--changed-paths",
        str(changed_paths),
        tmp_path=tmp_path,
        event_name=event_name,
        payload=payload,
    )


def test_shared_harness_pull_request_selection_is_deterministic(
    tmp_path: Path,
) -> None:
    changed = tmp_path / "changed-paths.txt"
    changed.write_text("scripts/e2e/e2e_harness.py\n", encoding="utf-8")
    payload = _pull_request(changed_files=1, additions=5, labels=("e2e",))
    result = _select(tmp_path, changed, "pull_request", payload)
    assert result.returncode == 0, result.stderr
    outputs = _outputs(result.stdout)
    assert outputs["selected"] == "true"
    assert outputs["matrix"] == _SHARED_HARNESS_MATRIX

    repeated = _select(tmp_path, changed, "pull_request", payload)
    assert repeated.returncode == 0, repeated.stderr
    assert _outputs(repeated.stdout)["matrix"] == outputs["matrix"]


def test_headless_source_pull_request_selects_smoke_recipe(tmp_path: Path) -> None:
    changed = tmp_path / "changed-paths.txt"
    changed.write_text("src/autoskillit/execution/headless/session.py\n", encoding="utf-8")
    payload = _pull_request(changed_files=1, additions=5, labels=("e2e",))
    result = _select(tmp_path, changed, "pull_request", payload)
    assert result.returncode == 0, result.stderr
    outputs = _outputs(result.stdout)
    assert outputs["selected"] == "true"
    assert outputs["matrix"] == _HEADLESS_SMOKE_MATRIX


def test_small_unlabelled_pull_request_selects_nothing(tmp_path: Path) -> None:
    changed = tmp_path / "changed-paths.txt"
    changed.write_text("README.md\n", encoding="utf-8")
    result = _select(
        tmp_path, changed, "pull_request", _pull_request(changed_files=3, additions=15)
    )
    assert result.returncode == 0, result.stderr
    outputs = _outputs(result.stdout)
    assert outputs["selected"] == "false"
    assert outputs["matrix"] == '{"include":[]}'


def test_merge_group_never_reads_the_changed_paths_file(tmp_path: Path) -> None:
    result = _select(tmp_path, tmp_path / "missing.txt", "merge_group", {"merge_group": {}})
    assert result.returncode == 0, result.stderr
    outputs = _outputs(result.stdout)
    assert outputs["selected"] == "false"
    assert outputs["matrix"] == '{"include":[]}'


def test_dispatch_selects_the_named_test(tmp_path: Path) -> None:
    payload = {"inputs": {"tests": "canary"}}
    result = _select(tmp_path, tmp_path / "missing.txt", "workflow_dispatch", payload)
    assert result.returncode == 0, result.stderr
    outputs = _outputs(result.stdout)
    assert outputs["selected"] == "true"
    assert outputs["matrix"] == _CANARY_MATRIX


def test_dispatch_selects_headless_smoke(tmp_path: Path) -> None:
    payload = {"inputs": {"tests": "headless-smoke"}}
    result = _select(tmp_path, tmp_path / "missing.txt", "workflow_dispatch", payload)
    assert result.returncode == 0, result.stderr
    outputs = _outputs(result.stdout)
    assert outputs["selected"] == "true"
    assert outputs["matrix"] == _HEADLESS_SMOKE_MATRIX


def test_dispatch_selects_both_named_tests_in_request_order(tmp_path: Path) -> None:
    payload = {"inputs": {"tests": "clean-install, canary"}}
    result = _select(tmp_path, tmp_path / "missing.txt", "workflow_dispatch", payload)
    assert result.returncode == 0, result.stderr
    outputs = _outputs(result.stdout)
    assert outputs["selected"] == "true"
    assert outputs["matrix"] == (
        '{"include":[{"test":"clean-install","kind":"clean-install",'
        '"timeout_minutes":37},{"test":"canary","kind":"canary",'
        '"timeout_minutes":37}]}'
    )


def test_dispatch_of_an_unknown_test_fails(tmp_path: Path) -> None:
    payload = {"inputs": {"tests": "nope"}}
    result = _select(tmp_path, tmp_path / "missing.txt", "workflow_dispatch", payload)
    assert result.returncode != 0
    assert result.stdout == ""


def test_pull_request_without_changed_paths_file_fails(tmp_path: Path) -> None:
    payload = _pull_request(changed_files=20, additions=500, labels=("e2e",))
    result = _select(tmp_path, tmp_path / "missing.txt", "pull_request", payload)
    assert result.returncode != 0
    assert result.stdout == ""


@pytest.mark.parametrize(
    ("select_result", "selected", "run_result", "expected"),
    [
        ("success", "false", "skipped", 0),
        ("success", "true", "success", 0),
        ("success", "true", "failure", 1),
        ("success", "true", "skipped", 1),
        ("failure", "", "skipped", 1),
        ("success", "", "", 1),
        ("cancelled", "true", "cancelled", 1),
    ],
)
def test_gate_exit_code(
    tmp_path: Path, select_result: str, selected: str, run_result: str, expected: int
) -> None:
    result = _run(
        "gate",
        "--select-result",
        select_result,
        "--selected",
        selected,
        "--run-result",
        run_result,
        tmp_path=tmp_path,
        event_name="pull_request",
        payload={},
    )
    assert result.returncode == expected, result.stdout + result.stderr
