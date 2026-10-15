import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import psutil
import pytest

from autoskillit.config import AutomationConfig
from autoskillit.hooks import _HOOK_CONFIG_PATH_COMPONENTS
from autoskillit.server import _tracker_authority
from autoskillit.server._factory import make_context
from autoskillit.server.lifecycle import _state
from autoskillit.server.tools import tools_kitchen
from autoskillit.server.tools.tools_kitchen import (
    _close_kitchen_handler,
    _open_kitchen_handler,
)
from autoskillit.server.tools.tools_kitchen._open_kitchen._tracker_auto_init import (
    prune_stale_kitchen_state,
)
from tests.conftest import bind_test_kitchen_identity
from tests.fakes import FakePluginArtifactAuthority
from tests.server._helpers import _write_registry

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium]


async def test_kitchen_open_close_lifecycle(monkeypatch, tmp_path):
    """Gate: disabled→enabled→disabled; hook_config written then removed; task cancelled."""
    monkeypatch.chdir(tmp_path)

    ctx = make_context(
        AutomationConfig(),
        runner=None,
        plugin_authority=FakePluginArtifactAuthority(tmp_path),
        project_dir=tmp_path,
    )
    bind_test_kitchen_identity(ctx, "test-kitchen")
    monkeypatch.setattr(_state, "_ctx", ctx)
    monkeypatch.setattr(_state, "_startup_ready", None)

    hook_config_path = tmp_path.joinpath(*_HOOK_CONFIG_PATH_COMPONENTS)

    with (
        patch.object(tools_kitchen, "_prime_quota_cache", new_callable=AsyncMock),
        patch.object(_tracker_authority, "register_active_kitchen"),
        patch.object(_tracker_authority, "unregister_active_kitchen"),
    ):
        # initial state
        assert ctx.gate.enabled is False

        # open kitchen
        result = await _open_kitchen_handler()
        assert result is None  # no failure envelope

        assert ctx.gate.enabled is True
        assert hook_config_path.exists()
        data = json.loads(hook_config_path.read_text())
        assert "quota_guard" in data
        assert "kitchen_id" in data
        task = ctx.quota_refresh_task
        assert task is not None

        # close kitchen
        _close_kitchen_handler()

        assert ctx.gate.enabled is False
        assert not hook_config_path.exists()
        assert ctx.quota_refresh_task is None

    # drain the cancelled task from the event loop
    await asyncio.sleep(0)
    assert task.cancelled() or task.done()


async def test_kitchen_lifecycle_logs_registry_write_refusals(monkeypatch, tmp_path):
    """Open and close continue while explicitly consuming refused registry writes."""
    monkeypatch.chdir(tmp_path)
    ctx = make_context(
        AutomationConfig(),
        runner=None,
        plugin_authority=FakePluginArtifactAuthority(tmp_path),
        project_dir=tmp_path,
    )
    bind_test_kitchen_identity(ctx, "test-kitchen")
    monkeypatch.setattr(_state, "_ctx", ctx)
    monkeypatch.setattr(_state, "_startup_ready", None)
    lifecycle_logger = MagicMock()

    with (
        patch.object(tools_kitchen, "_prime_quota_cache", new_callable=AsyncMock),
        patch.object(_tracker_authority, "register_active_kitchen", return_value=False),
        patch.object(_tracker_authority, "unregister_active_kitchen", return_value=False),
        patch.object(_tracker_authority, "logger", lifecycle_logger),
    ):
        assert await _open_kitchen_handler() is None
        kitchen_id = ctx.kitchen_id
        _close_kitchen_handler()

    assert ctx.gate.enabled is False
    lifecycle_logger.warning.assert_any_call(
        "active_kitchen_registration_refused",
        kitchen_id=kitchen_id,
    )
    lifecycle_logger.warning.assert_any_call(
        "active_kitchen_unregistration_refused",
        kitchen_id=kitchen_id,
    )
    await asyncio.sleep(0)


