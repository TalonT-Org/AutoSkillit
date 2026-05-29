"""Shared interactive session launch prelude for CLI commands."""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from autoskillit.core import NamedResume, NoResume

if TYPE_CHECKING:
    from autoskillit.core import CodingAgentBackend, ResumeSpec


@dataclass(frozen=True, slots=True)
class _InfraExitSignal:
    session_id: str
    category: str


def _run_interactive_session(
    system_prompt: str,
    *,
    initial_message: str | None = None,
    extra_env: dict[str, str] | None = None,
    resume_spec: ResumeSpec | None = None,
    project_dir: Path | None = None,
    required_env: frozenset[str] | None = None,
    backend: CodingAgentBackend | None = None,
) -> str | _InfraExitSignal | None:
    """Launch an interactive Claude Code session.

    Returns:
        str — session_id when a reload sentinel is found
        _InfraExitSignal — when an infrastructure exit is detected
        None — clean exit
    """
    from autoskillit.execution import read_session_state

    if backend is None:
        from autoskillit.config import load_config
        from autoskillit.execution import get_backend

        config = load_config()
        backend = get_backend(config.agent_backend.backend)

        from autoskillit.core import FEATURE_REGISTRY, is_feature_enabled

        for feat_name, feat_def in FEATURE_REGISTRY.items():
            if (
                feat_def.requires_backend_alignment
                and config.agent_backend.backend != "claude-code"
                and not is_feature_enabled(
                    feat_name,
                    config.features,
                    experimental_enabled=config.experimental_enabled,
                )
            ):
                from autoskillit.core import get_logger

                get_logger(__name__).warning(
                    "feature_gate_blocked",
                    extra={
                        "feature": feat_name,
                        "backend": config.agent_backend.backend,
                    },
                )
                backend = get_backend("claude-code")
                break

    if shutil.which(backend.binary_name()) is None:
        print(
            f"ERROR: '{backend.binary_name()}' not found. "
            "Install: https://docs.anthropic.com/en/docs/claude-code"
        )
        sys.exit(1)
    from autoskillit.cli.session._reload import consume_reload_sentinel
    from autoskillit.cli.ui._terminal import terminal_guard
    from autoskillit.core import (
        MARKETPLACE_PREFIX,
        DirectInstall,
        InfraExitCategory,
        detect_autoskillit_mcp_prefix,
        pkg_root,
    )

    _project_dir = project_dir if project_dir is not None else Path.cwd()
    if backend.capabilities.skill_injection_capable:
        plugin_source = (
            None
            if detect_autoskillit_mcp_prefix() == MARKETPLACE_PREFIX
            else DirectInstall(plugin_dir=pkg_root())
        )
        tools_arg: tuple[str, ...] = ("AskUserQuestion",)
    else:
        plugin_source = None
        tools_arg = ()

    spec = backend.build_interactive_cmd(
        initial_prompt=initial_message,
        resume_spec=resume_spec if resume_spec is not None else NoResume(),
        system_prompt=system_prompt,
        env_extras=extra_env,
        required_env=required_env,
        plugin_source=plugin_source,
        tools=tools_arg,
    )
    cmd = [*spec.cmd]
    with terminal_guard():
        result = subprocess.run(cmd, env=spec.env)
    reload_session_id = consume_reload_sentinel(_project_dir)
    if reload_session_id is not None:
        return reload_session_id
    if result.returncode != 0:
        from autoskillit.core import ensure_project_temp

        state_dir = ensure_project_temp(_project_dir) / "session_state"
        state = read_session_state(state_dir)
        if (
            state is not None
            and state.infra_exit_category
            and state.infra_exit_category != InfraExitCategory.COMPLETED
            and state.session_id
        ):
            return _InfraExitSignal(
                session_id=state.session_id, category=state.infra_exit_category
            )
        sys.exit(result.returncode)
    return None


def _write_order_entry(project_dir: Path, recipe_name: str | None) -> dict[str, str]:
    import uuid

    from autoskillit.cli.session._constants import SESSION_TYPE_ORDER
    from autoskillit.core import (
        LAUNCH_ID_ENV_VAR,
        SESSION_TYPE_ENV_VAR,
        write_registry_entry,
    )

    lid = uuid.uuid4().hex[:16]
    write_registry_entry(project_dir, lid, SESSION_TYPE_ORDER, recipe_name)
    return {SESSION_TYPE_ENV_VAR: SESSION_TYPE_ORDER, LAUNCH_ID_ENV_VAR: lid}


def _launch_cook_session(
    system_prompt: str,
    *,
    initial_message: str | None = None,
    extra_env: dict[str, str] | None = None,
    resume_spec: ResumeSpec = NoResume(),
    project_dir: Path | None = None,
    required_env: frozenset[str] | None = None,
    backend: CodingAgentBackend | None = None,
) -> None:
    """Launch an interactive Claude Code cook session with reload and infra-resume support."""
    _max_reloads = 10
    _max_infra_resumes = 3
    current_resume_spec: ResumeSpec = resume_spec
    _current_initial_message = initial_message
    seen_reload_ids: set[str] = set()
    infra_resume_count = 0
    while True:
        session_signal = _run_interactive_session(
            system_prompt,
            initial_message=_current_initial_message,
            extra_env=extra_env,
            resume_spec=current_resume_spec,
            project_dir=project_dir,
            required_env=required_env,
            backend=backend,
        )
        if session_signal is None:
            break
        if isinstance(session_signal, _InfraExitSignal):
            infra_resume_count += 1
            if infra_resume_count >= _max_infra_resumes:
                raise SystemExit(
                    f"Too many infrastructure resumes ({_max_infra_resumes} max). "
                    f"Last exit: {session_signal.category}"
                )
            current_resume_spec = NamedResume(session_id=session_signal.session_id)
            _current_initial_message = None
            continue
        if len(seen_reload_ids) >= _max_reloads:
            raise SystemExit(f"Too many reloads ({_max_reloads} max). Check for infinite loop.")
        if session_signal in seen_reload_ids:
            raise SystemExit(f"Repeated reload_id {session_signal!r} — aborting.")
        seen_reload_ids.add(session_signal)
        current_resume_spec = NamedResume(session_id=session_signal)
        _current_initial_message = None
