"""Codex explorer role projection — agent toml rendering, registration, refresh, clear.

Owns the projection, registration, and atomic-replacement mechanics
for the bundled explorer role set (BUNDLED_EXPLORER_ROLES) and the
parent config that fronts them. Imports the role-transport primitives
from `execution/backends/_codex/explorer_projection.py`.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import tomllib
from collections.abc import Mapping
from pathlib import Path

from autoskillit.core import (
    BUNDLED_EXPLORER_ROLES,
    CODEX_EFFORT_MAPPING,
    CODEX_MODEL_ALIASES,
    WEB_EVIDENCE_RESEARCHER_ROLE,
    AgentDef,
    SkillExecutionRole,
    agent_definition_digest,
    atomic_write,
    get_logger,
    load_bundled_agent_definitions,
)
from autoskillit.execution.backends._claude_prompt import codex_discipline_suffix
from autoskillit.execution.backends._codex.explorer_projection import (
    _EXPLORER_ROLE_NAMES,
    _canonical_explorer_mcp_transport,
    _direct_agent_mcp_tools,
    _explorer_mcp_projection,
    _render_direct_role_mcp_lines,
    _render_parent_explorer_config,
    _render_role_mcp_lines,
    _resolve_role_mcp_transport,
    _validated_explorer_binding_env,
    _validated_explorer_binding_envs,
)
from autoskillit.execution.backends._codex_config import (
    _CODEX_AGENT_NAME_COLLISIONS,
    _format_toml_value,
)

logger = get_logger(__name__)


def _bundled_agent_definitions() -> tuple[AgentDef, ...]:
    return load_bundled_agent_definitions()


def _canonical_codex_model_effort(
    model_class: str | None,
    reasoning_effort: str | None = None,
) -> tuple[str, str | None]:
    if model_class is None:
        return "", reasoning_effort
    model = CODEX_MODEL_ALIASES[model_class]
    return model, reasoning_effort or CODEX_EFFORT_MAPPING.get(model_class)


def _preflight_agent_projection(
    session_dir: Path,
    definitions: tuple[AgentDef, ...],
    *,
    exact_definitions: bool,
) -> tuple[AgentDef, ...]:
    """Validate the complete role set and select roles safe to project."""
    names = tuple(definition.name for definition in definitions)
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ValueError(f"duplicate Codex agent definitions: {duplicates}")
    built_in_collisions = sorted(set(names) & _CODEX_AGENT_NAME_COLLISIONS)
    if built_in_collisions:
        raise ValueError(f"Codex built-in agent name collision: {built_in_collisions}")
    config_path = session_dir / "config.toml"
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    if exact_definitions and any(map(_direct_agent_mcp_tools, definitions)):
        _canonical_explorer_mcp_transport(config_path)
    configured_agents = config.get("agents", {})
    if not isinstance(configured_agents, dict):
        raise ValueError("Codex config agents table must be a mapping")
    protected_names = (
        set(names)
        if exact_definitions
        else {*BUNDLED_EXPLORER_ROLES, WEB_EVIDENCE_RESEARCHER_ROLE}
    )
    ambient_collisions = sorted(set(names) & set(configured_agents) & protected_names)
    if ambient_collisions:
        raise ValueError(f"ambient Codex agent name collision: {ambient_collisions}")

    agents_dir = session_dir / "agents"
    if agents_dir.exists() and not agents_dir.is_dir():
        raise ValueError(f"Codex agents path is not a directory: {agents_dir}")
    artifact_collisions = sorted(
        definition.name
        for definition in definitions
        if (agents_dir / f"{definition.name}.toml").exists()
    )
    if artifact_collisions:
        raise ValueError(f"ambient Codex agent artifact collision: {artifact_collisions}")
    return tuple(
        definition for definition in definitions if definition.name not in configured_agents
    )


def _render_agent_toml(
    definition: AgentDef,
    *,
    explorer_binding_env: Mapping[str, str] | None = None,
    explorer_mcp_transport: Mapping[str, object] | None = None,
    project_explorer_mcp: bool = False,
) -> str:
    """Render and parse one role before its output directory is touched."""
    direct_mcp_tools = _direct_agent_mcp_tools(definition)
    digest = agent_definition_digest(definition)
    lines = [
        f"name = {_format_toml_value(definition.name)}",
        f"description = {_format_toml_value(definition.description)}",
        f"sandbox_mode = {_format_toml_value(definition.codex.sandbox_mode)}",
    ]
    if definition.codex.model is not None:
        lines.append(f"model = {_format_toml_value(definition.codex.model)}")
    if definition.codex.reasoning_effort is not None:
        lines.append(
            f"model_reasoning_effort = {_format_toml_value(definition.codex.reasoning_effort)}"
        )
    if definition.codex.web_search is not None:
        lines.append(f"web_search = {_format_toml_value(definition.codex.web_search)}")
    body = (
        f"{definition.body}\n\n"
        f"AutoSkillit agent definition digest: {digest}\n\n"
        f"{codex_discipline_suffix()}"
    )
    lines.append(f"instructions = '''\n{body}\n'''")
    lines.append(f"developer_instructions = '''\n{body}\n'''")
    if definition.codex.disabled_features:
        lines.append("[features]")
        lines.extend(f"{feature} = false" for feature in definition.codex.disabled_features)
    if not definition.codex.agents_enabled:
        lines.extend(("[agents]", "enabled = false"))
    if explorer_binding_env is not None and not project_explorer_mcp:
        raise ValueError("an explorer binding requires an explorer MCP projection")
    if explorer_mcp_transport is not None and not project_explorer_mcp and not direct_mcp_tools:
        raise ValueError("an explorer MCP transport requires an explorer MCP projection")
    if project_explorer_mcp:
        if explorer_mcp_transport is None:
            raise ValueError("an explorer MCP projection requires a canonical transport")
        projection = _explorer_mcp_projection(
            explorer_mcp_transport,
            explorer_binding_env,
        )
        lines.extend(_render_role_mcp_lines(projection, explorer_binding_env))
    elif direct_mcp_tools:
        lines.extend(_render_direct_role_mcp_lines(explorer_mcp_transport, direct_mcp_tools))
    rendered = "\n".join(lines) + "\n"
    tomllib.loads(rendered)
    return rendered


def _eligible_agent_definitions(
    definitions: tuple[AgentDef, ...],
    bindings: Mapping[str, Mapping[str, str]],
    *,
    exact: bool,
) -> tuple[AgentDef, ...]:
    definitions = tuple(d for d in definitions if not d.reader_tools)
    if exact:
        return definitions
    return tuple(
        definition
        for definition in definitions
        if definition.name not in BUNDLED_EXPLORER_ROLES or definition.name in bindings
    )


def _generate_agent_tomls(
    session_dir: Path,
    agent_defs: tuple[AgentDef, ...] | None = None,
    *,
    explorer_binding_envs: Mapping[str, Mapping[str, str]] | None = None,
    explorer_mcp_transport: Mapping[str, object] | None = None,
) -> int:
    definitions = _bundled_agent_definitions() if agent_defs is None else agent_defs
    bindings = explorer_binding_envs or {}
    eligible = _eligible_agent_definitions(
        definitions,
        bindings,
        exact=agent_defs is not None,
    )
    direct_mcp_transport = _resolve_role_mcp_transport(
        session_dir, eligible, bindings, explorer_mcp_transport
    )
    rendered = {
        definition.name: _render_agent_toml(
            definition,
            explorer_binding_env=bindings.get(definition.name),
            explorer_mcp_transport=(
                direct_mcp_transport
                if definition.name in bindings or _direct_agent_mcp_tools(definition)
                else None
            ),
            project_explorer_mcp=definition.name in bindings,
        )
        for definition in eligible
    }
    out_dir = session_dir / "agents"
    out_dir.mkdir(exist_ok=True)
    for definition in eligible:
        toml_path = out_dir / f"{definition.name}.toml"
        atomic_write(toml_path, rendered[definition.name])
    logger.debug("codex_agents_generated", count=len(eligible), dest=str(out_dir))
    return len(eligible)


def _register_agent_tomls(
    session_dir: Path,
    agent_defs: tuple[AgentDef, ...] | None = None,
    *,
    explorer_binding_envs: Mapping[str, Mapping[str, str]] | None = None,
) -> int:
    config_path = session_dir / "config.toml"
    config_text = config_path.read_text(encoding="utf-8")
    tomllib.loads(config_text)
    registrations: list[str] = []
    definitions = _bundled_agent_definitions() if agent_defs is None else agent_defs
    bindings = explorer_binding_envs or {}
    eligible = _eligible_agent_definitions(
        definitions,
        bindings,
        exact=agent_defs is not None,
    )
    for definition in eligible:
        agent_path = session_dir / "agents" / f"{definition.name}.toml"
        agent = tomllib.loads(agent_path.read_text(encoding="utf-8"))
        if agent.get("name") != definition.name:
            raise ValueError(f"generated agent identity mismatch: {agent_path}")
        registrations.extend(
            [
                f"[agents.{_format_toml_value(definition.name)}]",
                f"description = {_format_toml_value(definition.description)}",
                f"config_file = {_format_toml_value(f'agents/{agent_path.name}')}",
                "",
            ]
        )
    if not registrations:
        return 0
    separator = "\n" if config_text.endswith("\n") else "\n\n"
    registration_text = "\n".join(registrations)
    updated = f"{config_text}{separator}{registration_text}"
    tomllib.loads(updated)
    atomic_write(config_path, updated)
    return len(registrations) // 4


def _validate_existing_explorer_role_toml(
    toml_path: Path,
    definition: AgentDef,
    *,
    require_binding_env: bool,
    explorer_mcp_transport: Mapping[str, object],
) -> dict[str, str] | None:
    """Validate persisted role identity and recover its binding environment."""
    try:
        current = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"invalid materialized explorer role {toml_path}: {exc}") from exc
    servers = current.get("mcp_servers")
    if not isinstance(servers, dict) or set(servers) != {"autoskillit"}:
        raise ValueError(f"materialized explorer role missing MCP projection: {toml_path}")
    current_server = servers.get("autoskillit")
    if not isinstance(current_server, dict):
        raise ValueError(f"materialized explorer role missing MCP projection: {toml_path}")
    current_server = dict(current_server)
    current_env = current_server.pop("env", None)
    expected_server = _explorer_mcp_projection(explorer_mcp_transport, None)
    if current_server != expected_server:
        raise ValueError(f"materialized explorer role has a divergent MCP projection: {toml_path}")
    if current.get("name") != definition.name:
        raise ValueError(f"materialized explorer role identity mismatch: {toml_path}")
    if current_env is None and not require_binding_env:
        return None
    if not isinstance(current_env, dict):
        raise ValueError(
            f"materialized explorer role has an invalid binding environment: {toml_path}"
        )
    return _validated_explorer_binding_env(definition.name, current_env)


def _validate_existing_parent_explorer_projection(
    session_config: Mapping[str, object],
    *,
    require_binding_env: bool,
    explorer_mcp_transport: Mapping[str, object],
) -> dict[str, str] | None:
    """Validate the parent half of the shared-principal MCP projection."""
    if session_config.get("sandbox_mode") != "read-only":
        raise ValueError("materialized explorer parent must be read-only")
    servers = session_config.get("mcp_servers")
    if not isinstance(servers, dict) or set(servers) != {"autoskillit"}:
        raise ValueError("materialized explorer parent must configure exactly one MCP server")
    current_server = servers.get("autoskillit")
    if not isinstance(current_server, dict):
        raise ValueError("materialized explorer parent is missing its MCP projection")
    current_server = dict(current_server)
    current_env = current_server.pop("env", None)
    expected_server = _explorer_mcp_projection(explorer_mcp_transport, None)
    if current_server != expected_server:
        raise ValueError("materialized explorer parent has a divergent MCP projection")
    if current_env is None and not require_binding_env:
        return None
    if not isinstance(current_env, dict):
        raise ValueError("materialized explorer parent has an invalid binding environment")
    return _validated_explorer_binding_env("parent", current_env)


def _validate_materialized_explorer_roles(
    session_dir: Path,
    definitions: tuple[AgentDef, ...],
    roles: frozenset[str],
    *,
    require_binding_env: bool,
) -> tuple[dict[str, AgentDef], dict[str, object], str]:
    """Validate registered persisted explorer artifacts before a grouped rewrite."""
    if any(type(role) is not str for role in roles):
        raise ValueError("explorer role cleanup set must contain only text names")
    definitions_by_name = {definition.name: definition for definition in definitions}
    if roles != _EXPLORER_ROLE_NAMES or not roles <= set(definitions_by_name):
        raise ValueError(f"unknown explorer roles: {sorted(roles - set(definitions_by_name))}")
    agents_dir = session_dir / "agents"
    if not agents_dir.is_dir():
        raise ValueError(f"materialized Codex agents directory is missing: {agents_dir}")
    config_path = session_dir / "config.toml"
    try:
        config_text = config_path.read_text(encoding="utf-8")
        session_config = tomllib.loads(config_text)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"invalid materialized Codex config: {exc}") from exc
    registered_agents = session_config.get("agents")
    if not isinstance(registered_agents, dict):
        raise ValueError("materialized Codex config has no agent registrations")
    explorer_mcp_transport = _canonical_explorer_mcp_transport(config_path)
    projected_bindings = [
        _validate_existing_parent_explorer_projection(
            session_config,
            require_binding_env=require_binding_env,
            explorer_mcp_transport=explorer_mcp_transport,
        )
    ]

    selected: dict[str, AgentDef] = {}
    for role in sorted(roles):
        definition = definitions_by_name[role]
        registration = registered_agents.get(role)
        expected_path = f"agents/{role}.toml"
        if not isinstance(registration, dict) or registration.get("config_file") != expected_path:
            raise ValueError(
                f"materialized Codex config has no canonical registration for {role!r}"
            )
        projected_bindings.append(
            _validate_existing_explorer_role_toml(
                agents_dir / f"{role}.toml",
                definition,
                require_binding_env=require_binding_env,
                explorer_mcp_transport=explorer_mcp_transport,
            )
        )
        selected[role] = definition
    if any(binding != projected_bindings[0] for binding in projected_bindings[1:]):
        raise ValueError("materialized explorer bindings diverge from the shared principal")
    return selected, explorer_mcp_transport, config_text


def _atomically_replace_explorer_projection(
    session_dir: Path,
    rendered_config: str,
    rendered_roles: Mapping[str, str],
) -> None:
    """Swap the parent and both roles as one staged session-root transaction."""
    stage_root = Path(
        tempfile.mkdtemp(prefix=".autoskillit-explorer-refresh-", dir=session_dir.parent)
    )
    staged_session = stage_root / "session"
    backup_session = stage_root / "previous-session"
    moved_original = False
    try:
        shutil.copytree(session_dir, staged_session, symlinks=True)
        atomic_write(staged_session / "config.toml", rendered_config)
        for role, content in rendered_roles.items():
            atomic_write(staged_session / "agents" / f"{role}.toml", content)
        os.replace(session_dir, backup_session)
        moved_original = True
        try:
            os.replace(staged_session, session_dir)
        except OSError as install_error:
            try:
                os.replace(backup_session, session_dir)
            except OSError as restore_error:
                raise restore_error from install_error
            moved_original = False
            raise
        moved_original = False
    finally:
        if not moved_original:
            shutil.rmtree(stage_root, ignore_errors=True)


def refresh_explorer_binding_env(
    session_dir: Path,
    explorer_binding_env: Mapping[str, Mapping[str, str]],
) -> None:
    """Atomically replace only server-issued explorer binding values on resume.

    The helper validates the persisted parent and both definition-derived role
    layers before staging a replacement session root, so a failed refresh
    cannot leave any of the three configs on a different principal.
    """
    definitions = _bundled_agent_definitions()
    binding_envs = _validated_explorer_binding_envs(definitions, explorer_binding_env)
    if not binding_envs:
        return

    definitions_by_name, explorer_mcp_transport, config_text = (
        _validate_materialized_explorer_roles(
            session_dir,
            definitions,
            frozenset(binding_envs),
            require_binding_env=True,
        )
    )
    shared_binding = next(iter(binding_envs.values()))
    rendered_config = _render_parent_explorer_config(
        config_text,
        explorer_mcp_transport=explorer_mcp_transport,
        explorer_binding_env=shared_binding,
    )
    rendered_roles: dict[str, str] = {}
    for role, definition in definitions_by_name.items():
        rendered_roles[role] = _render_agent_toml(
            definition,
            explorer_binding_env=shared_binding,
            explorer_mcp_transport=explorer_mcp_transport,
            project_explorer_mcp=True,
        )
    _atomically_replace_explorer_projection(
        session_dir,
        rendered_config,
        rendered_roles,
    )


def clear_explorer_binding_env(session_dir: Path, roles: frozenset[str]) -> None:
    """Atomically scrub persisted explorer secrets while retaining the broker allowlist."""
    if not isinstance(roles, frozenset):
        raise ValueError("explorer role cleanup set must be a frozenset")
    if not roles:
        return
    definitions = _bundled_agent_definitions()
    definitions_by_name, explorer_mcp_transport, config_text = (
        _validate_materialized_explorer_roles(
            session_dir,
            definitions,
            roles,
            require_binding_env=False,
        )
    )
    rendered_config = _render_parent_explorer_config(
        config_text,
        explorer_mcp_transport=explorer_mcp_transport,
        explorer_binding_env=None,
    )
    rendered_roles = {
        role: _render_agent_toml(
            definition,
            explorer_mcp_transport=explorer_mcp_transport,
            project_explorer_mcp=True,
        )
        for role, definition in definitions_by_name.items()
    }
    _atomically_replace_explorer_projection(
        session_dir,
        rendered_config,
        rendered_roles,
    )


def _render_parent_sandbox_config(config_text: str, sandbox_mode: str) -> str:
    """Render the generated-home config with the normalized parent sandbox."""
    if sandbox_mode not in {"read-only", "workspace-write"}:
        raise ValueError(f"unsupported parent sandbox mode: {sandbox_mode!r}")
    lines = config_text.splitlines()
    table_start = next(
        (i for i, line in enumerate(lines) if line.lstrip().startswith("[")), len(lines)
    )
    key_indexes = [
        i
        for i, line in enumerate(lines[:table_start])
        if line.split("=", 1)[0].strip() == "sandbox_mode"
    ]
    if len(key_indexes) > 1:
        raise ValueError("generated Codex config has duplicate top-level sandbox_mode keys")
    if key_indexes:
        del lines[key_indexes[0]]
    if sandbox_mode == "read-only":
        table_start = next(
            (i for i, line in enumerate(lines) if line.lstrip().startswith("[")), len(lines)
        )
        replacement = f"sandbox_mode = {_format_toml_value(sandbox_mode)}"
        lines.insert(table_start, replacement)
    updated = "\n".join(lines) + "\n"
    parsed = tomllib.loads(updated)
    if sandbox_mode == "read-only" and parsed.get("sandbox_mode") != sandbox_mode:
        raise ValueError("generated Codex config did not retain the parent sandbox mode")
    if sandbox_mode == "workspace-write" and "sandbox_mode" in parsed:
        raise ValueError("generated Codex config retained a workspace-write sandbox pin")
    return updated


def _render_cli_auth_store(config_text: str, execution_role: SkillExecutionRole) -> str:
    """Pin ORCHESTRATOR homes to the durable file credential store."""
    if execution_role is not SkillExecutionRole.ORCHESTRATOR:
        return config_text
    lines = config_text.splitlines()
    table_start = next(
        (i for i, line in enumerate(lines) if line.lstrip().startswith("[")), len(lines)
    )
    key_indexes = [
        i
        for i, line in enumerate(lines[:table_start])
        if line.split("=", 1)[0].strip() == "cli_auth_credentials_store"
    ]
    if len(key_indexes) > 1:
        raise ValueError(
            "generated Codex config has duplicate top-level cli_auth_credentials_store keys"
        )
    if key_indexes:
        del lines[key_indexes[0]]
        table_start -= 1
    lines.insert(table_start, 'cli_auth_credentials_store = "file"')
    updated = "\n".join(lines) + "\n"
    if tomllib.loads(updated).get("cli_auth_credentials_store") != "file":
        raise ValueError("generated Codex config did not retain the file credential store")
    return updated


def _materialize_profile_skills(
    session_dir: Path,
    *,
    source_codex_home: Path | None = None,
) -> int:
    """Symlink source-home profile skills into a generated Codex home.

    Scans the selected Codex home's ``skills`` for subdirectories containing
    SKILL.md. Each is symlinked into session_dir/skills/<name>. Falls back
    to shutil.copytree if symlink creation fails. Subdirectories without
    SKILL.md are skipped. Returns the number of skills materialized.
    """
    source_home = Path.home() / ".codex" if source_codex_home is None else Path(source_codex_home)
    profile_skills_root = source_home / "skills"
    if not profile_skills_root.is_dir():
        return 0
    count = 0
    skills_base = session_dir / "skills"
    skills_base.mkdir(parents=True, exist_ok=True)
    entries = list(profile_skills_root.iterdir())
    for entry in entries:
        if not entry.is_dir() or not (entry / "SKILL.md").is_file():
            continue
        target = skills_base / entry.name
        if target.exists() or target.is_symlink():
            continue
        try:
            target.symlink_to(entry.resolve())
        except OSError:
            logger.debug(
                "codex_profile_skill_symlink_failed_using_copytree",
                skill=entry.name,
                exc_info=True,
            )
            shutil.copytree(entry, target)
        count += 1
    return count


__all__ = [
    "clear_explorer_binding_env",
    "refresh_explorer_binding_env",
]
