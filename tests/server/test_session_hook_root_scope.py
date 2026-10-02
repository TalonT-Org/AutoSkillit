"""Server owners of the per-session Codex hook root lease (I3).

Covers the run_skill fresh/restore dispatch path, the fixed-batch leaf
``prepare`` closure, and direct skill dispatch: each acquires a
``SessionHookRoot`` through ``session_hook_root_scope`` and threads it into
the ``SkillProjectionContext`` handed to materialization, and releases the
underlying lease no later than the owning resource's cleanup.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from autoskillit.core import (
    ManagedWorkerPermit,
    SessionHookRoot,
    SkillContractError,
    SkillExecutionRole,
    SkillSemanticAdaptationResult,
    SkillSource,
    SkillSourceIdentity,
    ValidatedAddDir,
    WriteBehaviorSpec,
)
from autoskillit.execution.backends import ClaudeCodeBackend, CodexBackend
from autoskillit.hooks._session_binding import LoadedSkillEntry, LoadedSkillOrigin
from autoskillit.pipeline import ToolContext
from autoskillit.server.tools._backend_compat import _prepare_direct_skill_dispatch
from autoskillit.server.tools.tools_execution._fixed_batch_handlers import (
    _ManagedLeafLaunchAdapter,
)
from autoskillit.server.tools.tools_execution._managed_fixed_batch import ManagedLaunchBinding
from autoskillit.server.tools.tools_execution._managed_leaf import (
    ManagedLeafAssignmentInput,
    ManagedLeafBinding,
    ManagedLeafProjection,
    classify_managed_leaf_workspace,
    plan_managed_leaf_identities,
)
from autoskillit.server.tools.tools_execution._run_skill_session import (
    _prepare_owned_dispatch_session,
)
from autoskillit.server.tools.tools_execution._state import _RunSkillDispatchState
from autoskillit.workspace import AgentSkillDocument, SkillProjectionContext
from autoskillit.workspace.skills import EffectiveSkillInvocation, SkillInfo
from tests.fakes import FakePluginArtifactAuthority, make_managed_codex_context

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium]

_SYNTHETIC_MODEL_ID = "gpt-synthetic"


def _build_state(
    tool_ctx: Any,
    *,
    invocation: Any,
    projection_context: SkillProjectionContext,
    stored_contract_entry: Any = None,
) -> _RunSkillDispatchState:
    return _RunSkillDispatchState(
        skill_command="/test-skill",
        cwd=str(tool_ctx.project_dir),
        order_id="",
        step_name="",
        model="",
        recipe_execution_id="",
        invocation_template_digest="",
        step_provider="",
        stale_threshold=None,
        idle_output_timeout=None,
        output_dir="",
        resume_session_id="",
        retry_after_audit_attempt_id="",
        native_shell_capture_mode="",
        closure_authority_path="",
        closure_authority_hash="",
        closure_plan_paths="",
        closure_base_sha="",
        closure_diff_sha="",
        closure_target_sha="",
        step_guard_value=None,
        skill_inputs=None,
        tool_ctx=tool_ctx,
        ctx=cast(Any, None),
        invocation=invocation,
        projection_context=projection_context,
        _stored_contract_entry=stored_contract_entry,
        _effective_backend_obj=CodexBackend(),
        _cleanup_session_id="run-skill-session-1",
        resolved_command="/test-skill",
        expected_output_patterns=[],
        _contract_store=cast(Any, SimpleNamespace()),
        _cfg=cast(Any, SimpleNamespace()),
    )


class _RecordingSessionManager:
    """Records the exact projection context each materialization call receives."""

    def __init__(self) -> None:
        self.materialize_calls: list[SkillProjectionContext] = []
        self.restore_calls: list[SkillProjectionContext] = []

    def materialize_invocation(
        self,
        session_id: str,
        invocation: Any,
        projection_context: SkillProjectionContext,
        **_: Any,
    ) -> ValidatedAddDir:
        self.materialize_calls.append(projection_context)
        return ValidatedAddDir(path="/dev/shm/fake-fresh-session", session_home="/dev/shm/fake")

    def validate_session_exists(self, session_id: str) -> bool:
        return True

    def restore_snapshot_session(
        self, session_id: str, snapshot_dir: Path, projection_context: SkillProjectionContext
    ) -> ValidatedAddDir:
        self.restore_calls.append(projection_context)
        return ValidatedAddDir(path="/dev/shm/fake-restored", session_home="/dev/shm/fake")


@pytest.mark.anyio
async def test_run_skill_fresh_materialization_receives_session_hook_root(tmp_path: Path) -> None:
    manager = _RecordingSessionManager()
    tool_ctx = SimpleNamespace(
        session_skill_manager=manager,
        runner=SimpleNamespace(),
        ephemeral_root=None,
        project_dir=tmp_path,
    )
    invocation = SimpleNamespace(
        root=SimpleNamespace(name="test-skill"),
        semantic_plans=(),
        closure=(),
        project_root=None,
    )
    initial_projection = SkillProjectionContext(cwd=tmp_path, invocation=invocation)
    state = _build_state(tool_ctx, invocation=invocation, projection_context=initial_projection)
    hook_root = SessionHookRoot(
        artifact_path=tmp_path / "plugin-root",
        plugin_dir=tmp_path / "plugin-root",
        semantic_key="fresh-key",
    )

    # Later phases (contract building, native-shell lineage) need machinery this
    # focused test does not model; only the materialization call is asserted.
    try:
        await _prepare_owned_dispatch_session(state, tmp_path, hook_root)
    except Exception:
        pass

    assert manager.materialize_calls
    assert manager.materialize_calls[0].session_hook_root is hook_root


@pytest.mark.anyio
async def test_run_skill_restore_materialization_receives_session_hook_root(
    tmp_path: Path,
) -> None:
    manager = _RecordingSessionManager()
    tool_ctx = SimpleNamespace(
        session_skill_manager=manager,
        runner=SimpleNamespace(),
        ephemeral_root=None,
        project_dir=tmp_path,
    )
    initial_projection = SkillProjectionContext(
        cwd=tmp_path,
        invocation=SimpleNamespace(
            root=SimpleNamespace(name="test-skill"),
            semantic_plans=(),
            closure=(),
            project_root=None,
        ),
    )
    stored_entry = SimpleNamespace(snapshot_dir=tmp_path / "snapshot")
    state = _build_state(
        tool_ctx,
        invocation=None,
        projection_context=initial_projection,
        stored_contract_entry=stored_entry,
    )
    hook_root = SessionHookRoot(
        artifact_path=tmp_path / "plugin-root",
        plugin_dir=tmp_path / "plugin-root",
        semantic_key="restore-key",
    )

    try:
        await _prepare_owned_dispatch_session(state, tmp_path, hook_root)
    except Exception:
        pass

    assert manager.restore_calls
    assert manager.restore_calls[0].session_hook_root is hook_root


class _RecordingFixedBatchManager:
    """Records the leaf projection context and satisfies the SKILL.md presence check."""

    def __init__(self, home: Path, source_name: str, conventions: Any) -> None:
        self._home = home
        self._source_name = source_name
        self._conventions = conventions
        self.materialize_calls: list[SkillProjectionContext] = []

    def materialize_invocation(
        self,
        session_id: str,
        invocation: Any,
        projection_context: SkillProjectionContext,
        **_: Any,
    ) -> ValidatedAddDir:
        self.materialize_calls.append(projection_context)
        skill_dir = self._home / self._conventions.skills_subdir / self._source_name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text("stub", encoding="utf-8")
        return ValidatedAddDir(
            path=str(self._home),
            session_home=str(self._home),
            skill_entries=((self._source_name, str(skill_dir)),),
        )

    def cleanup_session(self, session_id: str) -> None:
        return None


def _build_leaf_adapter(
    tmp_path: Path,
    *,
    backend: Any,
    plugin_authority: FakePluginArtifactAuthority,
) -> tuple[
    _ManagedLeafLaunchAdapter,
    ManagedLeafProjection,
    ManagedWorkerPermit,
    _RecordingFixedBatchManager,
]:
    # Isolates the session-binding state root under tmp_path: resolve_state_root
    # finds .autoskillit here first, instead of walking up to the real cwd.
    (tmp_path / ".autoskillit").mkdir(exist_ok=True)
    parent_id = "managed-parent-hook-root"
    assignment = plan_managed_leaf_identities(
        "session-hook-root-fixed-batch-request",
        (ManagedLeafAssignmentInput("reviewer", "review", "Inspect the change."),),
    ).assignments[0]
    selected_source = LoadedSkillEntry(
        skill_name="review-skill",
        ts="2026-09-28T00:00:00Z",
        join_required=True,
        child_spawn_cardinality={"reviewer": 1},
        semantic_digest="semantic-source",
        adaptation_digest="adaptation-source",
        projected_digest="projected-source",
        canonical_digest="canonical-source",
        source_artifact_digest="source-artifact",
        source_artifact_incarnation_id="incarnation-1",
        binding_valid=True,
        binding_error=None,
        origin=LoadedSkillOrigin.AUTOSKILLIT,
    )
    projection = ManagedLeafProjection(
        binding=ManagedLeafBinding(
            assignment=assignment,
            source_artifact_digest=selected_source.source_artifact_digest,
            source_artifact_incarnation_id=selected_source.source_artifact_incarnation_id,
            source_projected_digest=selected_source.projected_digest,
            canonical_digest=selected_source.canonical_digest,
            semantic_digest=selected_source.semantic_digest,
            adaptation_digest=selected_source.adaptation_digest,
            model=_SYNTHETIC_MODEL_ID,
            reasoning_effort="high",
            workspace=classify_managed_leaf_workspace(
                read_only=True, write_behavior=WriteBehaviorSpec()
            ),
        ),
        prompt="Inspect the change.",
        leaf_projection_artifact_digest="leaf-projection",
    )
    launch = ManagedLaunchBinding(
        request_session_id="transport-session",
        managed_parent_id=parent_id,
        parent_session_id=parent_id,
        caller_key="caller",
        attestation_epoch=0,
        recovery_ready=True,
        selected_source=selected_source,
    )
    source_document = AgentSkillDocument(
        content="Inspect the change.",
        projected_digest=selected_source.projected_digest,
        canonical_digest=selected_source.canonical_digest,
        source_identity=SkillSourceIdentity(SkillSource.BUNDLED_EXTENDED, "review-skill"),
    )
    manager = _RecordingFixedBatchManager(
        tmp_path / "leaf-home", "review-skill", backend.conventions
    )
    tool_ctx = SimpleNamespace(
        session_skill_manager=manager,
        executor=SimpleNamespace(),
        backend=backend,
        runner=None,
        project_dir=tmp_path,
        plugin_authority=plugin_authority,
    )
    adapter = _ManagedLeafLaunchAdapter(
        tool_ctx=cast(ToolContext, tool_ctx),
        launch=launch,
        invocation=cast(
            Any,
            SimpleNamespace(
                root=SimpleNamespace(name="review-skill"),
                semantic_plans=(),
                closure=(),
                project_root=None,
            ),
        ),
        projection_context=cast(
            SkillProjectionContext,
            SimpleNamespace(
                adaptation_context=make_managed_codex_context(parent_id),
                parent_sandbox_mode="workspace-write",
            ),
        ),
        source_name="review-skill",
        write_behavior=WriteBehaviorSpec(),
        read_only=True,
        adaptation=SkillSemanticAdaptationResult(),
        source_document=source_document,
    )
    permit = ManagedWorkerPermit(
        permit_id="permit-1", _authority_id="authority-1", _owner="owner-1"
    )
    return adapter, projection, permit, manager


@pytest.mark.anyio
async def test_fixed_batch_prepare_threads_session_hook_root_into_leaf_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autoskillit.server.tools.tools_execution import _fixed_batch_handlers as fbh

    backend = CodexBackend()
    authority = FakePluginArtifactAuthority(tmp_path / "fixed-batch-plugin-root")
    adapter, projection, permit, manager = _build_leaf_adapter(
        tmp_path, backend=backend, plugin_authority=authority
    )
    monkeypatch.setattr(
        fbh, "project_agent_skill_document", lambda *a, **k: adapter.source_document
    )
    monkeypatch.setattr(fbh, "project_managed_leaf", lambda *a, **k: projection)

    try:
        async with adapter(projection, permit):
            pass

        assert manager.materialize_calls
        leaf_context = manager.materialize_calls[0]
        assert leaf_context.session_hook_root is not None
        assert len(authority.bindings) == 1
        assert (
            leaf_context.session_hook_root.artifact_path
            == authority.bindings[0].identity.managed_path
        )
        assert authority.bindings[0].closed
    finally:
        authority.close()


@pytest.mark.anyio
async def test_fixed_batch_prepare_claude_backend_acquires_no_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autoskillit.server.tools.tools_execution import _fixed_batch_handlers as fbh

    backend = ClaudeCodeBackend()
    authority = FakePluginArtifactAuthority(tmp_path / "fixed-batch-plugin-root")
    adapter, projection, permit, manager = _build_leaf_adapter(
        tmp_path, backend=backend, plugin_authority=authority
    )
    monkeypatch.setattr(
        fbh, "project_agent_skill_document", lambda *a, **k: adapter.source_document
    )
    monkeypatch.setattr(fbh, "project_managed_leaf", lambda *a, **k: projection)

    try:
        async with adapter(projection, permit):
            pass

        assert manager.materialize_calls
        assert manager.materialize_calls[0].session_hook_root is None
        assert authority.bindings == []
    finally:
        authority.close()


def _install_direct_dispatch_skill(tool_ctx: Any, *, name: str) -> None:
    """Install a resolver double for one effective skill, mirroring the
    established pattern in test_tools_execution_backend_mixing.py."""
    project_root = Path(tool_ctx.project_dir).resolve()
    skill_path = project_root / ".test-skills" / name / "SKILL.md"
    root = SkillInfo(
        name=name,
        source=SkillSource.BUNDLED_EXTENDED,
        path=skill_path,
        uses_capabilities=frozenset(),
        canonical_content=(
            f"---\nname: {name}\ndescription: Test skill\n"
            "write_paths: inherit\n---\n# Test skill\n"
        ),
    )
    invocation = EffectiveSkillInvocation(
        root=root,
        closure=(root,),
        capability_union=frozenset(),
        project_root=project_root,
        execution_role=SkillExecutionRole.SESSION,
    )
    resolver = MagicMock()
    resolver.resolve.return_value = root
    resolver.resolve_invocation.return_value = invocation
    tool_ctx.skill_resolver = resolver


def _seed_plugin_dispatcher(plugin_dir: Path) -> None:
    """Create the one file _resolve_codex_hooks_dir validates before baking a config."""
    dispatcher = plugin_dir / "hooks" / "_dispatch.py"
    dispatcher.parent.mkdir(parents=True, exist_ok=True)
    dispatcher.write_text("# stub dispatcher\n", encoding="utf-8")


def _install_direct_dispatch_session_manager(tool_ctx: Any, tmp_path: Path) -> None:
    from autoskillit.workspace import DefaultSessionSkillManager, SkillsDirectoryProvider

    tool_ctx.session_skill_manager = DefaultSessionSkillManager(
        SkillsDirectoryProvider(),
        ephemeral_root=tmp_path / "ephemeral-sessions",
        persistent_roots={"codex": tmp_path / "persistent-sessions"},
    )


def test_direct_dispatch_holds_session_hook_root_until_cleanup(
    tool_ctx: Any, tmp_path: Path
) -> None:
    tool_ctx.backend = CodexBackend()
    _install_direct_dispatch_skill(tool_ctx, name="direct-skill")
    _install_direct_dispatch_session_manager(tool_ctx, tmp_path)
    project_root = Path(tool_ctx.project_dir).resolve()
    authority = FakePluginArtifactAuthority(
        project_root / ".autoskillit" / "plugin-projections" / "direct-dispatch"
    )
    _seed_plugin_dispatcher(authority.plugin_dir)
    tool_ctx.plugin_authority = authority

    dispatch, error = _prepare_direct_skill_dispatch("/direct-skill", str(project_root), tool_ctx)

    assert error is None, error
    assert dispatch is not None
    assert len(authority.bindings) == 1
    binding = authority.bindings[0]
    assert not binding.closed
    root = dispatch.projection_context.session_hook_root
    assert root is not None
    assert root.artifact_path == binding.identity.managed_path

    dispatch.cleanup(tool_ctx)
    assert binding.closed


def test_direct_dispatch_releases_session_hook_root_on_materialization_failure(
    tool_ctx: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool_ctx.backend = CodexBackend()
    _install_direct_dispatch_skill(tool_ctx, name="direct-skill")
    _install_direct_dispatch_session_manager(tool_ctx, tmp_path)
    project_root = Path(tool_ctx.project_dir).resolve()
    authority = FakePluginArtifactAuthority(
        project_root / ".autoskillit" / "plugin-projections" / "direct-dispatch"
    )
    tool_ctx.plugin_authority = authority

    def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("forced materialization failure")

    monkeypatch.setattr(tool_ctx.session_skill_manager, "materialize_invocation", _boom)

    dispatch, error = _prepare_direct_skill_dispatch("/direct-skill", str(project_root), tool_ctx)

    assert dispatch is None
    assert error is not None
    assert len(authority.bindings) == 1
    assert authority.bindings[0].closed


@pytest.mark.parametrize("failure_phase", ["materialization", "dispatch-construction"])
def test_direct_dispatch_releases_session_hook_root_before_ownership_transfer(
    tool_ctx: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_phase: str,
) -> None:
    from autoskillit.server.tools import _backend_compat

    tool_ctx.backend = CodexBackend()
    _install_direct_dispatch_skill(tool_ctx, name="direct-skill")
    _install_direct_dispatch_session_manager(tool_ctx, tmp_path)
    project_root = Path(tool_ctx.project_dir).resolve()
    authority = FakePluginArtifactAuthority(
        project_root / ".autoskillit" / "plugin-projections" / "direct-dispatch"
    )
    _seed_plugin_dispatcher(authority.plugin_dir)
    tool_ctx.plugin_authority = authority

    if failure_phase == "materialization":
        expected_error = KeyboardInterrupt

        def _interrupt(*args: object, **kwargs: object) -> None:
            raise KeyboardInterrupt("forced materialization interruption")

        monkeypatch.setattr(tool_ctx.session_skill_manager, "materialize_invocation", _interrupt)
    else:
        expected_error = SkillContractError
        monkeypatch.setattr(_backend_compat, "render_target_skill_command", lambda *args: "")

    with pytest.raises(expected_error):
        _prepare_direct_skill_dispatch("/direct-skill", str(project_root), tool_ctx)

    assert len(authority.bindings) == 1
    assert authority.bindings[0].closed


def test_direct_dispatch_claude_backend_acquires_no_binding(tool_ctx: Any, tmp_path: Path) -> None:
    from autoskillit.execution.backends import get_backend

    tool_ctx.backend = get_backend("claude-code")
    _install_direct_dispatch_skill(tool_ctx, name="direct-skill")
    project_root = Path(tool_ctx.project_dir).resolve()
    authority = FakePluginArtifactAuthority(
        project_root / ".autoskillit" / "plugin-projections" / "direct-dispatch"
    )
    tool_ctx.plugin_authority = authority

    dispatch, error = _prepare_direct_skill_dispatch("/direct-skill", str(project_root), tool_ctx)

    assert error is None, error
    assert dispatch is not None
    assert dispatch.projection_context.session_hook_root is None
    assert authority.bindings == []

    dispatch.cleanup(tool_ctx)