async def test_open_kitchen_runs_reaper(monkeypatch, tmp_path):
    """Test 1C: _open_kitchen_handler must call reap_stale_dispatches_async.

    Interactive sessions need the reaper at kitchen-open time since they
    never go through _fleet_auto_gate_boot or _food_truck_auto_gate_boot.
    """
    monkeypatch.chdir(tmp_path)

    ctx = make_context(
        AutomationConfig(),
        runner=None,
        plugin_authority=FakePluginArtifactAuthority(tmp_path),
        project_dir=tmp_path,
    )
    bind_test_kitchen_identity(ctx, "test-kitchen")
    monkeypatch.setattr(_state, "_ctx", ctx)
    monkeypatch.setattr(_state, "_startup_ready", None)

    fake_state_path = tmp_path / "dispatches" / "campaign.json"

    reaper_called = AsyncMock()

    monkeypatch.setattr(
        tools_kitchen, "discover_campaign_state_files", lambda _project_dir: [fake_state_path]
    )
    monkeypatch.setattr(tools_kitchen, "reap_stale_dispatches_async", reaper_called)

    with (
        patch.object(tools_kitchen, "_prime_quota_cache", new_callable=AsyncMock),
        patch.object(_tracker_authority, "register_active_kitchen"),
    ):
        result = await _open_kitchen_handler()
        assert result is None
        reaper_called.assert_awaited_once()
        call_args = reaper_called.await_args
        assert call_args is not None
        assert list(call_args.args[0]) == [fake_state_path]


async def test_close_kitchen_preserves_peer_tracker_and_lease_sidecar(monkeypatch, tmp_path):
    """Close retires only its exact tracker and never removes peer authority."""
    from autoskillit.core import (
        ArtifactLease,
        ArtifactLeaseContention,
        TrackerAuthorityTarget,
        tracker_lease_path,
    )

    monkeypatch.chdir(tmp_path)

    ctx = make_context(
        AutomationConfig(),
        runner=None,
        plugin_authority=FakePluginArtifactAuthority(tmp_path),
        project_dir=tmp_path,
    )
    bind_test_kitchen_identity(ctx, "test-kitchen")
    monkeypatch.setattr(_state, "_ctx", ctx)
    monkeypatch.setattr(_state, "_startup_ready", None)

    tracker_dir = tmp_path / ".autoskillit" / "temp" / "pipeline_tracker"
    tracker_dir.mkdir(parents=True)
    peer_tracker = tracker_dir / "AB.json"
    peer_tracker.write_text(
        '{"pipeline_id": "AB", "kitchen_id": "AB", "steps": {}, "dependencies": {}}'
    )
    peer_target = TrackerAuthorityTarget.for_project(tmp_path, "AB", expected=False)
    peer_lease_path = tracker_lease_path(peer_target)

    lease = ArtifactLease.acquire_shared(peer_lease_path, timeout=2.0)
    peer_lease_inode = peer_lease_path.stat().st_ino
    try:
        with (
            patch.object(tools_kitchen, "_prime_quota_cache", new_callable=AsyncMock),
            patch.object(_tracker_authority, "register_active_kitchen"),
            patch.object(_tracker_authority, "unregister_active_kitchen"),
        ):
            await _open_kitchen_handler()
            current_target = TrackerAuthorityTarget.for_project(
                tmp_path, ctx.kitchen_id, expected=False
            )
            current_target.path.write_text(
                json.dumps(
                    {
                        "pipeline_id": ctx.kitchen_id,
                        "kitchen_id": ctx.kitchen_id,
                        "steps": {},
                        "dependencies": {},
                    }
                )
            )
            _close_kitchen_handler()

        assert peer_tracker.exists()
        assert peer_lease_path.exists()
        assert peer_lease_path.stat().st_ino == peer_lease_inode
        with pytest.raises(ArtifactLeaseContention):
            ArtifactLease.acquire_exclusive(peer_lease_path, timeout=0.0)
        assert not current_target.path.exists()
        assert tracker_lease_path(current_target).exists()
    finally:
        lease.close_preserving()


