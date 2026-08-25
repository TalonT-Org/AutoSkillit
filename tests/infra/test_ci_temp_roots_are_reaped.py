"""B10: every /dev/shm scratch root the CI workflows create is reaped via the lifecycle
script, not left as a bare `mkdir -p "$TMPDIR"` with no reclamation coverage."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from autoskillit.core.io import load_yaml

pytestmark = [pytest.mark.layer("infra"), pytest.mark.small]

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"

_TARGET_WORKFLOWS = (
    "conformance-probes.yml",
    "coverage-oracle.yml",
    "test-filter-audit.yml",
)

_MKDIR_TMPDIR = re.compile(r'mkdir\s+-p\s+"\$TMPDIR"')
_REAP_INVOCATION = re.compile(r"pytest_tmp_lifecycle\.py\s+reap\s+--root")


def _run_step_scripts(workflow_path: Path) -> list[str]:
    data = load_yaml(workflow_path)
    scripts: list[str] = []
    for job in data["jobs"].values():
        for step in job.get("steps", []):
            run = step.get("run")
            if isinstance(run, str):
                scripts.append(run)
    return scripts


@pytest.mark.parametrize("workflow_name", _TARGET_WORKFLOWS)
def test_every_mkdir_tmpdir_is_preceded_by_a_reap(workflow_name: str) -> None:
    """No bare `mkdir -p "$TMPDIR"` step may exist without a preceding lifecycle-script
    reap of the same root in the same shell step."""
    workflow_path = WORKFLOWS / workflow_name
    for script in _run_step_scripts(workflow_path):
        if not _MKDIR_TMPDIR.search(script):
            continue
        mkdir_line_index = next(
            i for i, line in enumerate(script.splitlines()) if _MKDIR_TMPDIR.search(line)
        )
        preceding = "\n".join(script.splitlines()[:mkdir_line_index])
        assert _REAP_INVOCATION.search(preceding), (
            f'{workflow_name}: mkdir -p "$TMPDIR" with no preceding '
            "pytest_tmp_lifecycle.py reap in the same step:\n{script}"
        )


@pytest.mark.parametrize("workflow_name", _TARGET_WORKFLOWS)
def test_reap_root_matches_the_platform_branch(workflow_name: str) -> None:
    """conformance-probes.yml's claude-probe job is a Linux/macOS matrix; the reap root must
    follow matrix.probe-tmpdir, not hardcode /dev/shm (which reaps nothing on the macOS leg).
    """
    workflow_path = WORKFLOWS / workflow_name
    for script in _run_step_scripts(workflow_path):
        for line in script.splitlines():
            if not _REAP_INVOCATION.search(line):
                continue
            assert '--root "$(dirname "$TMPDIR")"' in line, (
                f"{workflow_name}: reap call must derive its root from $TMPDIR (which "
                f"already follows the platform/matrix branch), not a hardcoded literal: "
                f"{line!r}"
            )


def test_the_three_ci_root_prefixes_are_reachable_by_the_reaper() -> None:
    """pytest-probes/pytest-coverage/pytest-audit must match the reaper's legacy-prefix
    scan, or a reap call against their platform root reclaims nothing."""
    import sys

    sys.path.insert(0, str(REPO_ROOT / "src"))
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "pytest_tmp_lifecycle", REPO_ROOT / "scripts" / "pytest_tmp_lifecycle.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    for name in ("pytest-probes", "pytest-coverage", "pytest-audit"):
        assert name.startswith(module._LEGACY_PREFIXES), (
            f"{name} does not match any _LEGACY_PREFIXES entry -- reap would never see it"
        )
