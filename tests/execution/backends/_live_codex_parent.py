"""Shared real-Codex parent setup used by explorer live gates."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from autoskillit.core.agent_definition import AgentDef
from autoskillit.execution.backends._codex_config import ensure_codex_mcp_registered
from autoskillit.execution.backends._codex_hooks import sync_hooks_to_codex_config
from autoskillit.execution.backends.codex import CodexBackend

CODEX_LIVE_PROCESS_ENV_ALLOWLIST = frozenset(
    {
        "ALL_PROXY",
        "CODEX_API_KEY",
        "CODEX_BASE_URL",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "OPENAI_ORGANIZATION",
        "OPENAI_ORG_ID",
        "OPENAI_PROJECT_ID",
        "PATH",
        "REQUESTS_CA_BUNDLE",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
        "all_proxy",
        "http_proxy",
        "https_proxy",
        "no_proxy",
    }
)


@dataclass(frozen=True, slots=True)
class LiveCodexParentSession:
    """The authenticated isolated home/session and filtered process environment."""

    profile_home: Path
    profile_codex_home: Path
    session_home: Path
    env: dict[str, str]
    explorer_binding_env: dict[str, dict[str, str]] | None


def prepare_live_codex_parent(
    *,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_auth: Path,
    agent_defs: tuple[AgentDef, ...],
    explorer_binding_env: Mapping[str, Mapping[str, str]] | None = None,
    explorer_binding_env_factory: (
        Callable[[Path], Mapping[str, Mapping[str, str]]] | None
    ) = None,
    profile_home_name: str = "profile-home",
    session_home_name: str = "session-home",
) -> LiveCodexParentSession:
    """Build the exact isolated Codex home/session used by credentialed probes."""
    if explorer_binding_env is not None and explorer_binding_env_factory is not None:
        raise ValueError("provide an explorer binding map or factory, not both")
    profile_home = tmp_path / profile_home_name
    profile_codex_home = profile_home / ".codex"
    session_home = tmp_path / session_home_name
    for directory in (profile_codex_home, session_home):
        directory.mkdir(parents=True)
    if source_auth.is_file():
        (profile_codex_home / "auth.json").symlink_to(source_auth.resolve())

    monkeypatch.setenv("HOME", str(profile_home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: profile_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(profile_home / ".config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(profile_home / ".local" / "share"))
    profile_config = profile_codex_home / "config.toml"
    ensure_codex_mcp_registered(config_path=profile_config, headless_auto_gate=False)
    sync_hooks_to_codex_config(config_path=profile_config)
    backend = CodexBackend()
    assert backend.ensure_pre_launch(session_dir=session_home) == []
    issued_binding_env = (
        explorer_binding_env_factory(session_home)
        if explorer_binding_env_factory is not None
        else explorer_binding_env
    )
    copied_binding_env = (
        {role: dict(values) for role, values in issued_binding_env.items()}
        if issued_binding_env is not None
        else None
    )
    backend.setup_session_dir(
        session_home,
        parent_sandbox_mode="read-only",
        agent_defs=agent_defs,
        explorer_binding_env=copied_binding_env,
    )
    env = {
        key: value for key, value in os.environ.items() if key in CODEX_LIVE_PROCESS_ENV_ALLOWLIST
    }
    env.update(
        {
            "HOME": str(profile_home),
            "CODEX_HOME": str(session_home),
            "XDG_CONFIG_HOME": str(profile_home / ".config"),
            "XDG_DATA_HOME": str(profile_home / ".local" / "share"),
        }
    )
    return LiveCodexParentSession(
        profile_home=profile_home,
        profile_codex_home=profile_codex_home,
        session_home=session_home,
        env=env,
        explorer_binding_env=copied_binding_env,
    )


def run_live_codex_parent(
    *,
    env: dict[str, str],
    cwd: Path,
    model: str,
    prompt: str,
    timeout: int,
    stdout: Any,
    stderr: Any,
    text: bool,
    resume_thread_id: str | None = None,
) -> subprocess.CompletedProcess[Any]:
    """Execute or resume the common real-Codex parent used by both live gates."""
    invocation = [
        "codex",
        "exec",
        "--json",
        "--sandbox",
        "read-only",
        "--model",
        model,
    ]
    if resume_thread_id is not None:
        if not resume_thread_id.strip():
            raise ValueError("resume_thread_id must be a non-empty string")
        invocation.extend(("resume", resume_thread_id))
    invocation.append(prompt)
    return subprocess.run(  # noqa: S603
        invocation,
        cwd=cwd,
        env=env,
        stdout=stdout,
        stderr=stderr,
        text=text,
        timeout=timeout,
        check=False,
    )
