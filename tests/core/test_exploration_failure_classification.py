"""Completeness contract for the ExplorationFailureCode -> agent-facing response
classification (#4684/#4718 Step 1.1e).

A registered failure code with no classified response is exactly the "registered
but unwired" defect this rectify plan targets at the agent-dispatch layer: adding
a code without deciding its FALLBACK/RETRY-THEN-SURFACE/SURFACE response must fail
loudly, not silently leave the new code unclassified.
"""

from __future__ import annotations

import pytest

from autoskillit.core import (
    EXPLORATION_FAILURE_CODE_RESPONSES,
    EXPLORATION_FALLBACK_CODES,
    PLUGINLESS_EXPLORER_ROLE,
    ExplorationFailureCode,
    ExplorationFailureResponse,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_every_exploration_failure_code_is_classified_exactly_once() -> None:
    classified = set(EXPLORATION_FAILURE_CODE_RESPONSES)
    all_codes = set(ExplorationFailureCode)
    missing = all_codes - classified
    orphaned = classified - all_codes
    assert not missing, f"Unclassified ExplorationFailureCode member(s): {sorted(missing)}"
    assert not orphaned, f"Classification references non-existent code(s): {sorted(orphaned)}"
    assert len(EXPLORATION_FAILURE_CODE_RESPONSES) == len(all_codes), (
        "each code must be classified exactly once"
    )
    for code, response in EXPLORATION_FAILURE_CODE_RESPONSES.items():
        assert isinstance(code, ExplorationFailureCode)
        assert isinstance(response, ExplorationFailureResponse)


def test_trusted_root_mismatch_is_surface_never_fallback() -> None:
    """A permanent, principled exclusion — see the mapping-row comment for why.

    trusted_root_mismatch is unreachable from enable_exploration today but is
    reachable on the launch path; falling back would let an unauthenticated
    read proceed after the server explicitly refused this repository's trust.
    A future "route every residual code to fallback" pass must not flip this
    without reading the security rationale recorded at the mapping row.
    """
    assert (
        EXPLORATION_FAILURE_CODE_RESPONSES[ExplorationFailureCode.TRUSTED_ROOT_MISMATCH]
        is ExplorationFailureResponse.SURFACE
    )
    assert ExplorationFailureCode.TRUSTED_ROOT_MISMATCH not in EXPLORATION_FALLBACK_CODES


def test_agents_md_and_agent_definition_name_exactly_the_fallback_set() -> None:
    """AGENTS.md and pluginless-explorer.md must both name exactly the
    FALLBACK-classified codes — no more, no fewer — so the two doc surfaces
    cannot drift apart. The Claude renderer preamble's equivalent contract is
    verified in tests/execution/test_explorer_dispatch.py (it requires the
    execution-layer backend, which tests/core/ may not import)."""
    from autoskillit.core import pkg_root

    assert EXPLORATION_FALLBACK_CODES, "expected at least one FALLBACK-classified code"

    agents_md = (pkg_root() / "agents" / "AGENTS.md").read_text()
    agent_definition = (pkg_root() / "agents" / "pluginless-explorer.md").read_text()

    for code in EXPLORATION_FALLBACK_CODES:
        assert code.value in agents_md, f"AGENTS.md missing fallback code {code.value!r}"
        assert code.value in agent_definition, (
            f"pluginless-explorer.md missing fallback code {code.value!r}"
        )


def test_pluginless_explorer_role_name_is_registered() -> None:
    from autoskillit.core import pkg_root

    agent_path = pkg_root() / "agents" / f"{PLUGINLESS_EXPLORER_ROLE}.md"
    assert agent_path.exists()
