"""Focused security tests for the typed exploration broker MCP tools."""

from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from autoskillit.core import (
    CompletenessReport,
    ContinuationCursor,
    EvidencePage,
    EvidenceRecord,
    ExplorationQuerySpec,
    MethodProvenance,
    NodeKey,
    RepositoryIdentity,
    RepositorySnapshot,
)
from autoskillit.hooks._exploration_request_record import write_exploration_request_record
from autoskillit.pipeline import CapabilityResolutionStatus
from autoskillit.pipeline.exploration_context import OwnerBoundExplorationContextStore

if TYPE_CHECKING:
    from autoskillit.core import CodingAgentBackend
    from autoskillit.pipeline import ToolContext
    from autoskillit.workspace import DefaultSessionSkillManager
    from tests.fakes import InMemoryHeadlessExecutor

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


class _Store:
    def __init__(self) -> None:
        self.submit_calls = 0
        self.submitted_query: ExplorationQuerySpec | None = None
        self.submitted_page_size: int | None = None
        self.get_status = CapabilityResolutionStatus.OK

    @staticmethod
    def _page() -> EvidencePage:
        return EvidencePage(
            evidence=(),
            result_digest="result-digest",
            completeness=CompletenessReport((), (), True),
        )

    def submit_from_launch_environment(
        self,
        *,
        query: ExplorationQuerySpec,
        page_size: int,
    ) -> tuple[CapabilityResolutionStatus, EvidencePage | None]:
        self.submit_calls += 1
        self.submitted_query = query
        self.submitted_page_size = page_size
        return CapabilityResolutionStatus.OK, self._page()

    def get_page_from_launch_environment(
        self, **_kwargs: object
    ) -> tuple[CapabilityResolutionStatus, EvidencePage | None]:
        return (
            self.get_status,
            self._page() if self.get_status is CapabilityResolutionStatus.OK else None,
        )


class _RecordingPageStore:
    def __init__(self, issued_page: EvidencePage) -> None:
        self.issued_page = issued_page
        self.calls: list[tuple[int, ContinuationCursor | None]] = []

    def get_page_from_launch_environment(
        self,
        *,
        page_size: int,
        cursor: ContinuationCursor | None = None,
    ) -> tuple[CapabilityResolutionStatus, EvidencePage]:
        self.calls.append((page_size, cursor))
        return CapabilityResolutionStatus.OK, self.issued_page


@dataclass(frozen=True, slots=True)
class _ExplorerRunSkillScaffold:
    tool_ctx: ToolContext
    backend: CodingAgentBackend
    manager: DefaultSessionSkillManager
    executor: InMemoryHeadlessExecutor


def _explorer_run_skill_scaffold(
    tool_ctx: ToolContext,
    tmp_path: Path,
    *,
    backend: CodingAgentBackend | None = None,
) -> _ExplorerRunSkillScaffold:
    from autoskillit.execution.backends.codex import CodexBackend
    from autoskillit.workspace import DefaultSessionSkillManager, SkillsDirectoryProvider
    from tests.fakes import InMemoryHeadlessExecutor

    resolved_backend = backend or CodexBackend(source_codex_home=tmp_path / "source-codex-home")
    manager = DefaultSessionSkillManager(
        SkillsDirectoryProvider(),
        ephemeral_root=tmp_path / "ephemeral-sessions",
        persistent_roots={"codex": tmp_path / "persistent-sessions"},
    )
    executor = InMemoryHeadlessExecutor()
    tool_ctx.backend = resolved_backend
    tool_ctx.session_skill_manager = manager
    tool_ctx.read_only_resolver = lambda _command: True
    tool_ctx.executor = executor
    return _ExplorerRunSkillScaffold(tool_ctx, resolved_backend, manager, executor)


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
    monkeypatch.setattr(explorer_projection, "load_bundled_agent_definitions", lambda: definitions)
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
            capabilities=SimpleNamespace(
                terminal_explorer_capable=True, session_scoped_explorer_capable=False
            ),
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
            capabilities=SimpleNamespace(
                terminal_explorer_capable=True, session_scoped_explorer_capable=False
            ),
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
async def test_submit_preserves_default_query_and_page_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.server.tools import tools_exploration

    store = _Store()
    monkeypatch.setattr(tools_exploration, "_get_store", lambda: store)
    monkeypatch.setattr(tools_exploration, "_require_enabled", lambda: None)

    result = await tools_exploration.submit_exploration_query("needle")

    assert json.loads(result)["status"] == "accepted"
    assert store.submitted_query is not None
    assert store.submitted_query.max_results == 100
    assert store.submitted_page_size == 100


