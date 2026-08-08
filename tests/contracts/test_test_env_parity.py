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


_ENV_KEY_RE = re.compile(r"^([A-Z][A-Z_0-9]+):(?:\s|$)")


def _taskfile_env_vars(content: str | None = None) -> set[str]:
    """Extract env vars set in Taskfile.yml pytest task env blocks."""
    taskfile_content = _TASKFILE.read_text() if content is None else content
    env_vars: set[str] = set()
    env_indent: int | None = None
    key_indent: int | None = None

    for raw_line in taskfile_content.splitlines():
        stripped = raw_line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw_line) - len(stripped)

        if env_indent is not None and indent <= env_indent:
            env_indent = None
            key_indent = None
        if stripped == "env:":
            env_indent = indent
            key_indent = None
            continue
        if env_indent is None:
            continue

        if key_indent is None:
            key_indent = indent
        if indent != key_indent:
            continue
        match = _ENV_KEY_RE.match(stripped)
        if match is not None:
            env_vars.add(match.group(1))

    return env_vars


def test_taskfile_env_extraction_is_scoped_to_direct_env_entries() -> None:
    content = """
vars:
  NOT_AN_ENV: value
tasks:
  first:
    env:
      FIRST_ENV: value
      COMPLEX_ENV:
        NOT_A_DIRECT_ENTRY: value
    vars:
      ALSO_NOT_ENV: value
  second:
    env:
      SECOND_ENV: value
"""

    assert _taskfile_env_vars(content) == {"COMPLEX_ENV", "FIRST_ENV", "SECOND_ENV"}


# Taskfile vars that are NOT parity concerns — paths, feature toggles,
# and test-specific configuration that never masks a production failure class.
_TASKFILE_NON_PARITY_VARS: frozenset[str] = frozenset(
    {
        "AUTOSKILLIT_EXPLORER_LIVE_GATE",
        "AUTOSKILLIT_TEST_FILTER",
        "CLAUDE_CODE_SMOKE_TEST",
        "CLAUDE_STARTUP_READINESS_SMOKE",
        "CODEX_SMOKE_TEST",
        "PYTEST_CACHEDIR",
        "PYTEST_TMPDIR",
        "RECORD_SCENARIO",
        "RECORD_SCENARIO_DIR",
        "RECORD_SCENARIO_RECIPE",
        "RESEARCH_SMOKE_TEST",
        "SMOKE_TEST",
        "TMPDIR",
    }
)


def test_every_taskfile_override_is_registered() -> None:
    """Unregistered Taskfile env overrides fail — no silent suppression.

    Double-bind pincer: an env var added to the Taskfile's pytest tasks
    that isn't in the non-parity allowlist and isn't in the registry will
    fail this test, forcing the author to either register it with a
    justification or add it to ``_TASKFILE_NON_PARITY_VARS``.
    """
    taskfile_vars = _taskfile_env_vars()
    unregistered = sorted(
        var
        for var in taskfile_vars
        if var not in TEST_HARNESS_ENV_OVERRIDES and var not in _TASKFILE_NON_PARITY_VARS
    )
    assert not unregistered, (
        f"Taskfile env var(s) not registered in TEST_HARNESS_ENV_OVERRIDES "
        f"and not in _TASKFILE_NON_PARITY_VARS — register with a justification "
        f"or add to the non-parity allowlist: {unregistered}"
    )


def test_registry_entries_are_still_in_taskfile() -> None:
    """Every registered override must still appear in the Taskfile."""
    taskfile_vars = _taskfile_env_vars()
    orphans = {var for var in TEST_HARNESS_ENV_OVERRIDES if var not in taskfile_vars}
    assert not orphans, f"Stale TEST_HARNESS_ENV_OVERRIDES entries: {orphans}"


def test_parity_fixtures_exist() -> None:
    """Overrides with a parity fixture must reference a real function."""
    from tests import conftest as test_conftest

    for var, override in TEST_HARNESS_ENV_OVERRIDES.items():
        if override.parity_fixture is None:
            continue
        parity_fixture = getattr(test_conftest, override.parity_fixture, None)

        assert callable(parity_fixture), (
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