@pytest.fixture
def campaign_id(request, monkeypatch) -> str | None:
    value = getattr(request, "param", None)
    if value is not None:
        monkeypatch.setenv("AUTOSKILLIT_CAMPAIGN_ID", value)
    return value


@pytest.fixture
def real_kitchen_lifecycle(monkeypatch, tmp_path, campaign_id):
    """Build real contexts with the lifecycle effects isolated to this test."""
    from autoskillit.server import _misc
    from autoskillit.server.lifecycle import _lifespan
    from autoskillit.server.recipe import _recipe_generation, _recipe_section_pagination
    from autoskillit.server.recipe._recipe_generation import RecipeGenerationStore
    from autoskillit.server.recipe._recipe_section_pagination import PagePlanCache
    from autoskillit.server.tools.tools_kitchen._open_kitchen import _gate as open_gate
    from tests.server._helpers import _mock_fmcp_ctx
    from tests.server._recipe_fixture_parameters import RESEARCH_OVERRIDES

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "0")
    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "orchestrator")
    monkeypatch.setattr(_recipe_generation, "_RECIPE_GENERATION_STORE", RecipeGenerationStore())
    monkeypatch.setattr(_recipe_section_pagination, "_PAGE_PLAN_CACHE", PagePlanCache())

    def make_context_for_lifecycle():
        return make_context(
            AutomationConfig(),
            runner=None,
            plugin_authority=FakePluginArtifactAuthority(tmp_path),
            project_dir=tmp_path,
        )

    ctx = make_context_for_lifecycle()
    monkeypatch.setattr(_state, "_ctx", ctx)
    monkeypatch.setattr(_state, "_startup_ready", None)
    overrides = {
        **RESEARCH_OVERRIDES,
        "source_dir": str(ctx.project_dir.resolve()),
    }
    fmcp_ctx = _mock_fmcp_ctx()

    def new_context():
        return make_context_for_lifecycle()

    with (
        patch.object(tools_kitchen, "_prime_quota_cache", new_callable=AsyncMock),
        patch.object(_misc, "_prime_quota_cache", new_callable=AsyncMock),
        patch.object(tools_kitchen, "_write_hook_config"),
        patch.object(tools_kitchen, "create_background_task", return_value=None),
        patch.object(tools_kitchen, "discover_campaign_state_files", return_value=[]),
        patch.object(tools_kitchen, "reap_stale_dispatches_async", new_callable=AsyncMock),
        patch.object(tools_kitchen, "sweep_orphaned_tethers_async", new_callable=AsyncMock),
        patch.object(open_gate, "sweep_stale_markers"),
        patch.object(open_gate, "prune_stale_kitchen_state"),
        patch.object(_lifespan, "_reap_self_excluded_codex_and_daemon_orphans"),
        patch.object(_tracker_authority, "register_active_kitchen", return_value=True),
        patch.object(_tracker_authority, "unregister_active_kitchen", return_value=True),
    ):
        yield SimpleNamespace(
            ctx=ctx,
            overrides=overrides,
            fmcp_ctx=fmcp_ctx,
            new_context=new_context,
        )