_SIBLING_TOOL_NAMES = (
    "submit_exploration_query",
    "get_exploration_page",
    "resume_exploration_context",
)


async def _call_sibling_tool(tools_exploration: object, tool_name: str) -> str:
    if tool_name == "submit_exploration_query":
        return await tools_exploration.submit_exploration_query("needle")  # type: ignore[attr-defined]
    if tool_name == "get_exploration_page":
        return await tools_exploration.get_exploration_page()  # type: ignore[attr-defined]
    return await tools_exploration.resume_exploration_context()  # type: ignore[attr-defined]


@pytest.mark.parametrize("tool_name", _SIBLING_TOOL_NAMES)
@pytest.mark.asyncio
async def test_sibling_tool_returns_broker_unavailable_when_store_is_none(
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str,
) -> None:
    """No store configured is a named, distinguishable code — not the opaque
    catch-all it collapsed into before #4684's REQ-017 remediation."""
    from autoskillit.core import ExplorationFailureCode
    from autoskillit.server.tools import tools_exploration

    monkeypatch.setattr(tools_exploration, "_get_store", lambda: None)
    monkeypatch.setattr(tools_exploration, "_require_enabled", lambda: None)

    result = await _call_sibling_tool(tools_exploration, tool_name)

    assert json.loads(result) == {
        "status": "error",
        "code": ExplorationFailureCode.BROKER_UNAVAILABLE.value,
    }


@pytest.mark.parametrize("tool_name", _SIBLING_TOOL_NAMES)
@pytest.mark.asyncio
async def test_sibling_tool_returns_unexpected_internal_error_for_unnamed_failures(
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str,
) -> None:
    """A genuinely unclassified exception gets its own distinguishable code —
    never silently reused for the BROKER_UNAVAILABLE case above, and never
    re-raised (preserves the "Never raises" contract)."""
    from autoskillit.core import ExplorationFailureCode
    from autoskillit.server.tools import tools_exploration

    def _boom() -> None:
        raise ValueError("unrelated internal failure")

    monkeypatch.setattr(tools_exploration, "_get_store", _boom)
    monkeypatch.setattr(tools_exploration, "_require_enabled", lambda: None)

    result = await _call_sibling_tool(tools_exploration, tool_name)

    assert json.loads(result) == {
        "status": "error",
        "code": ExplorationFailureCode.UNEXPECTED_INTERNAL_ERROR.value,
    }


@pytest.mark.parametrize("tool_name", _SIBLING_TOOL_NAMES)
@pytest.mark.asyncio
async def test_terminal_authority_returns_before_request_identity_consumption(
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str,
) -> None:
    from autoskillit.server.tools import tools_exploration

    store = _Store()
    consumer = MagicMock(side_effect=AssertionError("request token must not be consumed"))
    monkeypatch.setattr(tools_exploration, "_get_store", lambda: store)
    monkeypatch.setattr(tools_exploration, "_require_enabled", lambda: None)
    monkeypatch.setattr(tools_exploration, "consume_exploration_request_record", consumer)

    if tool_name == "submit_exploration_query":
        result = await tools_exploration.submit_exploration_query("needle")
    elif tool_name == "get_exploration_page":
        result = await tools_exploration.get_exploration_page()
    else:
        result = await tools_exploration.resume_exploration_context()

    assert json.loads(result)["status"] in {"accepted", "ready", "resumed"}
    consumer.assert_not_called()


