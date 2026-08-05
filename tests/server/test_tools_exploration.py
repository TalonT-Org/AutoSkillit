"""Focused security tests for the typed exploration broker MCP tools."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from autoskillit.core import (
    CompletenessReport,
    EvidencePage,
    EvidenceRecord,
    MethodProvenance,
    NodeKey,
    RepositoryIdentity,
    RepositorySnapshot,
)
from autoskillit.pipeline import CapabilityResolutionStatus
from autoskillit.pipeline.exploration_context import OwnerBoundExplorationContextStore

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


class _Store:
    def __init__(self) -> None:
        self.submit_calls = 0
        self.get_status = CapabilityResolutionStatus.OK

    @staticmethod
    def _page() -> EvidencePage:
        return EvidencePage(
            evidence=(),
            result_digest="result-digest",
            completeness=CompletenessReport((), (), True),
        )

    def submit_from_launch_environment(
        self, **_kwargs: object
    ) -> tuple[CapabilityResolutionStatus, EvidencePage | None]:
        self.submit_calls += 1
        return CapabilityResolutionStatus.OK, self._page()

    def get_page_from_launch_environment(
        self, **_kwargs: object
    ) -> tuple[CapabilityResolutionStatus, EvidencePage | None]:
        return (
            self.get_status,
            self._page() if self.get_status is CapabilityResolutionStatus.OK else None,
        )


def _snapshot_service() -> MagicMock:
    service = MagicMock()
    service.capture_snapshot.side_effect = lambda root: RepositorySnapshot(
        RepositoryIdentity("test-repository", "test-revision", worktree_path=str(root.resolve())),
        tree_digest="test-tree",
        collector_manifest_digest="test-manifest",
    )
    return service


def test_fresh_launch_injects_only_server_issued_role_bindings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from autoskillit.server import _explorer_projection as explorer_projection

    roles = ("semantic-code-navigator", "repository-impact-profiler")
    definitions = tuple(SimpleNamespace(name=role) for role in roles)
    monkeypatch.setattr(explorer_projection, "load_agent_definitions", lambda _path: definitions)
    monkeypatch.setattr(
        explorer_projection,
        "agent_definition_digest",
        lambda definition: f"digest-{definition.name}",
    )
    project_dir = tmp_path / "project"
    store: OwnerBoundExplorationContextStore[object] = OwnerBoundExplorationContextStore(
        trusted_root=project_dir,
        service=_snapshot_service(),
    )
    tool_ctx = SimpleNamespace(exploration_context_store=store)
    projection_context = SimpleNamespace(
        backend=SimpleNamespace(
            capabilities=SimpleNamespace(terminal_explorer_capable=True),
            name="codex",
        ),
        cwd=project_dir / "worktree",
        parent_sandbox_mode="read-only",
    )
    authority_home = tmp_path / "generated-session"
    authority_home.mkdir()

    bindings = explorer_projection._issue_explorer_binding_env(
        tool_ctx,
        session_id="server-materialized-session",
        projection_context=projection_context,
        identity=(project_dir, "parent-source:trusted"),
        authority_home=authority_home,
    )

    assert bindings is not None
    assert set(bindings) == set(roles)
    assert bindings[roles[0]] == bindings[roles[1]]
    for binding in bindings.values():
        assert set(binding) == {
            "AUTOSKILLIT_EXPLORATION_CAPABILITY",
            "AUTOSKILLIT_EXPLORATION_ROLE",
            "AUTOSKILLIT_EXPLORATION_SESSION_ID",
            "AUTOSKILLIT_EXPLORATION_AUTHORITY_PATH",
        }
        assert binding["AUTOSKILLIT_EXPLORATION_ROLE"] == "shared-explorer-session"
        assert binding["AUTOSKILLIT_EXPLORATION_SESSION_ID"] == "server-materialized-session"
        assert binding["AUTOSKILLIT_EXPLORATION_CAPABILITY"].startswith("explore_")
        assert Path(binding["AUTOSKILLIT_EXPLORATION_AUTHORITY_PATH"]) == (
            authority_home / ".autoskillit-exploration-authority.json"
        )


def test_workspace_write_launch_does_not_issue_explorer_authority(tmp_path: Path) -> None:
    from autoskillit.server.tools import tools_execution

    store = MagicMock()
    tool_ctx = SimpleNamespace(exploration_context_store=store)
    project_dir = tmp_path / "project"
    projection_context = SimpleNamespace(
        backend=SimpleNamespace(
            capabilities=SimpleNamespace(terminal_explorer_capable=True),
            name="codex",
        ),
        cwd=project_dir,
        parent_sandbox_mode="workspace-write",
    )

    bindings = tools_execution._issue_explorer_binding_env(
        tool_ctx,
        session_id="ordinary-session",
        projection_context=projection_context,
        identity=(project_dir, "parent-source:trusted"),
        authority_home=tmp_path,
    )

    assert bindings is None
    store.bind_launches.assert_not_called()


def test_cleanup_revokes_authority_when_config_scrubbing_fails(tmp_path: Path) -> None:
    from autoskillit.server.tools import tools_execution

    project_dir = tmp_path / "project"
    authority_home = tmp_path / "generated-session"
    authority_home.mkdir()
    store: OwnerBoundExplorationContextStore[object] = OwnerBoundExplorationContextStore(
        trusted_root=project_dir,
        service=_snapshot_service(),
    )
    binding = store.bind_launch(
        owner_id="uid:1000",
        role="semantic-code-navigator",
        session_id="session-a",
        cwd=project_dir,
        repository_root=project_dir,
        source_identity="bundled:semantic-code-navigator:definition-digest",
        authority_home=authority_home,
    )

    class _FailingBackend:
        def clear_explorer_binding_env(self, _session_dir: Path, _roles: frozenset[str]) -> None:
            raise RuntimeError("simulated config scrub failure")

    tools_execution._cleanup_explorer_launch(
        store,
        session_id="session-a",
        session_home=authority_home,
        backend=_FailingBackend(),  # type: ignore[arg-type]
    )

    assert not binding.authority_path.exists()
    assert (
        store.resolve(
            capability=binding.capability,
            owner_id="uid:1000",
            role="semantic-code-navigator",
            session_id="session-a",
        ).status
        is CapabilityResolutionStatus.INVALID
    )


@pytest.mark.asyncio
async def test_submit_is_gated_before_any_store_access(monkeypatch: pytest.MonkeyPatch) -> None:
    from autoskillit.server.tools import tools_exploration

    store = _Store()
    monkeypatch.setattr(tools_exploration, "_get_store", lambda: store)
    monkeypatch.setattr(tools_exploration, "_require_enabled", lambda: '{"error":"gate"}')

    result = await tools_exploration.submit_exploration_query("needle")

    assert result == '{"error":"gate"}'
    assert store.submit_calls == 0


@pytest.mark.asyncio
async def test_page_uses_only_server_issued_launch_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.server.tools import tools_exploration

    store = _Store()
    monkeypatch.setattr(tools_exploration, "_get_store", lambda: store)
    monkeypatch.setattr(tools_exploration, "_require_enabled", lambda: None)

    result = await tools_exploration.get_exploration_page()

    assert json.loads(result)["status"] == "ready"


def test_page_payload_preserves_evidence_authority_fields() -> None:
    from autoskillit.server.tools import tools_exploration

    page = EvidencePage(
        evidence=(
            EvidenceRecord(
                "evidence-1",
                MethodProvenance.COLLECTOR,
                "snapshot-1",
                subject=NodeKey("repository-path", "src/module.py"),
                facts=("fact",),
                locator="src/module.py:7",
                method="stdlib-ast",
                extractor_version="collector-v2",
                searched_scope=("src",),
                location="src/module.py:7",
                query_uncertainty=("observational",),
            ),
        ),
        result_digest="result-digest",
        completeness=CompletenessReport((), (), True),
    )

    payload = json.loads(tools_exploration._page_payload(page, status="ready"))

    assert payload["evidence"] == [
        {
            "id": "evidence-1",
            "provenance": "collector",
            "snapshot_digest": "snapshot-1",
            "locator": "src/module.py:7",
            "method": "stdlib-ast",
            "extractor_version": "collector-v2",
            "searched_scope": ["src"],
            "location": "src/module.py:7",
            "query_uncertainty": ["observational"],
            "facts": ["fact"],
            "inferences": [],
            "unknowns": [],
            "conflicts": [],
        }
    ]


@pytest.mark.asyncio
async def test_stale_resume_never_reopens_a_capability(monkeypatch: pytest.MonkeyPatch) -> None:
    from autoskillit.server.tools import tools_exploration

    store = _Store()
    store.get_status = CapabilityResolutionStatus.EXPIRED
    monkeypatch.setattr(tools_exploration, "_get_store", lambda: store)
    monkeypatch.setattr(tools_exploration, "_require_enabled", lambda: None)

    result = await tools_exploration.resume_exploration_context()

    assert json.loads(result) == {"status": "error", "code": "exploration_context_unavailable"}
    assert store.submit_calls == 0


@pytest.mark.anyio
@pytest.mark.parametrize("refresh_fails", (False, True))
async def test_fresh_run_skill_revokes_explorer_authority_after_injection_outcome(
    tool_ctx_kitchen_open,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    refresh_fails: bool,
) -> None:
    """A freshly minted binding is terminally cleaned on success and refresh failure."""
    from autoskillit.execution.backends.codex import CodexBackend
    from autoskillit.server.tools import tools_execution
    from autoskillit.workspace import DefaultSessionSkillManager, SkillsDirectoryProvider
    from tests.fakes import InMemoryHeadlessExecutor

    cleanup_store: OwnerBoundExplorationContextStore[object] = OwnerBoundExplorationContextStore(
        trusted_root=tool_ctx_kitchen_open.project_dir,
        service=_snapshot_service(),
    )
    cleanup_session = MagicMock(wraps=cleanup_store.cleanup_session)
    monkeypatch.setattr(cleanup_store, "cleanup_session", cleanup_session)
    authority_paths: list[Path] = []
    bind_launches = cleanup_store.bind_launches

    def _capture_bound_authority(**kwargs: object) -> dict[str, dict[str, str]]:
        bindings = bind_launches(**kwargs)  # type: ignore[arg-type]
        authority_paths.append(
            Path(next(iter(bindings.values()))["AUTOSKILLIT_EXPLORATION_AUTHORITY_PATH"])
        )
        return bindings

    monkeypatch.setattr(cleanup_store, "bind_launches", _capture_bound_authority)
    concrete_backend = CodexBackend()
    backend = MagicMock(wraps=concrete_backend)
    backend.name = concrete_backend.name
    backend.conventions = concrete_backend.conventions
    backend.capabilities = replace(
        concrete_backend.capabilities,
        terminal_explorer_capable=True,
    )
    if refresh_fails:
        backend.refresh_explorer_binding_env.side_effect = RuntimeError("injection failed")
    else:
        backend.refresh_explorer_binding_env.return_value = None

    tool_ctx_kitchen_open.backend = backend
    tool_ctx_kitchen_open.session_skill_manager = DefaultSessionSkillManager(
        SkillsDirectoryProvider(),
        ephemeral_root=tmp_path / "ephemeral-sessions",
        persistent_roots={"codex": tmp_path / "persistent-sessions"},
    )
    tool_ctx_kitchen_open.read_only_resolver = lambda _command: True
    tool_ctx_kitchen_open.executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.exploration_context_store = cleanup_store
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)
    monkeypatch.setattr(
        tool_ctx_kitchen_open.launch_resolver,
        "backend_for_authority",
        lambda _authority: backend,
    )
    monkeypatch.setattr(
        tools_execution,
        "_explorer_launch_identity",
        lambda _invocation: (tool_ctx_kitchen_open.project_dir, "bundled:test"),
    )

    result = json.loads(await tools_execution.run_skill("/test skill", "/tmp"))

    assert result["success"] is (not refresh_fails), result["result"]
    assert backend.refresh_explorer_binding_env.call_count == 1
    cleanup_session.assert_called_once()
    backend.clear_explorer_binding_env.assert_called_once()
    assert len(authority_paths) == 1
    assert not authority_paths[0].exists()


@pytest.mark.anyio
async def test_resumed_run_skill_revokes_replacement_authority_after_refresh_failure(
    tool_ctx_kitchen_open,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Resume refresh failure revokes the replacement authority before returning."""
    from autoskillit.execution.backends.codex import CodexBackend
    from autoskillit.server.tools import tools_execution
    from autoskillit.workspace import DefaultSessionSkillManager, SkillsDirectoryProvider
    from tests.conftest import bind_test_skill_resume_contract
    from tests.fakes import InMemoryHeadlessExecutor

    concrete_backend = CodexBackend()
    backend = MagicMock(wraps=concrete_backend)
    backend.name = concrete_backend.name
    backend.conventions = concrete_backend.conventions
    backend.capabilities = replace(
        concrete_backend.capabilities,
        terminal_explorer_capable=True,
    )
    backend.refresh_explorer_binding_env.side_effect = RuntimeError("replacement injection failed")
    tool_ctx_kitchen_open.backend = backend
    tool_ctx_kitchen_open.session_skill_manager = DefaultSessionSkillManager(
        SkillsDirectoryProvider(),
        ephemeral_root=tmp_path / "ephemeral-sessions",
        persistent_roots={"codex": tmp_path / "persistent-sessions"},
    )
    bind_test_skill_resume_contract(
        tool_ctx_kitchen_open,
        session_id="explorer-resume",
        cwd="/tmp",
        skill_name="test",
        resolved_command="/test skill",
        read_only=True,
    )
    cleanup_store: OwnerBoundExplorationContextStore[object] = OwnerBoundExplorationContextStore(
        trusted_root=tool_ctx_kitchen_open.project_dir,
        service=_snapshot_service(),
    )
    cleanup_session = MagicMock(wraps=cleanup_store.cleanup_session)
    monkeypatch.setattr(cleanup_store, "cleanup_session", cleanup_session)
    tool_ctx_kitchen_open.exploration_context_store = cleanup_store
    tool_ctx_kitchen_open.executor = InMemoryHeadlessExecutor()
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)
    monkeypatch.setattr(
        tool_ctx_kitchen_open.launch_resolver,
        "backend_for_authority",
        lambda _authority: backend,
    )
    monkeypatch.setattr(
        tools_execution,
        "_explorer_launch_identity",
        lambda _invocation: (tool_ctx_kitchen_open.project_dir, "bundled:test"),
    )

    result = json.loads(
        await tools_execution.run_skill(
            "continue",
            "/tmp",
            resume_session_id="explorer-resume",
        )
    )

    assert result["success"] is False
    assert backend.refresh_explorer_binding_env.call_count == 1
    cleanup_session.assert_called_once_with("explorer-resume")
    backend.clear_explorer_binding_env.assert_called_once()
