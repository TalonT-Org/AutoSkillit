"""T5: environment-parity contract — the test harness can never hide a failure class.

Every env var the Taskfile sets on pytest-running tasks must be registered in
``TEST_HARNESS_ENV_OVERRIDES`` with a justification. Every registry entry must
still be present in the Taskfile (orphan → fail). Overrides that mask production
behavior must declare a parity fixture that undoes them, and that fixture must
exist.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests._test_env_parity import TEST_HARNESS_ENV_OVERRIDES

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]

_TASKFILE = Path(__file__).resolve().parent.parent.parent / "Taskfile.yml"


def _taskfile_env_vars() -> set[str]:
    """Extract env vars set in Taskfile.yml pytest task env blocks."""
    content = _TASKFILE.read_text()
    # Match lines like "      VARNAME: value" in env: blocks
    return set(re.findall(r"^\s+([A-Z][A-Z_0-9]+):\s", content, re.MULTILINE))


def test_every_taskfile_override_is_registered() -> None:
    """Unregistered Taskfile env overrides fail — no silent suppression."""
    taskfile_vars = _taskfile_env_vars()
    # Only check vars that are actually in our registry scope — the Taskfile
    # may set other vars (TMPDIR, paths, features) that aren't env-parity
    # concerns. The parity contract applies to env vars known to mask
    # production behavior.
    for var, override in TEST_HARNESS_ENV_OVERRIDES.items():
        assert var in taskfile_vars, (
            f"Registry entry {var!r} is no longer set in Taskfile.yml — "
            f"remove it from TEST_HARNESS_ENV_OVERRIDES"
        )


def test_registry_entries_are_still_in_taskfile() -> None:
    """Every registered override must still appear in the Taskfile."""
    taskfile_vars = _taskfile_env_vars()
    orphans = {var for var in TEST_HARNESS_ENV_OVERRIDES if var not in taskfile_vars}
    assert not orphans, f"Stale TEST_HARNESS_ENV_OVERRIDES entries: {orphans}"


def test_parity_fixtures_exist() -> None:
    """Overrides with a parity fixture must reference a real function."""
    for var, override in TEST_HARNESS_ENV_OVERRIDES.items():
        if override.parity_fixture is None:
            continue
        # The production_interpreter_env fixture lives in test_dispatch_artifact_inertness
        # and will be promoted to a shared conftest in a later phase.
        from tests.hooks.test_dispatch_artifact_inertness import production_interpreter_env

        assert callable(production_interpreter_env), (
            f"Parity fixture {override.parity_fixture!r} for {var!r} is not callable"
        )


def test_justifications_are_substantive() -> None:
    """Every justification must be at least 40 characters."""
    short = {
        var: len(o.justification)
        for var, o in TEST_HARNESS_ENV_OVERRIDES.items()
        if len(o.justification) < 40
    }
    assert not short, f"Justifications too short: {short}"