@pytest.mark.parametrize(
    "tool_name",
    [
        "submit_exploration_query",
        "get_exploration_page",
        "resume_exploration_context",
    ],
)
@pytest.mark.asyncio
async def test_session_fallbacks_consume_exact_native_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    tool_name: str,
) -> None:
    from autoskillit.server.tools import tools_exploration

    (tmp_path / ".autoskillit" / "temp").mkdir(parents=True)
    store: OwnerBoundExplorationContextStore[object] = OwnerBoundExplorationContextStore(
        trusted_root=tmp_path,
        service=_snapshot_service(),
    )
    capability = store.bind_session_scoped(
        owner_id=f"uid:{os.getuid()}",
        session_id="native-session-a",
        cwd=tmp_path,
        repository_root=tmp_path,
        source_identity="interactive:native-session-a",
    )
    page = _Store._page()
    monkeypatch.setattr(
        store,
        "submit_from_launch_environment",
        MagicMock(return_value=(CapabilityResolutionStatus.INVALID, None)),
    )
    monkeypatch.setattr(
        store,
        "get_page_from_launch_environment",
        MagicMock(return_value=(CapabilityResolutionStatus.INVALID, None)),
    )
    monkeypatch.setattr(
        store,
        "submit_for_capability",
        MagicMock(return_value=(capability, page)),
    )
    monkeypatch.setattr(
        store,
        "get_page_for_capability",
        MagicMock(return_value=(CapabilityResolutionStatus.OK, page)),
    )
    lookup = MagicMock(wraps=store.session_scoped_capability)
    monkeypatch.setattr(store, "session_scoped_capability", lookup)
    monkeypatch.setattr(tools_exploration, "_get_store", lambda: store)
    monkeypatch.setattr(tools_exploration, "_require_enabled", lambda: None)
    monkeypatch.setattr(
        "autoskillit.server._get_ctx",
        lambda: SimpleNamespace(project_dir=tmp_path),
    )
    token = write_exploration_request_record(tmp_path, tool_name, "native-session-a")

    if tool_name == "submit_exploration_query":
        result = await tools_exploration.submit_exploration_query(
            "needle", _autoskillit_exploration_request_token=token
        )
    elif tool_name == "get_exploration_page":
        result = await tools_exploration.get_exploration_page(
            _autoskillit_exploration_request_token=token
        )
    else:
        result = await tools_exploration.resume_exploration_context(
            _autoskillit_exploration_request_token=token
        )

    assert json.loads(result)["status"] in {"accepted", "ready", "resumed"}
    lookup.assert_called_once_with("native-session-a")


