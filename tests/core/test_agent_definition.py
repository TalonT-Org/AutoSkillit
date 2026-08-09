"""Tests for the canonical bundled-agent definition authority."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from autoskillit.core import (
    BUNDLED_EXPLORER_ROLES,
    CODEX_EXPLORER_IDENTITY,
    DIRECT_PREFIX,
    EXPLORATION_TOOLS,
    REPOSITORY_IMPACT_PROFILER_ROLE,
    SEMANTIC_CODE_NAVIGATOR_ROLE,
    AgentDef,
    AgentDefinitionError,
    CodexAgentProjectionDef,
    agent_definition_digest,
    load_agent_definition,
    load_bundled_agent_definitions,
    pkg_root,
)
from tests._codex_feature_policy import RETIRED_CODEX_FEATURES

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]

_EXPLORATION_BROKER_TOOLS = frozenset(f"{DIRECT_PREFIX}{tool}" for tool in EXPLORATION_TOOLS)


@pytest.mark.parametrize(
    ("name", "role_boundary"),
    [
        (SEMANTIC_CODE_NAVIGATOR_ROLE, "structural and semantic"),
        (REPOSITORY_IMPACT_PROFILER_ROLE, "registrations, configuration"),
    ],
)
def test_specialized_explorers_are_terminal_luna_broker_roles(
    name: str, role_boundary: str
) -> None:
    definition = load_agent_definition(pkg_root() / "agents" / f"{name}.md")

    assert frozenset(definition.tools) == _EXPLORATION_BROKER_TOOLS
    assert definition.model == "sonnet"
    assert definition.codex.model == "gpt-5.6-luna"
    assert definition.codex.reasoning_effort == "max"
    assert (
        definition.codex.model,
        definition.codex.reasoning_effort,
    ) == CODEX_EXPLORER_IDENTITY
    assert definition.codex.sandbox_mode == "read-only"
    assert definition.codex.web_search == "disabled"
    assert not (set(RETIRED_CODEX_FEATURES) & set(definition.codex.disabled_features))
    assert not definition.codex.agents_enabled
    assert role_boundary in definition.body
    assert "spawn peers" in definition.body
    assert "synthesis" in definition.body
    assert "select a backend" in definition.body


def test_bundled_agent_catalog_loads_with_unique_digests() -> None:
    definitions = load_bundled_agent_definitions()
    assert definitions
    assert BUNDLED_EXPLORER_ROLES <= {definition.name for definition in definitions}
    assert len({definition.name for definition in definitions}) == len(definitions)
    assert len({agent_definition_digest(definition) for definition in definitions}) == len(
        definitions
    )


def test_explicit_luna_projection_is_independent_from_claude_model(tmp_path: Path) -> None:
    path = tmp_path / "semantic-code-navigator.md"
    path.write_text(
        "---\n"
        "name: semantic-code-navigator\n"
        "description: Bounded semantic navigation\n"
        "tools: [Read, Grep, Glob]\n"
        "model: sonnet\n"
        "maxTurns: 12\n"
        "codex:\n"
        "  model: gpt-5.6-luna\n"
        "  reasoning_effort: max\n"
        "  sandbox_mode: read-only\n"
        "  disabled_features: [apps, shell_tool]\n"
        "  agents_enabled: false\n"
        "  web_search: disabled\n"
        "---\n\n"
        "Return bounded evidence only.\n",
        encoding="utf-8",
    )
    definition = load_agent_definition(path)
    assert definition.model == "sonnet"
    assert definition.codex == CodexAgentProjectionDef(
        model="gpt-5.6-luna",
        reasoning_effort="max",
        sandbox_mode="read-only",
        disabled_features=("apps", "shell_tool"),
        agents_enabled=False,
        web_search="disabled",
    )


def test_frontmatter_delimiter_must_occupy_its_own_line(tmp_path: Path) -> None:
    path = tmp_path / "delimiter-in-description.md"
    path.write_text(
        "---\n"
        "name: delimiter-reader\n"
        "description: Preserve before---after as metadata\n"
        "tools: [Read]\n"
        "---\n\n"
        "Return the complete body.\n",
        encoding="utf-8",
    )

    definition = load_agent_definition(path)

    assert definition.description == "Preserve before---after as metadata"
    assert definition.body == "Return the complete body."


def test_uppercase_lsp_is_read_only_for_derived_codex_projection(tmp_path: Path) -> None:
    path = tmp_path / "lsp-reader.md"
    path.write_text(
        "---\n"
        "name: lsp-reader\n"
        "description: LSP navigation\n"
        "tools: [Read, Grep, Glob, LSP]\n"
        "model: sonnet\n"
        "---\n\n"
        "Return bounded evidence only.\n",
        encoding="utf-8",
    )

    assert load_agent_definition(path).codex.sandbox_mode == "read-only"


def test_definition_and_projection_are_frozen() -> None:
    definition = AgentDef(
        name="semantic-code-navigator",
        description="Bounded semantic navigation",
        tools=("Read",),
        model="sonnet",
        max_turns=1,
        body="Return evidence.",
        codex=CodexAgentProjectionDef("gpt-5.6-luna", "max", "read-only"),
    )
    with pytest.raises(FrozenInstanceError):
        definition.name = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    "projection",
    [
        ("unknown", "max", "read-only"),
        ("gpt-5.6-luna", "unknown", "read-only"),
        ("gpt-5.6-luna", "max", "danger-full-access"),
    ],
)
def test_invalid_native_projection_fails_closed(projection: tuple[str, str, str]) -> None:
    with pytest.raises(AgentDefinitionError):
        CodexAgentProjectionDef(*projection)


@pytest.mark.parametrize("web_search", ["enabled", False, 1])
def test_invalid_web_search_policy_fails_closed(web_search: object) -> None:
    with pytest.raises(AgentDefinitionError, match="web_search must be 'disabled'"):
        CodexAgentProjectionDef(
            "gpt-5.6-luna",
            "max",
            "read-only",
            web_search=web_search,  # type: ignore[arg-type]
        )


def test_web_search_policy_is_optional_and_positional_arguments_remain_stable() -> None:
    assert CodexAgentProjectionDef(None, None, "read-only").web_search is None
    projection = CodexAgentProjectionDef(
        "gpt-5.6-luna",
        "max",
        "read-only",
        ("apps",),
        False,
    )
    assert projection.agents_enabled is False
    assert projection.web_search is None


@pytest.mark.parametrize("disabled_feature", (*RETIRED_CODEX_FEATURES, "web_search"))
def test_retired_codex_features_fail_closed(disabled_feature: str) -> None:
    with pytest.raises(AgentDefinitionError, match="unsupported Codex disabled features"):
        CodexAgentProjectionDef(
            "gpt-5.6-luna",
            "max",
            "read-only",
            (disabled_feature,),
        )


@pytest.mark.parametrize(
    "disabled_features",
    [
        ("shell_tool", "apps"),
        ("apps", "apps"),
        ("unknown_feature",),
        ("apps", 1),
        ["apps"],
    ],
)
def test_invalid_disabled_features_fail_closed(disabled_features: object) -> None:
    with pytest.raises(AgentDefinitionError):
        CodexAgentProjectionDef(
            "gpt-5.6-luna",
            "max",
            "read-only",
            disabled_features,  # type: ignore[arg-type]
        )


def test_extension_surface_disabled_features_are_valid_and_canonical() -> None:
    disabled_features = (
        "enable_mcp_apps",
        "image_generation",
        "in_app_browser",
        "plugin_sharing",
        "plugins",
        "remote_plugin",
        "standalone_web_search",
        "tool_suggest",
    )
    projection = CodexAgentProjectionDef(
        "gpt-5.6-luna",
        "max",
        "read-only",
        disabled_features,
    )
    assert projection.disabled_features == disabled_features


@pytest.mark.parametrize("agents_enabled", [None, 0, "false"])
def test_invalid_agents_enabled_fails_closed(agents_enabled: object) -> None:
    with pytest.raises(AgentDefinitionError, match="agents_enabled must be a boolean"):
        CodexAgentProjectionDef(
            "gpt-5.6-luna",
            "max",
            "read-only",
            agents_enabled=agents_enabled,  # type: ignore[arg-type]
        )


def test_disabled_features_frontmatter_requires_a_string_list(tmp_path: Path) -> None:
    path = tmp_path / "invalid-disabled-features.md"
    path.write_text(
        "---\n"
        "name: invalid-disabled-features\n"
        "description: Bounded semantic navigation\n"
        "tools: [Read, Grep, Glob]\n"
        "codex:\n"
        "  model: gpt-5.6-luna\n"
        "  reasoning_effort: max\n"
        "  sandbox_mode: read-only\n"
        "  disabled_features: shell_tool\n"
        "---\n\n"
        "Return bounded evidence only.\n",
        encoding="utf-8",
    )
    with pytest.raises(AgentDefinitionError, match="disabled_features must be a string list"):
        load_agent_definition(path)


def test_agents_enabled_frontmatter_requires_a_boolean(tmp_path: Path) -> None:
    path = tmp_path / "invalid-agents-enabled.md"
    path.write_text(
        "---\n"
        "name: invalid-agents-enabled\n"
        "description: Bounded semantic navigation\n"
        "tools: [Read, Grep, Glob]\n"
        "codex:\n"
        "  model: gpt-5.6-luna\n"
        "  reasoning_effort: max\n"
        "  sandbox_mode: read-only\n"
        '  agents_enabled: "false"\n'
        "---\n\n"
        "Return bounded evidence only.\n",
        encoding="utf-8",
    )
    with pytest.raises(AgentDefinitionError, match="agents_enabled must be a boolean"):
        load_agent_definition(path)


def test_definition_digest_is_domain_separated_and_content_bound() -> None:
    definition = AgentDef(
        name="semantic-code-navigator",
        description="Bounded semantic navigation",
        tools=("Read",),
        model="sonnet",
        max_turns=1,
        body="Return evidence.",
        codex=CodexAgentProjectionDef("gpt-5.6-luna", "max", "read-only"),
    )
    digest = agent_definition_digest(definition)
    changed = AgentDef(
        name=definition.name,
        description=definition.description,
        tools=definition.tools,
        model=definition.model,
        max_turns=definition.max_turns,
        body="Return different evidence.",
        codex=definition.codex,
    )
    changed_features = AgentDef(
        name=definition.name,
        description=definition.description,
        tools=definition.tools,
        model=definition.model,
        max_turns=definition.max_turns,
        body=definition.body,
        codex=CodexAgentProjectionDef(
            "gpt-5.6-luna",
            "max",
            "read-only",
            ("apps",),
        ),
    )
    changed_agents_enabled = AgentDef(
        name=definition.name,
        description=definition.description,
        tools=definition.tools,
        model=definition.model,
        max_turns=definition.max_turns,
        body=definition.body,
        codex=CodexAgentProjectionDef(
            "gpt-5.6-luna",
            "max",
            "read-only",
            agents_enabled=False,
        ),
    )
    changed_web_search = AgentDef(
        name=definition.name,
        description=definition.description,
        tools=definition.tools,
        model=definition.model,
        max_turns=definition.max_turns,
        body=definition.body,
        codex=CodexAgentProjectionDef(
            "gpt-5.6-luna",
            "max",
            "read-only",
            web_search="disabled",
        ),
    )
    assert digest.startswith("sha256:")
    assert digest != agent_definition_digest(changed)
    assert digest != agent_definition_digest(changed_features)
    assert digest != agent_definition_digest(changed_agents_enabled)
    assert digest != agent_definition_digest(changed_web_search)
