"""Canonical bundled-agent definitions and Codex projection policy."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .io import load_yaml
from .types import (
    CODEX_EFFORT_MAPPING,
    CODEX_MODEL_ALIASES,
    CODEX_VALID_MODEL_IDS,
    CODEX_VALID_REASONING_EFFORTS,
)

__all__ = [
    "AGENT_DEFINITION_DIGEST_DOMAIN",
    "AgentDef",
    "AgentDefinitionError",
    "CodexAgentProjectionDef",
    "agent_definition_digest",
    "load_agent_definition",
    "load_agent_definitions",
]


AGENT_DEFINITION_DIGEST_DOMAIN = "autoskillit.agent-definition.v1"
_AGENT_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")
_READ_ONLY_AGENT_TOOLS = frozenset({"Read", "Grep", "Glob", "LSP"})
_CODEX_SANDBOX_MODES = frozenset({"read-only", "workspace-write"})
_LUNA_READ_ONLY_PROJECTION = ("gpt-5.6-luna", "max", "read-only")
_CODEX_DISABLEABLE_FEATURES = (
    "apps",
    "apps_mcp_path_override",
    "browser_use",
    "browser_use_external",
    "browser_use_full_cdp_access",
    "code_mode",
    "code_mode_buffered_exec",
    "code_mode_host",
    "code_mode_only",
    "computer_use",
    "enable_mcp_apps",
    "goals",
    "image_generation",
    "in_app_browser",
    "js_repl",
    "js_repl_tools_only",
    "multi_agent",
    "multi_agent_v2",
    "plugin_sharing",
    "plugins",
    "remote_plugin",
    "request_permissions_tool",
    "shell_tool",
    "standalone_web_search",
    "tool_search_always_defer_mcp_tools",
    "tool_suggest",
    "unified_exec",
    "unified_exec_zsh_fork",
    "web_search_cached",
    "web_search_request",
)
_CODEX_DISABLEABLE_FEATURE_SET = frozenset(_CODEX_DISABLEABLE_FEATURES)


class AgentDefinitionError(ValueError):
    """Raised when an agent definition cannot be validated canonically."""


@dataclass(frozen=True, slots=True)
class CodexAgentProjectionDef:
    """Native Codex policy embedded in or derived from an agent definition."""

    model: str | None
    reasoning_effort: str | None
    sandbox_mode: str
    disabled_features: tuple[str, ...] = ()
    agents_enabled: bool = True

    def __post_init__(self) -> None:
        if self.model is not None and self.model not in CODEX_VALID_MODEL_IDS:
            raise AgentDefinitionError(f"unsupported Codex model: {self.model!r}")
        if (
            self.reasoning_effort is not None
            and self.reasoning_effort not in CODEX_VALID_REASONING_EFFORTS
        ):
            raise AgentDefinitionError(
                f"unsupported Codex reasoning effort: {self.reasoning_effort!r}"
            )
        if self.sandbox_mode not in _CODEX_SANDBOX_MODES:
            raise AgentDefinitionError(f"unsupported Codex sandbox mode: {self.sandbox_mode!r}")
        if self.reasoning_effort is not None and self.model is None:
            raise AgentDefinitionError("Codex reasoning effort requires a native model")
        if not isinstance(self.disabled_features, tuple):
            raise AgentDefinitionError("Codex disabled_features must be an immutable tuple")
        if any(not isinstance(feature, str) for feature in self.disabled_features):
            raise AgentDefinitionError("Codex disabled_features must contain only strings")
        unsupported = set(self.disabled_features) - _CODEX_DISABLEABLE_FEATURE_SET
        if unsupported:
            raise AgentDefinitionError(
                f"unsupported Codex disabled features: {sorted(unsupported)}"
            )
        if len(set(self.disabled_features)) != len(self.disabled_features):
            raise AgentDefinitionError("Codex disabled_features must not contain duplicates")
        if self.disabled_features != tuple(sorted(self.disabled_features)):
            raise AgentDefinitionError("Codex disabled_features must use canonical order")
        if type(self.agents_enabled) is not bool:
            raise AgentDefinitionError("Codex agents_enabled must be a boolean")


@dataclass(frozen=True, slots=True)
class AgentDef:
    """Static, validated definition of one registered agent role."""

    name: str
    description: str
    tools: tuple[str, ...]
    model: str | None
    max_turns: int | None
    body: str
    codex: CodexAgentProjectionDef

    def __post_init__(self) -> None:
        if not _AGENT_NAME_RE.fullmatch(self.name):
            raise AgentDefinitionError(f"invalid agent name: {self.name!r}")
        if not self.description.strip():
            raise AgentDefinitionError("agent description must be non-empty")
        if not self.tools or any(not tool.strip() for tool in self.tools):
            raise AgentDefinitionError("agent tools must be non-empty strings")
        if len(set(self.tools)) != len(self.tools):
            raise AgentDefinitionError("agent tools must be unique")
        if self.max_turns is not None and self.max_turns < 1:
            raise AgentDefinitionError("agent maxTurns must be a positive integer")
        if not self.body.strip():
            raise AgentDefinitionError("agent body must be non-empty")
        if "'''" in self.body:
            raise AgentDefinitionError("agent body cannot contain TOML triple single quotes")

    @staticmethod
    def validate_injected_parent_policy(
        agent_defs: tuple[AgentDef, ...] | None,
        parent_sandbox_mode: str,
    ) -> None:
        """Reject a parent policy Codex would apply over the Phase A probe role."""
        if agent_defs is None:
            return
        requires_read_only_parent = any(
            (
                definition.codex.model,
                definition.codex.reasoning_effort,
                definition.codex.sandbox_mode,
            )
            == _LUNA_READ_ONLY_PROJECTION
            for definition in agent_defs
        )
        if requires_read_only_parent and parent_sandbox_mode != "read-only":
            raise ValueError(
                "gpt-5.6-luna/max/read-only agent projection requires "
                "parent_sandbox_mode='read-only'"
            )


def _required_text(meta: dict[str, Any], key: str) -> str:
    value = meta.get(key)
    if not isinstance(value, str) or not value.strip():
        raise AgentDefinitionError(f"agent frontmatter {key!r} must be non-empty text")
    return value.strip()


def _parse_tools(meta: dict[str, Any]) -> tuple[str, ...]:
    raw = meta.get("tools")
    if not isinstance(raw, list) or not raw or any(not isinstance(item, str) for item in raw):
        raise AgentDefinitionError("agent frontmatter 'tools' must be a non-empty string list")
    return tuple(raw)


def _derived_codex_projection(
    meta: dict[str, Any], tools: tuple[str, ...]
) -> CodexAgentProjectionDef:
    raw = meta.get("codex")
    if raw is not None:
        if not isinstance(raw, dict):
            raise AgentDefinitionError("agent frontmatter 'codex' must be a mapping")
        unexpected = set(raw) - {
            "model",
            "reasoning_effort",
            "sandbox_mode",
            "disabled_features",
            "agents_enabled",
        }
        if unexpected:
            raise AgentDefinitionError(
                f"unknown Codex agent projection fields: {sorted(unexpected)}"
            )
        model = raw.get("model")
        effort = raw.get("reasoning_effort")
        sandbox = raw.get("sandbox_mode")
        disabled_features = raw.get("disabled_features", [])
        agents_enabled = raw.get("agents_enabled", True)
        if (
            not isinstance(model, str)
            or not isinstance(effort, str)
            or not isinstance(sandbox, str)
        ):
            raise AgentDefinitionError(
                "Codex agent projection requires model, reasoning_effort, and sandbox_mode"
            )
        if not isinstance(disabled_features, list) or any(
            not isinstance(feature, str) for feature in disabled_features
        ):
            raise AgentDefinitionError(
                "Codex agent projection disabled_features must be a string list"
            )
        return CodexAgentProjectionDef(
            model,
            effort,
            sandbox,
            tuple(disabled_features),
            agents_enabled,
        )

    model_key = meta.get("model")
    if model_key is not None and not isinstance(model_key, str):
        raise AgentDefinitionError("agent frontmatter 'model' must be text")
    native_model = CODEX_MODEL_ALIASES.get(model_key) if model_key is not None else None
    effort = CODEX_EFFORT_MAPPING.get(model_key) if model_key is not None else None
    sandbox = "read-only" if set(tools) <= _READ_ONLY_AGENT_TOOLS else "workspace-write"
    return CodexAgentProjectionDef(native_model, effort, sandbox)


def load_agent_definition(path: Path) -> AgentDef:
    """Load one Markdown definition through the canonical fail-closed parser."""
    content = path.read_text(encoding="utf-8")
    if not content.startswith("---"):
        raise AgentDefinitionError(f"{path}: missing YAML frontmatter")
    parts = content.split("---", 2)
    if len(parts) != 3:
        raise AgentDefinitionError(f"{path}: malformed YAML frontmatter")
    raw_meta = load_yaml(parts[1])
    if not isinstance(raw_meta, dict):
        raise AgentDefinitionError(f"{path}: YAML frontmatter must be a mapping")
    meta = {str(key): value for key, value in raw_meta.items()}
    name = _required_text(meta, "name")
    description = _required_text(meta, "description")
    tools = _parse_tools(meta)
    model = meta.get("model")
    if model is not None and not isinstance(model, str):
        raise AgentDefinitionError("agent frontmatter 'model' must be text")
    max_turns = meta.get("maxTurns")
    if max_turns is not None and (type(max_turns) is not int or max_turns < 1):
        raise AgentDefinitionError("agent frontmatter 'maxTurns' must be a positive integer")
    return AgentDef(
        name=name,
        description=description,
        tools=tools,
        model=model,
        max_turns=max_turns,
        body=parts[2].strip(),
        codex=_derived_codex_projection(meta, tools),
    )


def load_agent_definitions(agents_dir: Path) -> tuple[AgentDef, ...]:
    """Load the complete agent catalog and reject duplicate registered names."""
    definitions = tuple(
        load_agent_definition(path)
        for path in sorted(agents_dir.glob("*.md"))
        if path.name not in {"AGENTS.md", "CLAUDE.md"}
    )
    names = tuple(definition.name for definition in definitions)
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise AgentDefinitionError(f"duplicate agent names: {duplicates}")
    return definitions


def agent_definition_digest(definition: AgentDef) -> str:
    """Return a domain-separated digest over one canonical definition."""
    payload = {
        "body": definition.body,
        "codex": {
            "model": definition.codex.model,
            "reasoning_effort": definition.codex.reasoning_effort,
            "sandbox_mode": definition.codex.sandbox_mode,
            "disabled_features": list(definition.codex.disabled_features),
            "agents_enabled": definition.codex.agents_enabled,
        },
        "description": definition.description,
        "max_turns": definition.max_turns,
        "model": definition.model,
        "name": definition.name,
        "tools": list(definition.tools),
    }
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    digest = hashlib.sha256(
        AGENT_DEFINITION_DIGEST_DOMAIN.encode("ascii") + b"\0" + canonical
    ).hexdigest()
    return f"sha256:{digest}"
