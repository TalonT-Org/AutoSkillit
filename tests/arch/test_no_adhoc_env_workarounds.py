"""Architectural guard: forbid new ad-hoc ``monkeypatch.delenv(...)`` /
``os.environ.pop(...)`` calls for ambient env vars the central ``_scrub_ambient_env``
autouse fixture (``tests/conftest.py``) already scrubs unconditionally before every test.

Scans every ``tests/**/*.py`` file for two unambiguous call shapes:

- ``<anything>.delenv("VAR", ...)`` — any receiver, since AST alone cannot resolve
  whether a given local name is monkeypatch-typed.
- ``os.environ.pop("VAR", ...)`` — receiver must literally be ``os.environ`` (not an
  arbitrary local dict), which is exactly what distinguishes a real ambient-environment
  workaround from stripping a locally constructed child-process env dict.

A third shape — ``<local-env-dict>.pop(...)`` — is deliberately NOT matched: AST alone
cannot distinguish ``environ.pop(...)`` on a local dict from ``os.environ.pop(...)`` on
the ambient environment without a broad, hand-maintained exception list. The two shapes
above are unambiguous and cover the overwhelming majority of sites; a narrow guard beats
a broad one carrying inferred exceptions.

Any collected var that is ``disposition="scrub"`` in ``AMBIENT_ENV_DISPOSITIONS`` is
already deleted for every test by the central fixture before the test body runs, so a
per-test delenv/pop call for that var is either dead code or, worse, disguises a real
behavioral input (e.g. "assert X when var is absent") as if it were ambient cleanup.
Sites that ARE a genuine behavioral input — usually paired with a sibling test that
``setenv``s the same var — are declared in ``_INTENTIONAL_ENV_INPUT_SITES`` with a
justification, mirroring the ``_TEMP_PATH_WHITELIST`` pattern in
``test_python_no_hardcoded_temp.py``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests._ambient_env_surface import AMBIENT_ENV_DISPOSITIONS

pytestmark = [pytest.mark.small]

TESTS_ROOT = Path(__file__).resolve().parent.parent

# Keyed by "<repo-relative file>::<VAR_NAME>", not "file:line" — line numbers drift
# across edits. Two kinds of entry: (1) a genuine behavioral test input — usually the
# delenv/pop half of a setenv/delenv pair exercising presence-vs-absence behavior for
# that var — and (2) a pre-existing site outside this change's CLAUDECODE/
# CLAUDE_CODE_EXECPATH remediation scope, grandfathered here rather than silently
# passing, pending a future consolidation pass that assesses it individually.
_INTENTIONAL_ENV_INPUT_SITES: dict[str, str] = {
    "tests/arch/test_codex_env_forward_bridge.py::AUTOSKILLIT_AGENT_BACKEND": (
        "Pre-existing test-owned AUTOSKILLIT_AGENT_BACKEND clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/arch/test_codex_env_forward_bridge.py::AUTOSKILLIT_AGENT_BACKEND__BACKEND": (
        "Pre-existing test-owned AUTOSKILLIT_AGENT_BACKEND__BACKEND clear predating the "
        "central _scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH "
        "remediation scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/arch/test_codex_env_forward_bridge.py::AUTOSKILLIT_CAMPAIGN_ID": (
        "Pre-existing test-owned AUTOSKILLIT_CAMPAIGN_ID clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/arch/test_codex_env_forward_bridge.py::AUTOSKILLIT_KITCHEN_SESSION_ID": (
        "Pre-existing test-owned AUTOSKILLIT_KITCHEN_SESSION_ID clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/arch/test_codex_env_forward_bridge.py::AUTOSKILLIT_MCP_CLIENT_BACKEND": (
        "Pre-existing test-owned AUTOSKILLIT_MCP_CLIENT_BACKEND clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/arch/test_env_symmetry.py::AUTOSKILLIT_AGENT_BACKEND": (
        "Pre-existing test-owned AUTOSKILLIT_AGENT_BACKEND clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/arch/test_env_symmetry.py::AUTOSKILLIT_AGENT_BACKEND__BACKEND": (
        "Pre-existing test-owned AUTOSKILLIT_AGENT_BACKEND__BACKEND clear predating the "
        "central _scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH "
        "remediation scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/arch/test_env_symmetry.py::AUTOSKILLIT_CAMPAIGN_ID": (
        "Pre-existing test-owned AUTOSKILLIT_CAMPAIGN_ID clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/arch/test_env_symmetry.py::AUTOSKILLIT_KITCHEN_SESSION_ID": (
        "Pre-existing test-owned AUTOSKILLIT_KITCHEN_SESSION_ID clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/arch/test_feature_markers.py::AUTOSKILLIT_SESSION_TYPE": (
        "Pre-existing test-owned AUTOSKILLIT_SESSION_TYPE clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/arch/test_mcp_env_forward_coverage.py::AUTOSKILLIT_AGENT_BACKEND": (
        "Pre-existing test-owned AUTOSKILLIT_AGENT_BACKEND clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/arch/test_mcp_env_forward_coverage.py::AUTOSKILLIT_AGENT_BACKEND__BACKEND": (
        "Pre-existing test-owned AUTOSKILLIT_AGENT_BACKEND__BACKEND clear predating the "
        "central _scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH "
        "remediation scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/arch/test_mcp_env_forward_coverage.py::AUTOSKILLIT_CAMPAIGN_ID": (
        "Pre-existing test-owned AUTOSKILLIT_CAMPAIGN_ID clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/arch/test_mcp_env_forward_coverage.py::AUTOSKILLIT_KITCHEN_SESSION_ID": (
        "Pre-existing test-owned AUTOSKILLIT_KITCHEN_SESSION_ID clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/arch/test_mcp_env_forward_coverage.py::AUTOSKILLIT_MCP_CLIENT_BACKEND": (
        "Pre-existing test-owned AUTOSKILLIT_MCP_CLIENT_BACKEND clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/arch/test_skill_capability_registry.py::AUTOSKILLIT_FOOD_TRUCK_TOOL_TAGS": (
        "Pre-existing test-owned AUTOSKILLIT_FOOD_TRUCK_TOOL_TAGS clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/arch/test_skill_capability_registry.py::AUTOSKILLIT_HEADLESS_AUTO_GATE": (
        "Pre-existing test-owned AUTOSKILLIT_HEADLESS_AUTO_GATE clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/cli/_cook_launch_helpers.py::AUTOSKILLIT_CODEX_STARTUP_TRACE": (
        "Pre-existing test-owned AUTOSKILLIT_CODEX_STARTUP_TRACE clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/cli/test_doctor.py::AUTOSKILLIT_CAMPAIGN_ID": (
        "Pre-existing test-owned AUTOSKILLIT_CAMPAIGN_ID clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/cli/test_doctor_backend_guards.py::AUTOSKILLIT_AGENT_BACKEND": (
        "Pre-existing test-owned AUTOSKILLIT_AGENT_BACKEND clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/cli/test_doctor_backend_guards.py::AUTOSKILLIT_AGENT_BACKEND__BACKEND": (
        "Pre-existing test-owned AUTOSKILLIT_AGENT_BACKEND__BACKEND clear predating the "
        "central _scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH "
        "remediation scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/cli/test_doctor_fleet_checks.py::AUTOSKILLIT_CAMPAIGN_ID": (
        "Pre-existing test-owned AUTOSKILLIT_CAMPAIGN_ID clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/cli/test_doctor_fleet_checks.py::AUTOSKILLIT_SESSION_TYPE": (
        "Pre-existing test-owned AUTOSKILLIT_SESSION_TYPE clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/cli/test_init.py::AUTOSKILLIT_AGENT_BACKEND": (
        "Pre-existing test-owned AUTOSKILLIT_AGENT_BACKEND clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/cli/test_init.py::AUTOSKILLIT_AGENT_BACKEND__BACKEND": (
        "Pre-existing test-owned AUTOSKILLIT_AGENT_BACKEND__BACKEND clear predating the "
        "central _scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH "
        "remediation scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/cli/test_init_helpers.py::AUTOSKILLIT_AGENT_BACKEND": (
        "Pre-existing test-owned AUTOSKILLIT_AGENT_BACKEND clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/cli/test_init_helpers.py::AUTOSKILLIT_AGENT_BACKEND__BACKEND": (
        "Pre-existing test-owned AUTOSKILLIT_AGENT_BACKEND__BACKEND clear predating the "
        "central _scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH "
        "remediation scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/cli/test_startup_budget.py::GITHUB_TOKEN": (
        "Pre-existing test-owned GITHUB_TOKEN clear predating the central _scrub_ambient_env "
        "fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation scope of this "
        "change; grandfathered pending a future consolidation pass."
    ),
    "tests/cli/test_update_checks_guards.py::AUTOSKILLIT_SOURCE_REPO": (
        "Pre-existing test-owned AUTOSKILLIT_SOURCE_REPO clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/config/test_agent_backend_config.py::AUTOSKILLIT_AGENT_BACKEND": (
        "Pre-existing test-owned AUTOSKILLIT_AGENT_BACKEND clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/config/test_agent_backend_config.py::AUTOSKILLIT_AGENT_BACKEND__BACKEND": (
        "Pre-existing test-owned AUTOSKILLIT_AGENT_BACKEND__BACKEND clear predating the "
        "central _scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH "
        "remediation scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/contracts/test_backend_prompt_conventions.py::AUTOSKILLIT_CAMPAIGN_ID": (
        "Pre-existing test-owned AUTOSKILLIT_CAMPAIGN_ID clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/contracts/test_backend_prompt_conventions.py::AUTOSKILLIT_KITCHEN_SESSION_ID": (
        "Pre-existing test-owned AUTOSKILLIT_KITCHEN_SESSION_ID clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/core/test_backend_gating_core.py::AUTOSKILLIT_AGENT_BACKEND": (
        "Pre-existing test-owned AUTOSKILLIT_AGENT_BACKEND clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/core/test_kitchen_state.py::AUTOSKILLIT_CAMPAIGN_ID": (
        "Pre-existing test-owned AUTOSKILLIT_CAMPAIGN_ID clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/core/test_kitchen_state.py::AUTOSKILLIT_STATE_DIR": (
        "Pre-existing test-owned AUTOSKILLIT_STATE_DIR clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/core/test_session_provenance.py::AUTOSKILLIT_CAMPAIGN_ID": (
        "Pre-existing test-owned AUTOSKILLIT_CAMPAIGN_ID clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/core/test_session_provenance.py::AUTOSKILLIT_STATE_DIR": (
        "Pre-existing test-owned AUTOSKILLIT_STATE_DIR clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/core/test_session_type.py::AUTOSKILLIT_HEADLESS": (
        "Pre-existing test-owned AUTOSKILLIT_HEADLESS clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/core/test_session_type.py::AUTOSKILLIT_SESSION_TYPE": (
        "Pre-existing test-owned AUTOSKILLIT_SESSION_TYPE clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/core/test_version_snapshot.py::AUTOSKILLIT_AGENT_BACKEND": (
        "Pre-existing test-owned AUTOSKILLIT_AGENT_BACKEND clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/core/test_version_snapshot_codex_routing.py::AUTOSKILLIT_AGENT_BACKEND": (
        "Pre-existing test-owned AUTOSKILLIT_AGENT_BACKEND clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/execution/backends/test_backend_env_injection.py::AUTOSKILLIT_AGENT_BACKEND": (
        "Pre-existing test-owned AUTOSKILLIT_AGENT_BACKEND clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/execution/backends/test_backend_env_injection.py::AUTOSKILLIT_AGENT_BACKEND__BACKEND": (
        "Pre-existing test-owned AUTOSKILLIT_AGENT_BACKEND__BACKEND clear predating the "
        "central _scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH "
        "remediation scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/execution/backends/test_backend_env_injection.py::AUTOSKILLIT_CAMPAIGN_ID": (
        "Pre-existing test-owned AUTOSKILLIT_CAMPAIGN_ID clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/execution/backends/test_backend_env_injection.py::AUTOSKILLIT_KITCHEN_SESSION_ID": (
        "Pre-existing test-owned AUTOSKILLIT_KITCHEN_SESSION_ID clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/execution/backends/test_backend_sandbox_invariants.py::AUTOSKILLIT_CAMPAIGN_ID": (
        "Pre-existing test-owned AUTOSKILLIT_CAMPAIGN_ID clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    (
        "tests/execution/backends/test_backend_sandbox_invariants.py"
        "::AUTOSKILLIT_KITCHEN_SESSION_ID"
    ): (
        "Pre-existing test-owned AUTOSKILLIT_KITCHEN_SESSION_ID clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/execution/backends/test_claude_backend.py::AUTOSKILLIT_CAMPAIGN_ID": (
        "Pre-existing test-owned AUTOSKILLIT_CAMPAIGN_ID clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/execution/backends/test_claude_backend.py::AUTOSKILLIT_KITCHEN_SESSION_ID": (
        "Pre-existing test-owned AUTOSKILLIT_KITCHEN_SESSION_ID clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/execution/backends/test_claude_code_backend.py::AUTOSKILLIT_AGENT_BACKEND": (
        "Pre-existing test-owned AUTOSKILLIT_AGENT_BACKEND clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/execution/backends/test_claude_code_backend.py::CLAUDE_CODE_EXECPATH": (
        "Pre-existing test-owned CLAUDE_CODE_EXECPATH clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/execution/backends/test_codex_backend.py::AUTOSKILLIT_AGENT_BACKEND": (
        "Pre-existing test-owned AUTOSKILLIT_AGENT_BACKEND clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/execution/backends/test_codex_backend.py::AUTOSKILLIT_CAMPAIGN_ID": (
        "Pre-existing test-owned AUTOSKILLIT_CAMPAIGN_ID clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/execution/backends/test_codex_backend.py::AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT": (
        "Pre-existing test-owned AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/execution/backends/test_codex_backend.py::AUTOSKILLIT_KITCHEN_SESSION_ID": (
        "Pre-existing test-owned AUTOSKILLIT_KITCHEN_SESSION_ID clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/execution/backends/test_probe_canary.py::GITHUB_REPOSITORY": (
        "Pre-existing test-owned GITHUB_REPOSITORY clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/execution/test_codex_flag_contracts.py::AUTOSKILLIT_CAMPAIGN_ID": (
        "Pre-existing test-owned AUTOSKILLIT_CAMPAIGN_ID clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/execution/test_codex_flag_contracts.py::AUTOSKILLIT_KITCHEN_SESSION_ID": (
        "Pre-existing test-owned AUTOSKILLIT_KITCHEN_SESSION_ID clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/execution/test_commands_skill_session.py::AUTOSKILLIT_CAMPAIGN_ID": (
        "Pre-existing test-owned AUTOSKILLIT_CAMPAIGN_ID clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/execution/test_commands_skill_session.py::AUTOSKILLIT_KITCHEN_SESSION_ID": (
        "Pre-existing test-owned AUTOSKILLIT_KITCHEN_SESSION_ID clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/execution/test_headless_execute.py::AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT": (
        "Pre-existing test-owned AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/execution/test_headless_path_validation.py::AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT": (
        "Pre-existing test-owned AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/execution/test_idle_output_env.py::AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT": (
        "Pre-existing test-owned AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/execution/test_quota_sleep.py::AUTOSKILLIT_PROVIDER_PROFILE": (
        "Pre-existing test-owned AUTOSKILLIT_PROVIDER_PROFILE clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/execution/test_quota_sleep.py::AUTOSKILLIT_QUOTA_GUARD__DISABLED": (
        "Pre-existing test-owned AUTOSKILLIT_QUOTA_GUARD__DISABLED clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/execution/test_session_log_flush.py::AUTOSKILLIT_CAMPAIGN_ID": (
        "Pre-existing test-owned AUTOSKILLIT_CAMPAIGN_ID clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/execution/test_session_log_flush.py::AUTOSKILLIT_STATE_DIR": (
        "Pre-existing test-owned AUTOSKILLIT_STATE_DIR clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/fleet/test_gate_state_persistence.py::AUTOSKILLIT_CAMPAIGN_STATE_PATH": (
        "Pre-existing test-owned AUTOSKILLIT_CAMPAIGN_STATE_PATH clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/fleet/test_gate_state_persistence.py::AUTOSKILLIT_HEADLESS": (
        "Pre-existing test-owned AUTOSKILLIT_HEADLESS clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/hooks/test_compose_pr_body_guard.py::AUTOSKILLIT_HEADLESS": (
        "Pre-existing test-owned AUTOSKILLIT_HEADLESS clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/hooks/test_github_mutation_guard.py::AUTOSKILLIT_HEADLESS": (
        "Pre-existing test-owned AUTOSKILLIT_HEADLESS clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/hooks/test_github_mutation_guard.py::AUTOSKILLIT_SESSION_TYPE": (
        "Pre-existing test-owned AUTOSKILLIT_SESSION_TYPE clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/hooks/test_hook_config_bridge.py::AUTOSKILLIT_SESSION_DEADLINE": (
        "Pre-existing test-owned AUTOSKILLIT_SESSION_DEADLINE clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/hooks/test_hook_settings.py::AUTOSKILLIT_LOG_DIR": (
        "Pre-existing test-owned AUTOSKILLIT_LOG_DIR clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/hooks/test_lint_after_edit_hook.py::AUTOSKILLIT_HEADLESS": (
        "Pre-existing test-owned AUTOSKILLIT_HEADLESS clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/hooks/test_lint_after_edit_hook.py::AUTOSKILLIT_SKILL_NAME": (
        "Pre-existing test-owned AUTOSKILLIT_SKILL_NAME clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/hooks/test_pipeline_step_guard.py::AUTOSKILLIT_DISPATCH_ID": (
        "Pre-existing test-owned AUTOSKILLIT_DISPATCH_ID clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/hooks/test_pr_create_guard.py::AUTOSKILLIT_SESSION_TYPE": (
        "Pre-existing test-owned AUTOSKILLIT_SESSION_TYPE clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/hooks/test_pr_create_guard.py::AUTOSKILLIT_SKILL_NAME": (
        "Pre-existing test-owned AUTOSKILLIT_SKILL_NAME clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/hooks/test_quota_check.py::AUTOSKILLIT_AGENT_BACKEND": (
        "Pre-existing test-owned AUTOSKILLIT_AGENT_BACKEND clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/hooks/test_quota_check.py::AUTOSKILLIT_LOG_DIR": (
        "Pre-existing test-owned AUTOSKILLIT_LOG_DIR clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/hooks/test_quota_check.py::AUTOSKILLIT_PROVIDER_PROFILE": (
        "Pre-existing test-owned AUTOSKILLIT_PROVIDER_PROFILE clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/hooks/test_quota_check.py::AUTOSKILLIT_QUOTA_GUARD__CACHE_PATH": (
        "Pre-existing test-owned AUTOSKILLIT_QUOTA_GUARD__CACHE_PATH clear predating the "
        "central _scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH "
        "remediation scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/hooks/test_quota_check.py::AUTOSKILLIT_QUOTA_GUARD__DISABLED": (
        "Pre-existing test-owned AUTOSKILLIT_QUOTA_GUARD__DISABLED clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/hooks/test_quota_check.py::AUTOSKILLIT_SESSION_DEADLINE": (
        "Pre-existing test-owned AUTOSKILLIT_SESSION_DEADLINE clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/hooks/test_quota_post_check.py::AUTOSKILLIT_AGENT_BACKEND": (
        "Pre-existing test-owned AUTOSKILLIT_AGENT_BACKEND clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/hooks/test_quota_post_check.py::AUTOSKILLIT_LOG_DIR": (
        "Pre-existing test-owned AUTOSKILLIT_LOG_DIR clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/hooks/test_quota_post_check.py::AUTOSKILLIT_PROVIDER_PROFILE": (
        "Pre-existing test-owned AUTOSKILLIT_PROVIDER_PROFILE clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/hooks/test_quota_post_check.py::AUTOSKILLIT_QUOTA_GUARD__CACHE_PATH": (
        "Pre-existing test-owned AUTOSKILLIT_QUOTA_GUARD__CACHE_PATH clear predating the "
        "central _scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH "
        "remediation scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/hooks/test_quota_post_check.py::AUTOSKILLIT_QUOTA_GUARD__DISABLED": (
        "Pre-existing test-owned AUTOSKILLIT_QUOTA_GUARD__DISABLED clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/hooks/test_quota_post_check.py::AUTOSKILLIT_SESSION_DEADLINE": (
        "Pre-existing test-owned AUTOSKILLIT_SESSION_DEADLINE clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/hooks/test_session_start_reminder.py::AUTOSKILLIT_STATE_DIR": (
        "Pre-existing test-owned AUTOSKILLIT_STATE_DIR clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/hooks/test_shell_capture_hook.py::AUTOSKILLIT_AGENT_BACKEND": (
        "Pre-existing test-owned AUTOSKILLIT_AGENT_BACKEND clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/hooks/test_state_root_resolution.py::AUTOSKILLIT_STATE_ROOT": (
        "Pre-existing test-owned AUTOSKILLIT_STATE_ROOT clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/hooks/test_test_runner_guard.py::AUTOSKILLIT_HEADLESS": (
        "Pre-existing test-owned AUTOSKILLIT_HEADLESS clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/hooks/test_write_guard.py::AUTOSKILLIT_ALLOWED_WRITE_PREFIX": (
        "Pre-existing test-owned AUTOSKILLIT_ALLOWED_WRITE_PREFIX clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/hooks/test_write_guard.py::AUTOSKILLIT_ALLOWED_WRITE_PREFIXES": (
        "Pre-existing test-owned AUTOSKILLIT_ALLOWED_WRITE_PREFIXES clear predating the "
        "central _scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH "
        "remediation scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/hooks/test_write_guard.py::AUTOSKILLIT_CWD": (
        "Pre-existing test-owned AUTOSKILLIT_CWD clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/hooks/test_write_guard.py::AUTOSKILLIT_HEADLESS": (
        "Pre-existing test-owned AUTOSKILLIT_HEADLESS clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/infra/test_open_kitchen_guard.py::AUTOSKILLIT_HEADLESS": (
        "Pre-existing test-owned AUTOSKILLIT_HEADLESS clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/infra/test_open_kitchen_guard.py::AUTOSKILLIT_STATE_DIR": (
        "Pre-existing test-owned AUTOSKILLIT_STATE_DIR clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/integration/test_codex_food_truck.py::AUTOSKILLIT_CAMPAIGN_ID": (
        "Pre-existing test-owned AUTOSKILLIT_CAMPAIGN_ID clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/integration/test_codex_food_truck.py::AUTOSKILLIT_KITCHEN_SESSION_ID": (
        "Pre-existing test-owned AUTOSKILLIT_KITCHEN_SESSION_ID clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/integration/test_guard_composition.py::AUTOSKILLIT_ALLOWED_WRITE_PREFIX": (
        "Pre-existing test-owned AUTOSKILLIT_ALLOWED_WRITE_PREFIX clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/integration/test_guard_composition.py::AUTOSKILLIT_ALLOWED_WRITE_PREFIXES": (
        "Pre-existing test-owned AUTOSKILLIT_ALLOWED_WRITE_PREFIXES clear predating the "
        "central _scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH "
        "remediation scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/integration/test_pipeline_step_completion_flow.py::AUTOSKILLIT_DISPATCH_ID": (
        "Pre-existing test-owned AUTOSKILLIT_DISPATCH_ID clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    (
        "tests/integration/test_write_guard_worktree_integration.py"
        "::AUTOSKILLIT_ALLOWED_WRITE_PREFIX"
    ): (
        "Pre-existing test-owned AUTOSKILLIT_ALLOWED_WRITE_PREFIX clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    (
        "tests/integration/test_write_guard_worktree_integration.py"
        "::AUTOSKILLIT_ALLOWED_WRITE_PREFIXES"
    ): (
        "Pre-existing test-owned AUTOSKILLIT_ALLOWED_WRITE_PREFIXES clear predating the "
        "central _scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH "
        "remediation scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/integration/test_write_guard_worktree_integration.py::AUTOSKILLIT_HEADLESS": (
        "Pre-existing test-owned AUTOSKILLIT_HEADLESS clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/server/test_delivery_e2e_verification.py::AUTOSKILLIT_ATTESTED_CLIENT_GATE_TOKENS": (
        "Pre-existing test-owned AUTOSKILLIT_ATTESTED_CLIENT_GATE_TOKENS clear predating the "
        "central _scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH "
        "remediation scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/server/test_delivery_e2e_verification.py::AUTOSKILLIT_ATTESTED_META_SUPPORT": (
        "Pre-existing test-owned AUTOSKILLIT_ATTESTED_META_SUPPORT clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/server/test_factory.py::AUTOSKILLIT_EXPLORATION_CAPABILITY": (
        "Pre-existing test-owned AUTOSKILLIT_EXPLORATION_CAPABILITY clear predating the "
        "central _scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH "
        "remediation scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/server/test_factory.py::AUTOSKILLIT_HEADLESS": (
        "Pre-existing test-owned AUTOSKILLIT_HEADLESS clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/server/test_factory.py::AUTOSKILLIT_PROVIDER_PROFILE": (
        "Pre-existing test-owned AUTOSKILLIT_PROVIDER_PROFILE clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/server/test_factory.py::GITHUB_TOKEN": (
        "Pre-existing test-owned GITHUB_TOKEN clear predating the central _scrub_ambient_env "
        "fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation scope of this "
        "change; grandfathered pending a future consolidation pass."
    ),
    "tests/server/test_factory.py::RECORD_SCENARIO": (
        "Pre-existing test-owned RECORD_SCENARIO clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/server/test_factory.py::REPLAY_SCENARIO": (
        "Pre-existing test-owned REPLAY_SCENARIO clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/server/test_factory_recording.py::RECORD_SCENARIO": (
        "Pre-existing test-owned RECORD_SCENARIO clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/server/test_factory_recording.py::REPLAY_SCENARIO": (
        "Pre-existing test-owned REPLAY_SCENARIO clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/server/test_helpers_tier_guards.py::AUTOSKILLIT_HEADLESS": (
        "Pre-existing test-owned AUTOSKILLIT_HEADLESS clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/server/test_helpers_tier_guards.py::AUTOSKILLIT_SESSION_TYPE": (
        "Pre-existing test-owned AUTOSKILLIT_SESSION_TYPE clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/server/test_lifespan_fleet_boot.py::AUTOSKILLIT_HEADLESS": (
        "Pre-existing test-owned AUTOSKILLIT_HEADLESS clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/server/test_progress_heartbeat_wiring.py::AUTOSKILLIT_HEADLESS": (
        "Pre-existing test-owned AUTOSKILLIT_HEADLESS clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/server/test_record_pipeline_step.py::AUTOSKILLIT_DISPATCH_ID": (
        "Pre-existing test-owned AUTOSKILLIT_DISPATCH_ID clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/server/test_record_pipeline_step_complete.py::AUTOSKILLIT_DISPATCH_ID": (
        "Pre-existing test-owned AUTOSKILLIT_DISPATCH_ID clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/server/test_session_deadline.py::AUTOSKILLIT_SESSION_DEADLINE": (
        "Pre-existing test-owned AUTOSKILLIT_SESSION_DEADLINE clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/server/test_session_type_visibility_fleet.py::AUTOSKILLIT_HEADLESS": (
        "Pre-existing test-owned AUTOSKILLIT_HEADLESS clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/server/test_session_type_visibility_fleet.py::AUTOSKILLIT_SESSION_TYPE": (
        "Pre-existing test-owned AUTOSKILLIT_SESSION_TYPE clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    (
        "tests/server/test_session_type_visibility_orchestrator.py"
        "::AUTOSKILLIT_FOOD_TRUCK_TOOL_TAGS"
    ): (
        "Pre-existing test-owned AUTOSKILLIT_FOOD_TRUCK_TOOL_TAGS clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/server/test_session_type_visibility_orchestrator.py::AUTOSKILLIT_HEADLESS": (
        "Pre-existing test-owned AUTOSKILLIT_HEADLESS clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/server/test_session_type_visibility_orchestrator.py::AUTOSKILLIT_HEADLESS_AUTO_GATE": (
        "Pre-existing test-owned AUTOSKILLIT_HEADLESS_AUTO_GATE clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/server/test_session_type_visibility_orchestrator.py::AUTOSKILLIT_SESSION_TYPE": (
        "Pre-existing test-owned AUTOSKILLIT_SESSION_TYPE clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/server/test_tools_clone.py::AUTOSKILLIT_CAMPAIGN_ID": (
        "Pre-existing test-owned AUTOSKILLIT_CAMPAIGN_ID clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/server/test_tools_dispatch_halt.py::AUTOSKILLIT_CAMPAIGN_STATE_PATH": (
        "Pre-existing test-owned AUTOSKILLIT_CAMPAIGN_STATE_PATH clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/server/test_tools_dispatch_validation.py::AUTOSKILLIT_HEADLESS": (
        "Pre-existing test-owned AUTOSKILLIT_HEADLESS clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/server/test_tools_execution_routing.py::AUTOSKILLIT_DISPATCH_ID": (
        "Pre-existing test-owned AUTOSKILLIT_DISPATCH_ID clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/server/test_tools_kitchen_envelope.py::AUTOSKILLIT_HEADLESS": (
        "Pre-existing test-owned AUTOSKILLIT_HEADLESS clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/server/test_tools_kitchen_gate.py::AUTOSKILLIT_CAMPAIGN_ID": (
        "Pre-existing test-owned AUTOSKILLIT_CAMPAIGN_ID clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/server/test_tools_run_cmd_invariants.py::AUTOSKILLIT_HEADLESS": (
        "Pre-existing test-owned AUTOSKILLIT_HEADLESS clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/server/test_tools_run_python_invariants.py::AUTOSKILLIT_HEADLESS": (
        "Pre-existing test-owned AUTOSKILLIT_HEADLESS clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/server/test_tools_status_kitchen.py::GITHUB_TOKEN": (
        "Pre-existing test-owned GITHUB_TOKEN clear predating the central _scrub_ambient_env "
        "fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation scope of this "
        "change; grandfathered pending a future consolidation pass."
    ),
    "tests/workspace/test_clone_registry.py::AUTOSKILLIT_CAMPAIGN_ID": (
        "Pre-existing test-owned AUTOSKILLIT_CAMPAIGN_ID clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
    "tests/workspace/test_clone_registry.py::AUTOSKILLIT_KITCHEN_SESSION_ID": (
        "Pre-existing test-owned AUTOSKILLIT_KITCHEN_SESSION_ID clear predating the central "
        "_scrub_ambient_env fixture; outside the CLAUDECODE/CLAUDE_CODE_EXECPATH remediation "
        "scope of this change; grandfathered pending a future consolidation pass."
    ),
}


def _string_literal_arg(call: ast.Call, keyword: str) -> str | None:
    """Resolve a call's var argument: first positional arg, else the named keyword."""
    if call.args:
        arg = call.args[0]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return arg.value
        return None
    for kw in call.keywords:
        if (
            kw.arg == keyword
            and isinstance(kw.value, ast.Constant)
            and isinstance(kw.value.value, str)
        ):
            return kw.value.value
    return None