@pytest.mark.parametrize(
    "campaign_id",
    [None, "campaign-5270"],
    indirect=True,
    ids=["ephemeral", "campaign"],
)
async def test_back_to_back_open_close_open_resets_infrastructure(
    campaign_id,
    real_kitchen_lifecycle,
):
    """Close resets identity, and a real reopen establishes a new usable identity."""
    from autoskillit.pipeline import KitchenOpenPhase
    from autoskillit.server.tools.tools_kitchen import open_kitchen
    from autoskillit.server.tools.tools_recipe import load_recipe

    ctx = real_kitchen_lifecycle.ctx
    overrides = real_kitchen_lifecycle.overrides
    fmcp_ctx = real_kitchen_lifecycle.fmcp_ctx

    result1 = json.loads(await open_kitchen(name="research", overrides=overrides, ctx=fmcp_ctx))
    assert result1["success"] is True, result1
    assert ctx.kitchen_open_state.phase is KitchenOpenPhase.COMMITTED
    assert ctx.gate_infrastructure_ready is True
    first_id = ctx.kitchen_id
    first_activation_id = ctx.kitchen_activation_id
    first_identity = ctx.kitchen_process_identity
    assert first_identity is not None
    assert first_identity.kitchen_id == first_id

    _close_kitchen_handler()
    assert ctx.gate_infrastructure_ready is False
    assert ctx.kitchen_open_state.phase is KitchenOpenPhase.CLOSED
    assert ctx.kitchen_id == ""
    assert ctx.kitchen_process_identity is None

    result2 = json.loads(await open_kitchen(name="research", overrides=overrides, ctx=fmcp_ctx))
    assert result2["success"] is True, result2
    assert ctx.kitchen_open_state.phase is KitchenOpenPhase.COMMITTED
    assert ctx.gate_infrastructure_ready is True
    second_id = ctx.kitchen_id
    assert ctx.kitchen_activation_id != first_activation_id
    if campaign_id is None:
        assert second_id != first_id
    else:
        assert first_id == second_id == campaign_id
    second_identity = ctx.kitchen_process_identity
    assert second_identity is not None
    assert second_identity.kitchen_id == second_id

    loaded = json.loads(await load_recipe(name="research", overrides=overrides))
    assert loaded["success"] is True, loaded

    _close_kitchen_handler()
    await asyncio.sleep(0)


@pytest.mark.parametrize("campaign_id", ["campaign-5270"], indirect=True)
async def test_same_campaign_reopen_issues_fresh_recipe_authority(
    campaign_id,
    real_kitchen_lifecycle,
):
    import json

    from autoskillit.fleet import (
        DispatchRecord,
        DispatchStatus,
        read_all_campaign_captures,
        write_captured_values,
        write_initial_state,
    )
    from autoskillit.pipeline import ReadyRecipe
    from autoskillit.server.recipe._recipe_execution import (
        RecipeExecutionAdmissionError,
        bind_attested_runtime_invocation,
    )
    from autoskillit.server.tools.tools_kitchen import open_kitchen
    from autoskillit.server.tools.tools_recipe import (
        complete_recipe_initialization,
        get_recipe_section,
    )
    from tests.server._helpers import _credit_initialization_sections, _resolve_recipe_section

    ctx = real_kitchen_lifecycle.ctx
    overrides = real_kitchen_lifecycle.overrides
    fmcp_ctx = real_kitchen_lifecycle.fmcp_ctx

    first = json.loads(await open_kitchen(name="research", overrides=overrides, ctx=fmcp_ctx))
    assert first["success"] is True, first
    await _credit_initialization_sections(first)
    first_state = ctx.recipe_initialization_state
    assert isinstance(first_state, ReadyRecipe)
    first_execution_id = first_state.installed_execution.snapshot.execution_id

    prior_state = ctx.temp_dir / "dispatches" / "prior.json"
    write_initial_state(
        prior_state,
        campaign_id=ctx.kitchen_id,
        campaign_name="c",
        manifest_path="",
        dispatches=[DispatchRecord(name="a", status=DispatchStatus.SUCCESS)],
    )
    write_captured_values(prior_state, {"k": "v"})

    _close_kitchen_handler()
    recipe_root = ctx.temp_dir / "recipe-delivery"
    retired_markers = list((recipe_root / ".retired").glob("*.retired"))
    assert len(retired_markers) == 1
    retired_marker = retired_markers[0]

    second = json.loads(await open_kitchen(name="research", overrides=overrides, ctx=fmcp_ctx))
    assert second["success"] is True, second
    assert ctx.kitchen_id == campaign_id
    await _credit_initialization_sections(second)
    second_state = ctx.recipe_initialization_state
    assert isinstance(second_state, ReadyRecipe)
    assert await _resolve_recipe_section(second, section="content") is not None
    second_execution_id = second_state.installed_execution.snapshot.execution_id
    assert second_execution_id != first_execution_id
    assert second["initialization_id"] != first["initialization_id"]

    stale_identity = {
        key: value for key, value in first["recipe_pull"].items() if key != "pull_tool"
    }
    stale_pull = json.loads(await get_recipe_section(section="content", **stale_identity))
    assert stale_pull == {
        "success": False,
        "error": "invalid_recipe_artifact_identity",
    }
    stale_completion = json.loads(
        await complete_recipe_initialization(initialization_id=first["initialization_id"])
    )
    assert stale_completion == {
        "success": False,
        "error": "recipe_initialization_stale",
    }

    installed = second_state.installed_execution
    with pytest.raises(RecipeExecutionAdmissionError) as error:
        bind_attested_runtime_invocation(
            installed,
            execution_id=first_execution_id,
            step_name="scope",
            template_digest="",
            skill_command="",
            skill_inputs=None,
            actual_mcp_kwargs={},
        )
    assert error.value.code == "recipe_execution_id_mismatch"

    assert retired_marker.exists()
    active_namespaces = [
        path for path in recipe_root.iterdir() if path.is_dir() and path.name != ".retired"
    ]
    assert len(active_namespaces) == 1
    assert active_namespaces[0].name != retired_marker.stem
    assert read_all_campaign_captures(ctx.temp_dir / "dispatches", ctx.kitchen_id) == {"k": "v"}
    _close_kitchen_handler()


