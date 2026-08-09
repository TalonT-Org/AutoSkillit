"""Canonical bundled-agent definitions and catalog loading authority."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .io import load_yaml
from .paths import pkg_root
from .types import (
    CODEX_EFFORT_MAPPING,
    CODEX_MODEL_ALIASES,
    CODEX_VALID_MODEL_IDS,
    CODEX_VALID_REASONING_EFFORTS,
    PARENT_SANDBOX_MODES,
)

__all__ = [
    "AGENT_DEFINITION_DIGEST_DOMAIN",
    "BUNDLED_EXPLORER_ROLES",
    "CODEX_EXPLORER_IDENTITY",
    "REPOSITORY_IMPACT_PROFILER_ROLE",
    "SEMANTIC_CODE_NAVIGATOR_ROLE",
    "AgentDef",
    "AgentDefinitionError",
    "CodexAgentProjectionDef",
    "agent_definition_digest",
    "load_agent_definition",
    "load_agent_definitions",
    "load_bundled_agent_definitions",
    "normalize_codex_cli_version",
]


AGENT_DEFINITION_DIGEST_DOMAIN = "autoskillit.agent-definition.v1"
CODEX_EXPLORER_IDENTITY: tuple[str, str] = ("gpt-5.6-luna", "max")
SEMANTIC_CODE_NAVIGATOR_ROLE: str = "semantic-code-navigator"
REPOSITORY_IMPACT_PROFILER_ROLE: str = "repository-impact-profiler"
BUNDLED_EXPLORER_ROLES: frozenset[str] = frozenset(
    {SEMANTIC_CODE_NAVIGATOR_ROLE, REPOSITORY_IMPACT_PROFILER_ROLE}
)
_AGENT_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")
_CODEX_CLI_VERSION_RE = re.compile(r"(?:codex-cli )?(?P<version>[0-9]+\.[0-9]+\.[0-9]+)")
_READ_ONLY_AGENT_TOOLS = frozenset({"Read", "Grep", "Glob", "LSP"})
_CODEX_DISABLEABLE_FEATURES = (
    "apps",
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
    "multi_agent",
    "multi_agent_v2",
    "plugin_sharing",
    "plugins",
    "remote_plugin",
    "request_permissions_tool",
    "shell_tool",
    "standalone_web_search",
    "tool_suggest",
    "unified_exec",
    "unified_exec_zsh_fork",
)
_CODEX_DISABLEABLE_FEATURE_SET = frozenset(_CODEX_DISABLEABLE_FEATURES)


class AgentDefinitionError(ValueError):
    """Raised when an agent definition cannot be validated canonically."""


def normalize_codex_cli_version(value: str) -> str:
    """Return the bare version for one exact supported Codex CLI token.

    Accepted inputs are ``X.Y.Z`` and ``codex-cli X.Y.Z`` only. Any other
    prefix, suffix, whitespace, or additional version component is rejected.
    """
    match = _CODEX_CLI_VERSION_RE.fullmatch(value)
    if match is None:
        raise ValueError(f"invalid Codex CLI version token: {value!r}")
    return match.group("version")


@dataclass(frozen=True, slots=True)
class CodexAgentProjectionDef:
    """Native Codex policy embedded in or derived from an agent definition."""

    model: str | None
    reasoning_effort: str | None
    sandbox_mode: str
    disabled_features: tuple[str, ...] = ()
    agents_enabled: bool = True
    web_search: Literal["disabled"] | None = None

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
        if self.sandbox_mode not in PARENT_SANDBOX_MODES:
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
        if self.web_search is not None and self.web_search != "disabled":
            raise AgentDefinitionError("Codex web_search must be 'disabled'")


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
            "web_search",
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
        web_search = raw.get("web_search")
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
            web_search,
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
    lines = content.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        raise AgentDefinitionError(f"{path}: missing YAML frontmatter")
    closing_index = next(
        (index for index, line in enumerate(lines[1:], start=1) if line.rstrip("\r\n") == "---"),
        None,
    )
    if closing_index is None:
        raise AgentDefinitionError(f"{path}: malformed YAML frontmatter")
    raw_meta = load_yaml("".join(lines[1:closing_index]))
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
        body="".join(lines[closing_index + 1 :]).strip(),
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


def load_bundled_agent_definitions() -> tuple[AgentDef, ...]:
    """Load the canonical agent catalog bundled with this package."""
    return load_agent_definitions(pkg_root() / "agents")


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
            "web_search": definition.codex.web_search,
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
