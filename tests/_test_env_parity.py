"""Test harness environment override registry.

Every env var the test harness sets (via Taskfile.yml ``env:`` blocks on
pytest-running tasks) must be registered here with a justification and,
where it masks production behavior, a parity fixture that undoes it.

The double-bind pincer in ``tests/contracts/test_test_env_parity.py`` asserts:
- Every Taskfile env override is registered (unregistered → fail).
- Every registry entry is still present in the Taskfile (orphan → fail).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class HarnessEnvOverride:
    """One test-harness env var override with its justification."""

    var: str
    value: str
    justification: str
    parity_fixture: str | None


TEST_HARNESS_ENV_OVERRIDES: dict[str, HarnessEnvOverride] = {
    "PYTHONDONTWRITEBYTECODE": HarnessEnvOverride(
        var="PYTHONDONTWRITEBYTECODE",
        value="1",
        justification=(
            "Suppresses bytecode writing across all test processes to "
            "prevent stale .pyc artifacts in source and tmp trees. "
            "Masks the real production behavior where hooks execute "
            "without suppression; production_interpreter_env() is "
            "the parity escape hatch."
        ),
        parity_fixture="production_interpreter_env",
    ),
    "OMP_NUM_THREADS": HarnessEnvOverride(
        var="OMP_NUM_THREADS",
        value="1",
        justification=(
            "Prevents NumPy/OpenBLAS from spawning worker threads "
            "during tests, reducing xdist contention and making "
            "CPU-bound test timing more deterministic."
        ),
        parity_fixture=None,
    ),
    "AUTOSKILLIT_FEATURES__EXPERIMENTAL_ENABLED": HarnessEnvOverride(
        var="AUTOSKILLIT_FEATURES__EXPERIMENTAL_ENABLED",
        value="true",
        justification=(
            "Enables experimental features so test coverage includes "
            "feature-gated code paths that would otherwise be invisible "
            "to the test suite."
        ),
        parity_fixture=None,
    ),
    "AUTOSKILLIT_CLAUDE_EXPLORER_LIVE_GATE": HarnessEnvOverride(
        var="AUTOSKILLIT_CLAUDE_EXPLORER_LIVE_GATE",
        value="1",
        justification=(
            "Enables the explicitly selected Claude explorer live conformance gate; "
            "ordinary test tasks do not set this override."
        ),
        parity_fixture=None,
    ),
}