@pytest.mark.parametrize("campaign_id", ["campaign-5270"], indirect=True)
async def test_same_campaign_anonymous_reopen_then_load_recipe_and_get_recipe(
    campaign_id,
    real_kitchen_lifecycle,
):
    import json

    from autoskillit.server.tools.tools_kitchen import get_recipe, open_kitchen
    from autoskillit.server.tools.tools_recipe import load_recipe
    from tests.server._helpers import _resolve_recipe_section

    overrides = real_kitchen_lifecycle.overrides
    fmcp_ctx = real_kitchen_lifecycle.fmcp_ctx

    named = json.loads(await open_kitchen(name="research", overrides=overrides, ctx=fmcp_ctx))
    assert named["success"] is True, named
    _close_kitchen_handler()

    anonymous = json.loads(await open_kitchen(ctx=fmcp_ctx))
    assert anonymous["success"] is True, anonymous
    assert real_kitchen_lifecycle.ctx.kitchen_id == campaign_id
    loaded = json.loads(await load_recipe(name="research", overrides=overrides))
    assert loaded["success"] is True, loaded
    resource = json.loads(get_recipe("research"))
    assert "error" not in resource, resource
    assert await _resolve_recipe_section(resource) is not None
    _close_kitchen_handler()


@pytest.mark.parametrize(
    "campaign_id",
    [None, "campaign-5270"],
    indirect=True,
    ids=["ephemeral", "campaign"],
)
async def test_reopen_with_new_arguments_stays_in_one_activation(
    campaign_id,
    real_kitchen_lifecycle,
):
    import json

    from autoskillit.server.tools.tools_kitchen import open_kitchen
    from tests.server._helpers import _resolve_recipe_section

    ctx = real_kitchen_lifecycle.ctx
    fmcp_ctx = real_kitchen_lifecycle.fmcp_ctx
    overrides = real_kitchen_lifecycle.overrides

    first = json.loads(await open_kitchen(name="research", overrides=overrides, ctx=fmcp_ctx))
    assert first["success"] is True, first
    first_id = ctx.kitchen_id
    second_overrides = {**overrides, "task_description": "changed task"}
    second = json.loads(
        await open_kitchen(name="research", overrides=second_overrides, ctx=fmcp_ctx)
    )
    assert second["success"] is True, second
    assert ctx.kitchen_id == first_id
    if campaign_id is not None:
        assert ctx.kitchen_id == campaign_id
    assert await _resolve_recipe_section(first, section="content") is not None

    _close_kitchen_handler()
    recipe_root = ctx.temp_dir / "recipe-delivery"
    namespaces = [
        path for path in recipe_root.iterdir() if path.is_dir() and path.name != ".retired"
    ]
    assert namespaces == []
    assert len(list((recipe_root / ".retired").glob("*.retired"))) == 1