def _is_os_environ(node: ast.expr) -> bool:
    """True only for the literal receiver ``os.environ`` — not an arbitrary local dict."""
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "environ"
        and isinstance(node.value, ast.Name)
        and node.value.id == "os"
    )


def _collect_env_call_sites(tree: ast.Module, rel: str) -> list[tuple[str, int, str]]:
    """Collect ``(file, line, var)`` for every matching delenv/pop call in *tree*."""
    sites: list[tuple[str, int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        if func.attr == "delenv":
            var = _string_literal_arg(node, "name")
        elif func.attr == "pop" and _is_os_environ(func.value):
            var = _string_literal_arg(node, "key")
        else:
            continue
        if var is not None:
            sites.append((rel, node.lineno, var))
    return sites


def _all_env_call_sites() -> list[tuple[str, int, str]]:
    sites: list[tuple[str, int, str]] = []
    for py in sorted(TESTS_ROOT.rglob("*.py")):
        rel = py.relative_to(TESTS_ROOT.parent).as_posix()
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        except SyntaxError:
            continue
        sites.extend(_collect_env_call_sites(tree, rel))
    return sites


def test_no_adhoc_delenv_or_pop_for_centrally_scrubbed_vars() -> None:
    """Ad-hoc delenv/pop calls for centrally-scrubbed vars must be declared, not silent."""
    offenders: list[str] = []
    for rel, line, var in _all_env_call_sites():
        entry = AMBIENT_ENV_DISPOSITIONS.get(var)
        if entry is None or entry.disposition != "scrub":
            continue
        if f"{rel}::{var}" in _INTENTIONAL_ENV_INPUT_SITES:
            continue
        offenders.append(f"{rel}:{line} ({var})")
    assert not offenders, (
        "Ad-hoc monkeypatch.delenv(...) / os.environ.pop(...) calls found for vars the "
        "central `_scrub_ambient_env` autouse fixture (tests/conftest.py) already scrubs "
        "unconditionally before every test: "
        + ", ".join(sorted(offenders))
        + ". Remove the redundant call — the fixture already handles it. If this is a "
        "genuine behavioral test input (not a workaround), add an entry to "
        "_INTENTIONAL_ENV_INPUT_SITES with a justification."
    )


def test_intentional_env_input_sites_are_not_stale() -> None:
    """Allowlist hygiene: every declared site must still exist and still need declaring."""
    matched_keys = {f"{rel}::{var}" for rel, _line, var in _all_env_call_sites()}
    stale: list[str] = []
    for key in _INTENTIONAL_ENV_INPUT_SITES:
        _rel, _, var = key.partition("::")
        entry = AMBIENT_ENV_DISPOSITIONS.get(var)
        if entry is None or entry.disposition != "scrub":
            stale.append(f"{key} (var is no longer scrub-disposition)")
        elif key not in matched_keys:
            stale.append(f"{key} (no longer matched by the guard's own scanner)")
    assert not stale, f"Stale _INTENTIONAL_ENV_INPUT_SITES entries, remove them: {stale}"
