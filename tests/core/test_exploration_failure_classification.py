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
    render_exploration_failure_guidance,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_guidance_renderer_names_each_response_tier() -> None:
    guidance = render_exploration_failure_guidance(fallback_dispatch="pluginless explorer")

    assert "local checkout" in guidance
    assert "remote or public copy" in guidance
    assert "local access" in guidance
    for code, response in EXPLORATION_FAILURE_CODE_RESPONSES.items():
        assert code.value in guidance
        if response is ExplorationFailureResponse.FALLBACK:
            assert "dispatch pluginless explorer" in guidance


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

    from autoskillit.core import HARNESS_ZERO_TOOLS_REFUSAL_MARKER, load_agent_definition

    assert HARNESS_ZERO_TOOLS_REFUSAL_MARKER in agents_md, (
        "AGENTS.md must authorize the pluginless fallback for a harness spawn refusal"
    )
    description = load_agent_definition(
        pkg_root() / "agents" / "pluginless-explorer.md"
    ).description
    assert HARNESS_ZERO_TOOLS_REFUSAL_MARKER in description, (
        "pluginless-explorer's description must authorize dispatch on a harness spawn refusal"
    )


def test_pluginless_explorer_role_name_is_registered() -> None:
    from autoskillit.core import pkg_root

    agent_path = pkg_root() / "agents" / f"{PLUGINLESS_EXPLORER_ROLE}.md"
    assert agent_path.exists()


def test_client_spawn_refusal_is_a_typed_fallback_condition() -> None:
    from autoskillit.core import (
        EXPLORER_SPAWN_REFUSAL_RESPONSE,
        HARNESS_ZERO_TOOLS_REFUSAL_MARKER,
    )

    assert HARNESS_ZERO_TOOLS_REFUSAL_MARKER == "would be spawned with zero tools"
    assert EXPLORER_SPAWN_REFUSAL_RESPONSE is ExplorationFailureResponse.FALLBACK


def test_guidance_defines_client_spawn_refusal_response() -> None:
    from autoskillit.core import HARNESS_ZERO_TOOLS_REFUSAL_MARKER

    guidance = render_exploration_failure_guidance(fallback_dispatch="DISPATCH_X")

    assert HARNESS_ZERO_TOOLS_REFUSAL_MARKER in guidance
    refusal_sentence = guidance[guidance.index(HARNESS_ZERO_TOOLS_REFUSAL_MARKER) :]
    assert "DISPATCH_X" in refusal_sentence
    assert "verbatim" in refusal_sentence
