"""Authenticated live gate for the sterile Codex evidence-reader path."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from autoskillit.core import (
    EVIDENCE_READER_ENV_FORWARD_VARS,
    agent_definition_digest,
    canonical_reader_tools_to_bare,
    load_bundled_agent_definitions,
)
from autoskillit.execution import evidence_reader as reader_launcher
from autoskillit.execution.evidence_reader import (
    EvidenceReaderResultStatus,
    evidence_reader_provider_environment,
    launch_evidence_reader,
)
from autoskillit.exploration import capture_stable_artifact
from autoskillit.pipeline import ToolContext
from autoskillit.server.tools._evidence_reader import (
    create_evidence_reader_invocation,
    evidence_reader_scope_digest,
    load_evidence_reader_receipts,
    revoke_evidence_reader_invocation,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.large, pytest.mark.timeout(1200)]

_LIVE_ENV = "AUTOSKILLIT_EVIDENCE_READER_LIVE_GATE"
_ARTIFACT_DIR_ENV = "AUTOSKILLIT_EVIDENCE_READER_LIVE_GATE_ARTIFACT_DIR"
_RUN_ID_ENV = "AUTOSKILLIT_EVIDENCE_READER_LIVE_GATE_RUN_ID"
_AUTH_ENV_NAMES = ("CODEX_API_KEY", "OPENAI_API_KEY")
_ROLE = "pr-source-reader"
_POLICY = "read-only"

_skip_unless_live_gate = pytest.mark.skipif(
    not os.environ.get(_LIVE_ENV)
    or not shutil.which("codex")
    or not any(os.environ.get(name) for name in _AUTH_ENV_NAMES),
    reason=f"Set {_LIVE_ENV}=1 and provide a Codex API key for the live reader gate",
)


def _git(repository: Path, *args: str) -> str:
    return subprocess.run(  # noqa: S603
        ["git", "-C", str(repository), *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout


def _initialize_repository(repository: Path) -> Path:
    repository.mkdir()
    _git(repository, "init", "-q", "-b", "main")
    _git(repository, "config", "user.email", "evidence-reader-gate@example.invalid")
    _git(repository, "config", "user.name", "Evidence Reader Gate")
    artifact = repository / "evidence.txt"
    artifact.write_text(
        "# Release evidence\ntitle: Behavioral evidence reader live gate\n",
        encoding="utf-8",
    )
    _git(repository, "add", "evidence.txt")
    _git(repository, "commit", "-q", "-m", "add live-gate artifact")
    return artifact


def _write_server_wrapper(path: Path, repository: Path) -> None:
    path.write_text(
        "#!/bin/sh\n"
        f"export AUTOSKILLIT_PROJECT_DIR={shlex.quote(str(repository))}\n"
        f"exec {shlex.quote(sys.executable)} -m autoskillit\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


@_skip_unless_live_gate
@pytest.mark.smoke
def test_live_codex_evidence_reader_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run_id = os.environ.get(_RUN_ID_ENV)
    artifact_dir_value = os.environ.get(_ARTIFACT_DIR_ENV)
    assert run_id and artifact_dir_value
    artifact_dir = Path(artifact_dir_value).resolve()
    evidence_path = artifact_dir / "live-evidence-reader-gate.json"

    repository = tmp_path / "repository"
    artifact = _initialize_repository(repository)
    initial_head = _git(repository, "rev-parse", "HEAD").strip()
    initial_status = _git(repository, "status", "--porcelain=v2", "--untracked-files=all")
    initial_content = artifact.read_bytes()
    command_canary = repository / "command-surface-was-used"

    deadline = time.monotonic() + 900
    capture = capture_stable_artifact(repository, "evidence.txt", deadline=deadline)
    definition = next(item for item in load_bundled_agent_definitions() if item.name == _ROLE)
    bare_tools = canonical_reader_tools_to_bare(definition.reader_tools)
    broker_project = tmp_path / "broker-project"
    broker_project.mkdir()
    tool_ctx = cast(
        ToolContext,
        SimpleNamespace(temp_dir=broker_project / ".autoskillit" / "temp"),
    )
    invocation = create_evidence_reader_invocation(
        tool_ctx,
        capture,
        caller_session_id="live-evidence-reader-gate",
        role=_ROLE,
        role_definition_digest=agent_definition_digest(definition),
        canonical_tools=definition.reader_tools,
        bare_tools=bare_tools,
        policy=_POLICY,
        expires_at=time.time() + 900,
    )
    environment = dict(invocation.environment)
    scope_digest = evidence_reader_scope_digest(tool_ctx, environment)

    wrapper = tmp_path / "autoskillit-reader-server"
    _write_server_wrapper(wrapper, broker_project)
    transport: dict[str, object] = {
        "command": str(wrapper.resolve()),
        "args": [],
        "env_vars": sorted(EVIDENCE_READER_ENV_FORWARD_VARS),
        "startup_timeout_sec": 60,
        "tool_timeout_sec": 60,
    }

    isolated_directories: list[Path] = []
    real_mkdtemp = reader_launcher.tempfile.mkdtemp

    def recording_mkdtemp(*args: Any, **kwargs: Any) -> str:
        created = real_mkdtemp(*args, **kwargs)
        isolated_directories.append(Path(created))
        return created

    monkeypatch.setattr(reader_launcher.tempfile, "mkdtemp", recording_mkdtemp)
    try:
        result = launch_evidence_reader(
            definition,
            invocation,
            prompt=json.dumps(
                {"artifact_path": "evidence.txt", "requested_fields": ["title"]},
                separators=(",", ":"),
            ),
            mcp_transport=transport,
            provider_env=evidence_reader_provider_environment(),
            repository_root=repository,
            worktree_root=repository,
            common_git_dir=repository / ".git",
            expected_scope_digest=scope_digest,
            expected_snapshot_digest=capture.snapshot_digest,
            deadline=deadline,
        )
        receipts = load_evidence_reader_receipts(tool_ctx, environment)
    finally:
        revoke_evidence_reader_invocation(tool_ctx, environment)

    payload = json.loads(result.payload_json)
    assert result.status is EvidenceReaderResultStatus.ANSWERED
    assert result.role == _ROLE
    assert result.authorized_scope == scope_digest
    assert result.snapshot_digest == capture.snapshot_digest
    assert payload["complete"] is True
    assert payload["truncated"] is False
    assert result.citations
    assert {citation.citation_id for citation in result.citations} <= {
        receipt.citation_id for receipt in receipts
    }
    assert len(isolated_directories) == 2
    assert all(not path.exists() for path in isolated_directories)
    assert not invocation.invocation_dir.exists()
    assert not command_canary.exists()
    assert artifact.read_bytes() == initial_content
    assert _git(repository, "rev-parse", "HEAD").strip() == initial_head
    assert initial_status == ""
    assert _git(repository, "status", "--porcelain=v2", "--untracked-files=all") == ""

    evidence = {
        "contract": "live-codex-evidence-reader-v1",
        "run_id": run_id,
        "role": _ROLE,
        "status": result.status.value,
        "artifact_path": "evidence.txt",
        "snapshot_digest": capture.snapshot_digest,
        "citation_receipts": len(receipts),
        "command_surface_observed": False,
        "repository_changed": False,
        "invocation_cleaned": True,
        "isolated_home_cleaned": True,
        "isolated_cwd_cleaned": True,
    }
    artifact_dir.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(
        json.dumps(evidence, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
