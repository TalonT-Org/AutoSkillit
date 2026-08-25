"""Shared helpers for factory make_context() tests.

Exposes the same private helper names as the pre-split ``test_factory.py``
so the new focused test files can import them under their original names
and keep every test body verbatim.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from autoskillit.core import RepositoryIdentity, RepositorySnapshot
from autoskillit.core.types import SubprocessResult, TerminationReason
from autoskillit.pipeline.exploration_context import OwnerBoundExplorationContextStore
from tests.fakes import MockSubprocessRunner


def _runner() -> MockSubprocessRunner:
    """Build a MockSubprocessRunner with the default success result pre-loaded."""
    r = MockSubprocessRunner()
    r.set_default(
        SubprocessResult(
            returncode=0,
            stdout="",
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=1,
        )
    )
    return r


def _install_shared_explorer_authority(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[Path, Path, Path]:
    """Install a verified exploration authority.

    Returns (repo_root, exec_cwd, authority_path).
    """
    repository_root = tmp_path / "repository"
    execution_cwd = tmp_path / "sterile-agent-cwd"
    authority_home = tmp_path / "authority-home"
    for path in (repository_root, execution_cwd, authority_home):
        path.mkdir()
    service = Mock()
    service.capture_snapshot.return_value = RepositorySnapshot(
        RepositoryIdentity("test-repository", "test-revision"),
        tree_digest="test-tree",
        collector_manifest_digest="test-manifest",
    )
    store: OwnerBoundExplorationContextStore[object] = OwnerBoundExplorationContextStore(
        trusted_root=repository_root,
        service=service,
    )
    bindings = store.bind_launches(
        owner_id="uid:1000",
        session_id="session-a",
        cwd=execution_cwd,
        repository_root=repository_root,
        source_identities={
            "semantic-code-navigator": "navigator-definition-a:parent-source",
            "repository-impact-profiler": "profiler-definition-a:parent-source",
        },
        authority_home=authority_home,
    )
    binding = bindings["semantic-code-navigator"]
    for key, value in binding.items():
        monkeypatch.setenv(key, value)
    return (
        repository_root.resolve(),
        execution_cwd.resolve(),
        Path(binding["AUTOSKILLIT_EXPLORATION_AUTHORITY_PATH"]),
    )
