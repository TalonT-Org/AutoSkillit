"""Launch readiness probes leave generated homes untouched."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from autoskillit.core import (
    CODEX_HOME_ENV_VAR,
    CODEX_MODEL_ALIASES,
    SemanticAdaptationContext,
    resolve_executable_launch_binding,
)
from autoskillit.execution.backends import ClaudeCodeBackend, CodexBackend
from autoskillit.server.managed_join_prelaunch import prepare_managed_join_context
from tests.execution.backends._codex_fixtures import (
    generated_home_snapshot,
    installed_catalog,
    managed_source_home,
    use_bundled_catalog,
    with_migration_offer,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


@pytest.mark.parametrize("backend_name", ["codex", "claude-code"])
def test_launch_readiness_probe_preserves_generated_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend_name: str,
) -> None:
    binary_name = "codex" if backend_name == "codex" else "claude"
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    executable = binary_dir / binary_name
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    binding = resolve_executable_launch_binding(
        binary_name=binary_name,
        environment={"PATH": str(binary_dir)},
        cwd=tmp_path,
    )
    home = tmp_path / "generated-home"
    backend: CodexBackend | ClaudeCodeBackend
    if backend_name == "codex":
        selected = CODEX_MODEL_ALIASES["haiku"]
        source_home, raw = managed_source_home(
            tmp_path,
            catalog=with_migration_offer(installed_catalog(), selected, f"{selected}-successor"),
        )
        use_bundled_catalog(monkeypatch, raw)
        backend = CodexBackend(source_codex_home=source_home)
        context = prepare_managed_join_context(
            backend=backend,
            configured_model="haiku",
            state_root=tmp_path / "state",
            parent_id="launch1",
            launch_context="interactive",
        )
        assert isinstance(context, SemanticAdaptationContext)
        assert not backend.ensure_pre_launch(session_dir=home).errors
        backend.configure_managed_session_dir(
            home,
            adaptation_context=context,
            route="interactive-parent",
        )
        monkeypatch.setattr(
            "autoskillit.execution.backends._codex_probes._validate_mcp_probe",
            lambda *args, **kwargs: [],
        )
    else:
        home.mkdir()
        (home / "sentinel").write_text("untouched", encoding="utf-8")
        backend = ClaudeCodeBackend()
        monkeypatch.setattr(
            "autoskillit.execution.backends.claude.subprocess.run",
            lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "99.0.0", ""),
        )

    before = generated_home_snapshot(home)
    readiness = backend.probe_launch_readiness(session_dir=home, executable=binding)

    assert readiness.errors == ()
    assert generated_home_snapshot(home) == before
    if backend_name == "codex":
        assert readiness.attested_env == {
            CODEX_HOME_ENV_VAR: str(home),
            "CODEX_SQLITE_HOME": str(home),
        }