@pytest.mark.parametrize("campaign_id", ["campaign-5270"], indirect=True)
async def test_sibling_contexts_sharing_campaign_and_temp_dir_are_isolated(
    monkeypatch,
    campaign_id,
    real_kitchen_lifecycle,
):
    import json

    from autoskillit.server.tools.tools_kitchen import open_kitchen
    from autoskillit.server.tools.tools_recipe import get_recipe_section

    ctx_a = real_kitchen_lifecycle.ctx
    ctx_b = real_kitchen_lifecycle.new_context()
    assert ctx_b.temp_dir == ctx_a.temp_dir
    overrides = real_kitchen_lifecycle.overrides
    fmcp_ctx = real_kitchen_lifecycle.fmcp_ctx

    first = json.loads(await open_kitchen(name="research", overrides=overrides, ctx=fmcp_ctx))
    assert first["success"] is True, first
    monkeypatch.setattr(_state, "_ctx", ctx_b)
    second = json.loads(await open_kitchen(name="research", overrides=overrides, ctx=fmcp_ctx))
    assert second["success"] is True, second
    assert ctx_a.kitchen_id == ctx_b.kitchen_id == campaign_id

    monkeypatch.setattr(_state, "_ctx", ctx_a)
    _close_kitchen_handler()
    monkeypatch.setattr(_state, "_ctx", ctx_b)
    b_identity = {key: value for key, value in second["recipe_pull"].items() if key != "pull_tool"}
    pulled = json.loads(await get_recipe_section(section="content", **b_identity))
    assert pulled["success"] is True, pulled

    recipe_root = ctx_b.temp_dir / "recipe-delivery"
    active_namespaces = [
        path for path in recipe_root.iterdir() if path.is_dir() and path.name != ".retired"
    ]
    assert len(active_namespaces) == 1
    retired_markers = list((recipe_root / ".retired").glob("*.retired"))
    assert all(marker.stem != active_namespaces[0].name for marker in retired_markers)
    _close_kitchen_handler()


def _write_tracker(tracker_dir, kitchen_id, *, initialized_at=None):
    tracker_dir.mkdir(parents=True, exist_ok=True)
    tracker_data = {
        "kitchen_id": kitchen_id,
        "pipeline_id": kitchen_id,
        "initialized_at": (initialized_at or datetime.now(UTC)).isoformat(),
        "steps": {},
        "dependencies": {},
    }
    (tracker_dir / f"{kitchen_id}.json").write_text(json.dumps(tracker_data))


def test_open_without_close_prunes_dead_kitchen_tracker(monkeypatch, tmp_path):
    """A tracker whose registered PID is dead must be reaped on next open."""
    tracker_dir = tmp_path / ".autoskillit" / "temp" / "pipeline_tracker"
    _write_tracker(tracker_dir, "K1")
    _write_registry(
        monkeypatch,
        tmp_path,
        [
            {
                "kitchen_id": "K1",
                "pid": 999999999,
                "create_time": 1234567890.0,
                "project_path": str(tmp_path),
                "opened_at": datetime.now(UTC).isoformat(),
            }
        ],
    )

    prune_stale_kitchen_state(tmp_path, "K2")

    assert not (tracker_dir / "K1.json").exists()


def test_open_preserves_live_foreign_kitchen_tracker(monkeypatch, tmp_path):
    """A tracker whose registered PID is alive (a different kitchen) must survive."""
    tracker_dir = tmp_path / ".autoskillit" / "temp" / "pipeline_tracker"
    _write_tracker(tracker_dir, "K1")
    _write_registry(
        monkeypatch,
        tmp_path,
        [
            {
                "kitchen_id": "K1",
                "pid": os.getpid(),
                "create_time": psutil.Process(os.getpid()).create_time(),
                "project_path": str(tmp_path),
                "opened_at": datetime.now(UTC).isoformat(),
            }
        ],
    )

    prune_stale_kitchen_state(tmp_path, "K2")

    assert (tracker_dir / "K1.json").exists()