@pytest.mark.asyncio
async def test_second_native_session_cannot_retrieve_first_session_lease(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from autoskillit.server.tools import tools_exploration

    (tmp_path / ".autoskillit" / "temp").mkdir(parents=True)
    store: OwnerBoundExplorationContextStore[object] = OwnerBoundExplorationContextStore(
        trusted_root=tmp_path,
        service=_snapshot_service(),
    )
    store.bind_session_scoped(
        owner_id=f"uid:{os.getuid()}",
        session_id="native-session-a",
        cwd=tmp_path,
        repository_root=tmp_path,
        source_identity="interactive:native-session-a",
    )
    monkeypatch.setattr(
        store,
        "get_page_from_launch_environment",
        MagicMock(return_value=(CapabilityResolutionStatus.INVALID, None)),
    )
    lookup = MagicMock(wraps=store.session_scoped_capability)
    monkeypatch.setattr(store, "session_scoped_capability", lookup)
    monkeypatch.setattr(tools_exploration, "_get_store", lambda: store)
    monkeypatch.setattr(tools_exploration, "_require_enabled", lambda: None)
    monkeypatch.setattr(
        "autoskillit.server._get_ctx",
        lambda: SimpleNamespace(project_dir=tmp_path),
    )
    token = write_exploration_request_record(tmp_path, "get_exploration_page", "native-session-b")

    result = await tools_exploration.get_exploration_page(
        _autoskillit_exploration_request_token=token
    )

    assert json.loads(result) == {
        "status": "error",
        "code": "exploration_context_unavailable",
    }
    lookup.assert_called_once_with("native-session-b")


@pytest.mark.asyncio
async def test_submit_keeps_query_and_response_page_ceilings_independent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.server.tools import tools_exploration

    store = _Store()
    monkeypatch.setattr(tools_exploration, "_MAX_QUERY_RESULTS", 5)
    monkeypatch.setattr(tools_exploration, "_MAX_RESPONSE_PAGE_SIZE", 3)
    monkeypatch.setattr(tools_exploration, "_get_store", lambda: store)
    monkeypatch.setattr(tools_exploration, "_require_enabled", lambda: None)

    result = await tools_exploration.submit_exploration_query("needle", max_results=9)

    assert json.loads(result)["status"] == "accepted"
    assert store.submitted_query is not None
    assert store.submitted_query.max_results == 5
    assert store.submitted_page_size == 3


@pytest.mark.parametrize(
    "max_results",
    [
        pytest.param(True, id="bool-true"),
        pytest.param(False, id="bool-false"),
        pytest.param(0, id="zero"),
        pytest.param(-1, id="negative"),
    ],
)
@pytest.mark.asyncio
async def test_submit_rejects_invalid_max_results(
    monkeypatch: pytest.MonkeyPatch,
    max_results: int,
) -> None:
    from autoskillit.server.tools import tools_exploration

    store = _Store()
    monkeypatch.setattr(tools_exploration, "_get_store", lambda: store)
    monkeypatch.setattr(tools_exploration, "_require_enabled", lambda: None)

    result = await tools_exploration.submit_exploration_query(
        "needle",
        max_results=max_results,
    )

    assert json.loads(result) == {
        "status": "error",
        "code": "invalid_exploration_request",
    }
    assert store.submit_calls == 0


@pytest.mark.asyncio
async def test_page_uses_only_server_issued_launch_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.server.tools import tools_exploration

    issued_page = EvidencePage(
        evidence=(),
        result_digest="server-issued-result",
        completeness=CompletenessReport((), (), True),
    )
    store = _RecordingPageStore(issued_page)
    cursor = ContinuationCursor(
        result_digest="prior-result",
        offset=7,
        page_size=7,
        authority_digest="server-authority",
    )
    monkeypatch.setattr(tools_exploration, "_get_store", lambda: store)
    monkeypatch.setattr(tools_exploration, "_require_enabled", lambda: None)

    result = await tools_exploration.get_exploration_page(
        page_size=7,
        continuation=cursor.encode(),
    )

    assert store.calls == [(7, cursor)]
    assert result == tools_exploration._page_payload(issued_page, status="ready")


@pytest.mark.parametrize("page_size", [0, -1, 101])
@pytest.mark.asyncio
async def test_page_rejects_sizes_outside_current_wire_bounds(
    monkeypatch: pytest.MonkeyPatch,
    page_size: int,
) -> None:
    from autoskillit.server.tools import tools_exploration

    store = _RecordingPageStore(_Store._page())
    monkeypatch.setattr(tools_exploration, "_get_store", lambda: store)
    monkeypatch.setattr(tools_exploration, "_require_enabled", lambda: None)

    result = await tools_exploration.get_exploration_page(page_size=page_size)

    assert json.loads(result) == {
        "status": "error",
        "code": "invalid_exploration_request",
    }
    assert store.calls == []


@pytest.mark.asyncio
async def test_response_page_ceiling_controls_slicing_and_retrieval_independently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.server.tools import tools_exploration

    page = EvidencePage(
        evidence=tuple(
            EvidenceRecord(
                f"evidence-{index}",
                MethodProvenance.COLLECTOR,
                "snapshot-1",
                subject=NodeKey("repository-path", f"src/module_{index}.py"),
            )
            for index in range(3)
        ),
        result_digest="result-digest",
        completeness=CompletenessReport((), (), True),
    )
    store = _RecordingPageStore(page)
    monkeypatch.setattr(tools_exploration, "_MAX_QUERY_RESULTS", 1)
    monkeypatch.setattr(tools_exploration, "_MAX_RESPONSE_PAGE_SIZE", 2)
    monkeypatch.setattr(tools_exploration, "_get_store", lambda: store)
    monkeypatch.setattr(tools_exploration, "_require_enabled", lambda: None)

    accepted = await tools_exploration.get_exploration_page(page_size=2)
    rejected = await tools_exploration.get_exploration_page(page_size=3)

    assert len(json.loads(accepted)["evidence"]) == 2
    assert json.loads(rejected)["code"] == "invalid_exploration_request"
    assert store.calls == [(2, None)]


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
async def test_fresh_run_skill_projects_real_codex_binding_before_execution_and_cleans(
    tool_ctx_kitchen_open,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The real fresh Codex path projects authority during setup, then scrubs it."""
    from autoskillit.core import SkillResult
    from autoskillit.server.tools import tools_execution

    events: list[str] = []
    cleanup_store: OwnerBoundExplorationContextStore[object] = OwnerBoundExplorationContextStore(
        trusted_root=tool_ctx_kitchen_open.project_dir,
        service=_snapshot_service(),
    )
    original_store_cleanup = cleanup_store.cleanup_session

    def _capture_store_cleanup(session_id: str) -> None:
        events.append("store-cleanup")
        original_store_cleanup(session_id)

    monkeypatch.setattr(cleanup_store, "cleanup_session", _capture_store_cleanup)
    authority_paths: list[Path] = []
    issued_bindings: dict[str, dict[str, str]] = {}
    bind_launches = cleanup_store.bind_launches

    def _capture_bound_authority(**kwargs: object) -> dict[str, dict[str, str]]:
        events.append("bind")
        bindings = bind_launches(**kwargs)  # type: ignore[arg-type]
        issued_bindings.update(bindings)
        authority_paths.append(
            Path(next(iter(bindings.values()))["AUTOSKILLIT_EXPLORATION_AUTHORITY_PATH"])
        )
        return bindings

    monkeypatch.setattr(cleanup_store, "bind_launches", _capture_bound_authority)
    scaffold = _explorer_run_skill_scaffold(tool_ctx_kitchen_open, tmp_path)
    backend = scaffold.backend
    manager = scaffold.manager
    executor = scaffold.executor
    original_run = executor.run

    async def _inspect_projected_launch(*args: object, **kwargs: object) -> SkillResult:
        add_dirs = kwargs["add_dirs"]
        assert isinstance(add_dirs, list)
        session_home = Path(add_dirs[0].path).parent
        shared_binding = next(iter(issued_bindings.values()))
        parent = tomllib.loads((session_home / "config.toml").read_text(encoding="utf-8"))
        assert parent["mcp_servers"]["autoskillit"]["env"] == shared_binding
        for role in issued_bindings:
            role_config = tomllib.loads(
                (session_home / "agents" / f"{role}.toml").read_text(encoding="utf-8")
            )
            assert role_config["mcp_servers"]["autoskillit"]["env"] == shared_binding
        assert authority_paths[0].is_file()
        events.append("execute")
        return await original_run(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(executor, "run", _inspect_projected_launch)
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
    original_manager_cleanup = manager.cleanup_session

    def _inspect_scrubbed_session(session_id: str) -> bool:
        session_home = manager._session_roots[session_id] / session_id
        parent = tomllib.loads((session_home / "config.toml").read_text(encoding="utf-8"))
        assert "env" not in parent["mcp_servers"]["autoskillit"]
        for role in issued_bindings:
            role_config = tomllib.loads(
                (session_home / "agents" / f"{role}.toml").read_text(encoding="utf-8")
            )
            assert "env" not in role_config["mcp_servers"]["autoskillit"]
        events.append("manager-cleanup")
        return original_manager_cleanup(session_id)

    monkeypatch.setattr(manager, "cleanup_session", _inspect_scrubbed_session)

    result = json.loads(await tools_execution.run_skill("/test skill", str(tmp_path)))

    assert result["success"] is True, result["result"]
    assert events == ["bind", "execute", "store-cleanup", "manager-cleanup"]
    assert len(authority_paths) == 1
    assert not authority_paths[0].exists()


@pytest.mark.anyio
async def test_fresh_run_skill_revokes_explorer_authority_after_codex_setup_failure(
    tool_ctx_kitchen_open,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Cleanup ownership transfers before fresh Codex setup can fail."""
    from autoskillit.execution.backends.codex import CodexBackend
    from autoskillit.server.tools import tools_execution

    events: list[str] = []
    cleanup_store: OwnerBoundExplorationContextStore[object] = OwnerBoundExplorationContextStore(
        trusted_root=tool_ctx_kitchen_open.project_dir,
        service=_snapshot_service(),
    )
    authority_paths: list[Path] = []
    bind_launches = cleanup_store.bind_launches

    def _capture_bound_authority(**kwargs: object) -> dict[str, dict[str, str]]:
        events.append("bind")
        bindings = bind_launches(**kwargs)  # type: ignore[arg-type]
        authority_paths.append(
            Path(next(iter(bindings.values()))["AUTOSKILLIT_EXPLORATION_AUTHORITY_PATH"])
        )
        return bindings

    original_store_cleanup = cleanup_store.cleanup_session

    def _capture_store_cleanup(session_id: str) -> None:
        events.append("store-cleanup")
        original_store_cleanup(session_id)

    monkeypatch.setattr(cleanup_store, "bind_launches", _capture_bound_authority)
    monkeypatch.setattr(cleanup_store, "cleanup_session", _capture_store_cleanup)
    concrete_backend = CodexBackend(source_codex_home=tmp_path / "source-codex-home")
    backend = MagicMock(wraps=concrete_backend)
    backend.name = concrete_backend.name
    backend.conventions = concrete_backend.conventions
    backend.capabilities = concrete_backend.capabilities
    _explorer_run_skill_scaffold(tool_ctx_kitchen_open, tmp_path, backend=backend)

    def _fail_setup(_session_dir: Path, **kwargs: object) -> None:
        assert kwargs["explorer_binding_env"]
        events.append("setup")
        raise RuntimeError("injection failed")

    backend.setup_session_dir.side_effect = _fail_setup
    backend.clear_explorer_binding_env.return_value = None
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

    result = json.loads(await tools_execution.run_skill("/test skill", str(tmp_path)))

    assert result["success"] is False
    assert events == ["bind", "setup", "store-cleanup"]
    backend.refresh_explorer_binding_env.assert_not_called()
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
    from tests.conftest import bind_test_skill_resume_contract

    concrete_backend = CodexBackend()
    backend = MagicMock(wraps=concrete_backend)
    backend.name = concrete_backend.name
    backend.conventions = concrete_backend.conventions
    backend.capabilities = replace(
        concrete_backend.capabilities,
        terminal_explorer_capable=True,
    )
    backend.refresh_explorer_binding_env.side_effect = RuntimeError("replacement injection failed")
    _explorer_run_skill_scaffold(tool_ctx_kitchen_open, tmp_path, backend=backend)
    bind_test_skill_resume_contract(
        tool_ctx_kitchen_open,
        session_id="explorer-resume",
        cwd=str(tmp_path),
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
            str(tmp_path),
            resume_session_id="explorer-resume",
        )
    )

    assert result["success"] is False
    assert backend.refresh_explorer_binding_env.call_count == 1
    cleanup_session.assert_called_once_with("explorer-resume")
    backend.clear_explorer_binding_env.assert_called_once()
