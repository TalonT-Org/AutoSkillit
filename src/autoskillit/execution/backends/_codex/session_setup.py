"""Codex session-directory setup: config projection, auth linking, and agent-TOML registration."""

from __future__ import annotations

import shutil
import tomllib
from collections.abc import Mapping
from pathlib import Path

from autoskillit.core import (
    BUNDLED_EXPLORER_ROLES,
    AgentDef,
    SkillExecutionRole,
    atomic_write,
    get_logger,
)
from autoskillit.execution.backends import _codex_config as _codex_cfg
from autoskillit.execution.backends._codex.explorer_projection import (
    _canonical_explorer_mcp_transport,
    _render_parent_explorer_config,
    _validate_injected_explorer_parent_policy,
    _validated_explorer_binding_envs,
)
from autoskillit.execution.backends._codex_explorer_projection import (
    _bundled_agent_definitions,
    _generate_agent_tomls,
    _preflight_agent_projection,
    _register_agent_tomls,
    _render_cli_auth_store,
    _render_parent_sandbox_config,
)

logger = get_logger(__name__)


def setup_codex_session_dir(
    source_codex_home: Path,
    session_dir: Path,
    *,
    parent_sandbox_mode: str = "workspace-write",
    agent_defs: tuple[AgentDef, ...] | None = None,
    explorer_binding_env: Mapping[str, Mapping[str, str]] | None = None,
    execution_role: SkillExecutionRole = SkillExecutionRole.SESSION,
) -> frozenset[str]:
    """Project the pre-launch config snapshot, link auth, and register agent TOMLs.

    Pure-function form of ``CodexSessionCommandMixin.setup_session_dir`` — takes the
    resolved ``source_codex_home`` explicitly instead of reading it off ``self``, so
    it carries no dependency on the mixin's instance state.
    """
    config_path = session_dir / "config.toml"
    if not config_path.is_file():
        raise FileNotFoundError(f"pre-launch Codex config snapshot is missing: {config_path}")
    definitions = _bundled_agent_definitions() if agent_defs is None else agent_defs
    explorer_binding_envs = _validated_explorer_binding_envs(definitions, explorer_binding_env)
    explorer_mcp_transport = (
        _canonical_explorer_mcp_transport(config_path) if explorer_binding_envs else None
    )
    if explorer_binding_envs and parent_sandbox_mode != "read-only":
        raise ValueError("explorer shared-principal projection requires a read-only parent")
    policy_definitions = definitions if explorer_binding_envs else agent_defs
    _validate_injected_explorer_parent_policy(policy_definitions, parent_sandbox_mode)
    projected_definitions = _preflight_agent_projection(
        session_dir,
        definitions,
        exact_definitions=agent_defs is not None,
    )
    rendered_parent_config = _render_parent_sandbox_config(
        config_path.read_text(encoding="utf-8"),
        parent_sandbox_mode,
    )
    rendered_parent_config = _render_cli_auth_store(
        rendered_parent_config,
        execution_role,
    )
    if explorer_binding_envs:
        assert explorer_mcp_transport is not None
        shared_binding = next(iter(explorer_binding_envs.values()))
        rendered_parent_config = _render_parent_explorer_config(
            rendered_parent_config,
            explorer_mcp_transport=explorer_mcp_transport,
            explorer_binding_env=shared_binding,
        )
    finalized_config = tomllib.loads(rendered_parent_config)
    if (
        execution_role is SkillExecutionRole.ORCHESTRATOR
        and finalized_config.get("cli_auth_credentials_store") != "file"
    ):
        raise ValueError("finalized ORCHESTRATOR config lost the file credential store")
    atomic_write(config_path, rendered_parent_config)

    auth_source = source_codex_home / "auth.json"
    auth_dest = session_dir / "auth.json"
    auth_target = auth_source.resolve(strict=False)
    auth_dest.symlink_to(auth_target)
    logger.debug(
        "codex_auth_symlink",
        src=str(auth_target),
        dest=str(auth_dest),
    )

    env_source = source_codex_home / ".env"
    if env_source.exists():
        shutil.copy2(env_source, session_dir / ".env")

    toml_definitions = projected_definitions
    if not explorer_binding_envs and agent_defs is None:
        toml_definitions = tuple(
            d for d in projected_definitions if d.name not in BUNDLED_EXPLORER_ROLES
        )
    _generate_agent_tomls(
        session_dir,
        toml_definitions,
        explorer_binding_envs=explorer_binding_envs,
        explorer_mcp_transport=explorer_mcp_transport,
    )
    registered = _register_agent_tomls(
        session_dir,
        toml_definitions,
        explorer_binding_envs=explorer_binding_envs,
    )
    logger.debug("codex_agents_registered", count=registered)
    return _codex_cfg.effective_codex_agent_names(session_dir)