def test_open_retires_unleased_orphan_without_age_inference(monkeypatch, tmp_path):
    """Fresh absence of live registry ownership is sufficient retirement evidence."""
    tracker_dir = tmp_path / ".autoskillit" / "temp" / "pipeline_tracker"
    _write_tracker(tracker_dir, "K1", initialized_at=datetime.now(UTC))
    _write_registry(monkeypatch, tmp_path, [])

    prune_stale_kitchen_state(tmp_path, "K2")

    assert not (tracker_dir / "K1.json").exists()


def test_open_preserves_orphan_while_shared_lease_is_held(monkeypatch, tmp_path):
    """A live participant lease is sufficient ownership evidence."""
    from autoskillit.core import ArtifactLease, TrackerAuthorityTarget, tracker_lease_path

    tracker_dir = tmp_path / ".autoskillit" / "temp" / "pipeline_tracker"
    _write_tracker(tracker_dir, "K1", initialized_at=datetime.now(UTC))
    _write_registry(monkeypatch, tmp_path, [])
    target = TrackerAuthorityTarget.for_project(tmp_path, "K1", expected=False)

    lease = ArtifactLease.acquire_shared(tracker_lease_path(target), timeout=2.0)
    try:
        prune_stale_kitchen_state(tmp_path, "K2")
        assert target.path.exists()
    finally:
        lease.close_preserving()

    prune_stale_kitchen_state(tmp_path, "K2")
    assert not target.path.exists()


def test_multi_entry_same_kitchen_one_alive_preserves_tracker(monkeypatch, tmp_path):
    """Fleet-campaign shape: multiple registry entries share one kitchen_id.

    If any matching entry is alive, the tracker must survive even though
    another entry for the same kitchen_id is dead.
    """
    tracker_dir = tmp_path / ".autoskillit" / "temp" / "pipeline_tracker"
    _write_tracker(tracker_dir, "K1")
    _write_registry(
        monkeypatch,
        tmp_path,
        [
            {
                "kitchen_id": "K1",
                "pid": 999999999,
                "create_time": 1234567890.0,
                "project_path": str(tmp_path),
                "opened_at": datetime.now(UTC).isoformat(),
            },
            {
                "kitchen_id": "K1",
                "pid": os.getpid(),
                "create_time": psutil.Process(os.getpid()).create_time(),
                "project_path": str(tmp_path),
                "opened_at": datetime.now(UTC).isoformat(),
            },
        ],
    )

    prune_stale_kitchen_state(tmp_path, "different-kitchen")

    assert (tracker_dir / "K1.json").exists()


def test_same_process_reopen_replaces_registry_entry(monkeypatch, tmp_path):
    """A tracker survives while its registry entry is alive, then is reaped once dead."""
    tracker_dir = tmp_path / ".autoskillit" / "temp" / "pipeline_tracker"
    _write_tracker(tracker_dir, "K1")
    _write_registry(
        monkeypatch,
        tmp_path,
        [
            {
                "kitchen_id": "K1",
                "pid": os.getpid(),
                "create_time": psutil.Process(os.getpid()).create_time(),
                "project_path": str(tmp_path),
                "opened_at": datetime.now(UTC).isoformat(),
            }
        ],
    )

    prune_stale_kitchen_state(tmp_path, "K2")

    assert (tracker_dir / "K1.json").exists()

    _write_registry(
        monkeypatch,
        tmp_path,
        [
            {
                "kitchen_id": "K1",
                "pid": 999999999,
                "create_time": 1234567890.0,
                "project_path": str(tmp_path),
                "opened_at": datetime.now(UTC).isoformat(),
            }
        ],
    )

    prune_stale_kitchen_state(tmp_path, "K2")

    assert not (tracker_dir / "K1.json").exists()


