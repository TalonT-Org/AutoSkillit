"""Agent tool-name surface alignment: authored, projected, and registered tools must agree."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import (
    BUNDLED_EXPLORER_ROLES,
    DIRECT_PREFIX,
    EXPLORATION_TOOLS,
    MARKETPLACE_PREFIX,
    SkillExecutionRole,
    SkillSource,
    ToolInitializationOperation,
    canonical_reader_tools_to_bare,
    get_tool_def,
    load_agent_definitions,
    load_bundled_agent_definitions,
    pkg_root,
)

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]


def test_authored_agent_mcp_tools_derive_from_core_authority() -> None:
    """Every MCP tool in a bundled agent definition equals DIRECT_PREFIX + short_name."""
    definitions = load_bundled_agent_definitions()
    for definition in definitions:
        for tool in definition.tools:
            if not tool.startswith("mcp__"):
                continue
            assert tool.startswith(DIRECT_PREFIX), (
                f"Agent {definition.name!r} tool {tool!r} does not use "
                f"the canonical direct prefix {DIRECT_PREFIX!r}"
            )
            short = tool[len(DIRECT_PREFIX) :]
            assert (
                get_tool_def(short).initialization_operation
                is ToolInitializationOperation.INSPECTION
            )
            if definition.name in BUNDLED_EXPLORER_ROLES:
                assert short in EXPLORATION_TOOLS


def test_marketplace_artifact_agent_tools_carry_marketplace_prefix(tmp_path: Path) -> None:
    """The marketplace-published agent definitions carry MARKETPLACE_PREFIX."""
    from autoskillit.workspace import (
        SkillProjectionContext,
        materialize_sanitized_plugin_root,
    )
    from autoskillit.workspace.skills import (
        DefaultSkillResolver,
        EffectiveSkillCatalog,
        SkillCatalogEntry,
    )

    source_root = pkg_root()
    source_infos = tuple(
        s for s in DefaultSkillResolver().list_all() if s.source is SkillSource.BUNDLED
    )
    catalog = EffectiveSkillCatalog(
        skills=tuple(SkillCatalogEntry.from_skill_info(s) for s in source_infos),
        execution_role=SkillExecutionRole.SESSION,
    )
    destination = tmp_path / "plugins" / "autoskillit"
    destination.parent.mkdir(parents=True)
    materialize_sanitized_plugin_root(
        source_root,
        destination,
        catalog,
        SkillProjectionContext(cwd=tmp_path, catalog=catalog),
        mcp_tool_prefix=MARKETPLACE_PREFIX,
    )

    projected_agents_dir = destination / "agents"
    assert projected_agents_dir.is_dir()
    projected_defs = load_agent_definitions(projected_agents_dir)
    assert projected_defs, "Expected at least one agent definition in the projected artifact"

    for definition in projected_defs:
        for tool in definition.tools:
            if not tool.startswith("mcp__"):
                continue
            assert tool.startswith(MARKETPLACE_PREFIX), (
                f"Marketplace agent {definition.name!r} tool {tool!r} does not use "
                f"the marketplace prefix {MARKETPLACE_PREFIX!r}"
            )
            short = tool[len(MARKETPLACE_PREFIX) :]
            assert (
                get_tool_def(short).initialization_operation
                is ToolInitializationOperation.INSPECTION
            )
            if definition.name in BUNDLED_EXPLORER_ROLES:
                assert short in EXPLORATION_TOOLS


def test_session_log_reader_has_one_inspection_tool_and_terminal_codex_policy() -> None:
    definition = next(
        item for item in load_bundled_agent_definitions() if item.name == "session-log-reader"
    )

    assert definition.tools == (f"{DIRECT_PREFIX}inspect_session_logs",)
    assert get_tool_def("inspect_session_logs").initialization_operation is (
        ToolInitializationOperation.INSPECTION
    )
    assert definition.model == "haiku"
    assert definition.codex.model == "gpt-5.6-luna"
    assert definition.codex.reasoning_effort == "xhigh"
    assert definition.codex.sandbox_mode == "read-only"
    assert definition.codex.agents_enabled is False
    assert definition.codex.web_search == "disabled"
    assert {"shell_tool", "standalone_web_search", "multi_agent", "multi_agent_v2"} <= set(
        definition.codex.disabled_features
    )


def test_pr_source_reader_tools_convert_from_canonical_to_exact_bare_subset() -> None:
    from autoskillit.core import EVIDENCE_READER_TOOLS

    definition = next(
        item for item in load_bundled_agent_definitions() if item.name == "pr-source-reader"
    )

    assert canonical_reader_tools_to_bare(definition.reader_tools) == (
        "get_authorized_artifact_page",
        "read_authorized_artifact",
    )
    assert frozenset(canonical_reader_tools_to_bare(definition.reader_tools)) == (
        EVIDENCE_READER_TOOLS
    )


@pytest.mark.parametrize(
    "reader_tools",
    [
        (f"{MARKETPLACE_PREFIX}read_authorized_artifact",),
        (f"{DIRECT_PREFIX}read_authorized_artifact",),
    ],
)
def test_reader_tool_conversion_rejects_noncanonical_or_incomplete_subsets(
    reader_tools: tuple[str, ...],
) -> None:
    with pytest.raises(ValueError):
        canonical_reader_tools_to_bare(reader_tools)


def test_session_log_reader_covers_application_level_error_results() -> None:
    """Reader searches errors carried by successful tool transports."""
    agent_path = pkg_root() / "agents" / "session-log-reader.md"
    content = agent_path.read_text()

    assert "literal `error:`" in content
    assert '`"is_error":true`' in content
    assert "successful tool transport can contain an application error" in content
    assert "Paginate every" in content and "matching page" in content