async def test_open_kitchen_sweeps_stale_kitchen_state_markers(monkeypatch, tmp_path):
    """_open_kitchen_handler wires sweep_stale_markers() to remove aged kitchen_state markers."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AUTOSKILLIT_STATE_DIR", str(tmp_path / "state"))
    state_dir = tmp_path / "state" / "kitchen_state"
    state_dir.mkdir(parents=True, exist_ok=True)

    stale_marker = state_dir / "stale-session.json"
    stale_marker.write_text(
        json.dumps(
            {
                "session_id": "stale-session",
                "opened_at": (datetime.now(UTC) - timedelta(hours=25)).isoformat(),
                "recipe_name": "test",
                "marker_version": 1,
            }
        )
    )
    fresh_marker = state_dir / "fresh-session.json"
    fresh_marker.write_text(
        json.dumps(
            {
                "session_id": "fresh-session",
                "opened_at": datetime.now(UTC).isoformat(),
                "recipe_name": "test",
                "marker_version": 1,
            }
        )
    )

    ctx = make_context(
        AutomationConfig(),
        runner=None,
        plugin_authority=FakePluginArtifactAuthority(tmp_path),
        project_dir=tmp_path,
    )
    bind_test_kitchen_identity(ctx, "test-kitchen")
    monkeypatch.setattr(_state, "_ctx", ctx)
    monkeypatch.setattr(_state, "_startup_ready", None)

    with (
        patch.object(tools_kitchen, "_prime_quota_cache", new_callable=AsyncMock),
        patch.object(_tracker_authority, "register_active_kitchen"),
        patch.object(_tracker_authority, "unregister_active_kitchen"),
    ):
        result = await _open_kitchen_handler()
        assert result is None

    assert not stale_marker.exists()
    assert fresh_marker.exists()


def test_deferred_recall_open_still_prunes(monkeypatch, tmp_path):
    """The deferred-recall path prunes stale trackers."""
    tracker_dir = tmp_path / ".autoskillit" / "temp" / "pipeline_tracker"
    _write_tracker(
        tracker_dir, "dead-kitchen", initialized_at=datetime.now(UTC) - timedelta(hours=2)
    )
    _write_registry(
        monkeypatch,
        tmp_path,
        [
            {
                "kitchen_id": "dead-kitchen",
                "pid": 999999999,
                "create_time": 1234567890.0,
                "project_path": str(tmp_path),
                "opened_at": datetime.now(UTC).isoformat(),
            }
        ],
    )

    assert (tracker_dir / "dead-kitchen.json").exists()

    prune_stale_kitchen_state(tmp_path, "live-kitchen")

    assert not (tracker_dir / "dead-kitchen.json").exists()


async def test_close_kitchen_retains_stable_lock_and_restores_config_baseline(
    monkeypatch, tmp_path
):
    """Close resets live configuration while retaining the stable lock inode."""
    monkeypatch.chdir(tmp_path)

    ctx = make_context(
        AutomationConfig(),
        runner=None,
        plugin_authority=FakePluginArtifactAuthority(tmp_path),
        project_dir=tmp_path,
    )
    bind_test_kitchen_identity(ctx, "test-kitchen")
    monkeypatch.setattr(_state, "_ctx", ctx)
    monkeypatch.setattr(_state, "_startup_ready", None)

    with (
        patch.object(tools_kitchen, "_prime_quota_cache", new_callable=AsyncMock),
        patch.object(_tracker_authority, "register_active_kitchen"),
        patch.object(_tracker_authority, "unregister_active_kitchen"),
    ):
        await _open_kitchen_handler()

        from autoskillit.server._misc import _hook_config_overlay_path

        overlay_path = _hook_config_overlay_path(ctx.project_dir)
        overlay_lock_path = overlay_path.with_suffix(".lock")
        overlay_lock_path.parent.mkdir(parents=True, exist_ok=True)
        overlay_lock_path.write_text("")
        assert overlay_lock_path.exists()

        from autoskillit.server.tools.tools_config import configure_order

        assert json.loads(await configure_order(timeout=123))["success"] is True
        assert ctx.config.run_skill.timeout == 123

        _close_kitchen_handler()

        assert overlay_lock_path.exists()
        assert not overlay_path.exists()
        assert ctx.config.run_skill.timeout == 7200
        assert ctx._session_config_overrides == {}
