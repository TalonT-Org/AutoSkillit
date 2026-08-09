"""Behavioral tests for session-scoped configuration tools."""

from __future__ import annotations

import inspect
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier

import pytest

from autoskillit.config import AutomationConfig
from autoskillit.core import get_tool_def
from autoskillit.fleet import FleetSemaphore
from tests.server.conftest import _make_mock_ctx

pytestmark = [pytest.mark.layer("server"), pytest.mark.small, pytest.mark.feature("fleet")]

_HOOK_CONFIG_RELPATH = (".autoskillit", "temp", ".hook_config.json")
_OVERLAY_RELPATH = (".autoskillit", "temp", ".hook_config_overlay.json")


def _open_context(tmp_path, config: AutomationConfig | None = None):
    baseline = config or AutomationConfig()
    ctx = _make_mock_ctx(config=baseline)
    ctx.project_dir = tmp_path
    ctx.fleet_lock = FleetSemaphore(
        max_concurrent=baseline.fleet.max_concurrent_dispatches,
        timeout=baseline.fleet.acquire_timeout_sec,
    )
    ctx.gate.enabled = True
    hook_path = tmp_path.joinpath(*_HOOK_CONFIG_RELPATH)
    hook_path.parent.mkdir(parents=True, exist_ok=True)
    hook_path.write_text("{}")
    return ctx


_BEHAVIOR_CASES = (
    ("order", "timeout", 321, "order", "timeout"),
    ("order", "stale_threshold", 11, "order", "stale_threshold"),
    ("order", "idle_output_timeout", 22, "order", "idle_output_timeout"),
    ("order", "max_suppression_seconds", 33, "order", "max_suppression_seconds"),
    ("order", "default_model", "opus", "core", "default_model"),
    ("fleet", "max_concurrent_dispatches", 4, "fleet", "max_concurrent_dispatches"),
    ("fleet", "default_timeout_sec", 777, "fleet", "default_timeout_sec"),
    ("fleet", "max_extension_seconds", 800.0, "fleet", "max_extension_seconds"),
    ("fleet", "idle_output_timeout", 44.0, "fleet", "idle_output_timeout"),
    ("fleet", "acquire_timeout_sec", 55.0, "fleet", "acquire_timeout_sec"),
    ("fleet", "enable_deadline_extension", False, "fleet", "enable_deadline_extension"),
    ("fleet", "inspector_model", "inspector-x", "fleet", "inspector_model"),
    ("fleet", "default_model", "haiku", "core", "default_model"),
)


def _observed_value(ctx, section: str, field: str):
    if field == "max_concurrent_dispatches":
        return ctx.fleet_lock.max_concurrent
    if field == "acquire_timeout_sec":
        return ctx.fleet_lock.timeout
    target = (
        ctx.config.model
        if section == "core"
        else ctx.config.run_skill
        if section == "order"
        else ctx.config.fleet
    )
    return getattr(target, field)


async def _observe_headless_defaults(ctx, monkeypatch) -> dict[str, object]:
    import autoskillit.execution.headless as headless
    from autoskillit.core import SkillResult
    from autoskillit.execution.headless import DefaultHeadlessExecutor

    observed: dict[str, object] = {}

    async def _capture(_command, _cwd, _ctx, **kwargs):
        observed.update(kwargs)
        return SkillResult.crashed(exception=RuntimeError("captured"), skill_command="/test")

    monkeypatch.setattr(headless, "run_headless_core", _capture)
    await DefaultHeadlessExecutor(ctx).run("/test", str(ctx.project_dir))
    return observed


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("tool_name", "param", "value", "snapshot_section", "snapshot_field"),
    _BEHAVIOR_CASES,
)
async def test_every_public_parameter_changes_its_runtime_observer(
    tmp_path,
    monkeypatch,
    tool_name,
    param,
    value,
    snapshot_section,
    snapshot_field,
) -> None:
    from autoskillit.server import _state
    from autoskillit.server.tools.tools_config import configure_fleet, configure_order

    ctx = _open_context(tmp_path)
    monkeypatch.setattr(_state, "_ctx", ctx)
    tool = configure_order if tool_name == "order" else configure_fleet

    payload = json.loads(await tool(**{param: value}))

    assert payload["success"] is True
    observed = _observed_value(ctx, snapshot_section, snapshot_field)
    assert observed == value
    assert payload["config"][snapshot_section][snapshot_field] == observed

    overlay = json.loads(tmp_path.joinpath(*_OVERLAY_RELPATH).read_text())
    overlay_section = "core" if param == "default_model" else tool_name
    assert overlay[overlay_section][param] == value


def test_behavior_cases_cover_exact_public_and_registered_parameters() -> None:
    from autoskillit.server.tools.tools_config import configure_fleet, configure_order

    cases = {(tool_name, param) for tool_name, param, *_ in _BEHAVIOR_CASES}
    public = {("order", name) for name in inspect.signature(configure_order).parameters} | {
        ("fleet", name) for name in inspect.signature(configure_fleet).parameters
    }
    registered = {("order", param.name) for param in get_tool_def("configure_order").params} | {
        ("fleet", param.name) for param in get_tool_def("configure_fleet").params
    }

    assert cases == public == registered
    assert ("fleet", "max_total_issues") not in registered
    assert ("fleet", "max_issues_per_food_truck") not in registered


@pytest.mark.anyio
async def test_partial_updates_accumulate_in_live_config_and_snapshot(
    tmp_path, monkeypatch
) -> None:
    from autoskillit.execution.headless._headless_helpers import resolve_model_identity
    from autoskillit.server import _state
    from autoskillit.server.tools.tools_config import configure_order

    ctx = _open_context(tmp_path)
    monkeypatch.setattr(_state, "_ctx", ctx)

    assert json.loads(await configure_order(timeout=400))["success"] is True
    payload = json.loads(await configure_order(default_model="opus"))
    observed = await _observe_headless_defaults(ctx, monkeypatch)

    assert observed["timeout"] == 400
    assert resolve_model_identity("", ctx.config).configured_model == "opus"
    assert payload["config"]["order"]["timeout"] == 400
    assert payload["config"]["core"]["default_model"] == "opus"


@pytest.mark.anyio
async def test_shared_default_model_is_last_write_wins(tmp_path, monkeypatch) -> None:
    from autoskillit.execution.headless._headless_helpers import resolve_model_identity
    from autoskillit.server import _state
    from autoskillit.server.tools.tools_config import configure_fleet, configure_order

    ctx = _open_context(tmp_path)
    monkeypatch.setattr(_state, "_ctx", ctx)

    await configure_order(default_model="opus")
    payload = json.loads(await configure_fleet(default_model="haiku"))

    assert ctx.config.model.default_model == "haiku"
    assert payload["config"]["core"]["default_model"] == "haiku"
    assert resolve_model_identity("", ctx.config).configured_model == "haiku"
    assert resolve_model_identity("explicit", ctx.config).configured_model == "explicit"


@pytest.mark.anyio
async def test_same_worktree_contexts_do_not_import_each_others_overrides(
    tmp_path,
    monkeypatch,
) -> None:
    from autoskillit.execution.headless._headless_helpers import resolve_model_identity
    from autoskillit.server import _state
    from autoskillit.server.tools.tools_config import configure_order

    ctx_a = _open_context(tmp_path)
    ctx_b = _open_context(tmp_path)
    baseline_model = ctx_a.config.model.default_model

    monkeypatch.setattr(_state, "_ctx", ctx_a)
    await configure_order(timeout=401)
    monkeypatch.setattr(_state, "_ctx", ctx_b)
    await configure_order(default_model="haiku")

    observed_a = await _observe_headless_defaults(ctx_a, monkeypatch)
    observed_b = await _observe_headless_defaults(ctx_b, monkeypatch)
    assert observed_a["timeout"] == 401
    assert resolve_model_identity("", ctx_a.config).configured_model == baseline_model
    assert observed_b["timeout"] == 7200
    assert resolve_model_identity("", ctx_b.config).configured_model == "haiku"


@pytest.mark.anyio
async def test_configured_fleet_semaphore_enforces_capacity_and_timeout(
    tmp_path, monkeypatch
) -> None:
    from autoskillit.server import _state
    from autoskillit.server.tools.tools_config import configure_fleet

    ctx = _open_context(tmp_path)
    monkeypatch.setattr(_state, "_ctx", ctx)

    payload = json.loads(
        await configure_fleet(max_concurrent_dispatches=1, acquire_timeout_sec=0.01)
    )
    assert payload["success"] is True

    await ctx.fleet_lock.acquire()
    try:
        assert ctx.fleet_lock.at_capacity() is True
        with pytest.raises(TimeoutError):
            await ctx.fleet_lock.acquire()
    finally:
        ctx.fleet_lock.release()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "raw_overlay",
    (
        "{ malformed",
        "[]",
        '{"order": []}',
        '{"order": {"unknown_setting": 1}}',
        '{"order": {"timeout": "bad"}}',
    ),
)
async def test_invalid_persisted_overlay_changes_neither_disk_nor_live_state(
    tmp_path,
    monkeypatch,
    raw_overlay,
) -> None:
    from autoskillit.server import _state
    from autoskillit.server.tools.tools_config import configure_order

    ctx = _open_context(tmp_path)
    overlay_path = tmp_path.joinpath(*_OVERLAY_RELPATH)
    overlay_path.write_text(raw_overlay)
    monkeypatch.setattr(_state, "_ctx", ctx)

    payload = json.loads(await configure_order(timeout=500))

    assert payload["success"] is False
    assert "Invalid session configuration" in payload["error"]
    assert ctx.config.run_skill.timeout == 7200
    assert ctx._session_config_overrides == {}
    assert overlay_path.read_text() == raw_overlay


@pytest.mark.anyio
@pytest.mark.parametrize(
    "kwargs",
    (
        {"timeout": 0},
        {"stale_threshold": -1},
        {"idle_output_timeout": -1},
        {"max_suppression_seconds": -1},
        {"timeout": True},
    ),
)
async def test_invalid_update_has_no_disk_or_live_partial_commit(
    tmp_path,
    monkeypatch,
    kwargs,
) -> None:
    from autoskillit.server import _state
    from autoskillit.server.tools.tools_config import configure_order

    ctx = _open_context(tmp_path)
    monkeypatch.setattr(_state, "_ctx", ctx)

    payload = json.loads(await configure_order(**kwargs))

    assert payload["success"] is False
    assert ctx.config.run_skill.timeout == 7200
    assert ctx._session_config_overrides == {}
    assert not tmp_path.joinpath(*_OVERLAY_RELPATH).exists()


def test_config_and_ingredient_writers_preserve_each_others_keys(tmp_path) -> None:
    from autoskillit.server.tools.tools_config import _commit_effective_config
    from autoskillit.server.tools.tools_kitchen import _write_ingredient_locks

    ctx = _open_context(tmp_path)
    barrier = Barrier(2)

    def configure() -> None:
        barrier.wait()
        _commit_effective_config(ctx, "order", {"timeout": 600}, {})

    def lock_ingredient() -> None:
        barrier.wait()
        _write_ingredient_locks(tmp_path, "pipeline-1", {"flag": "false"}, None, {})

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda fn: fn(), (configure, lock_ingredient)))

    overlay = json.loads(tmp_path.joinpath(*_OVERLAY_RELPATH).read_text())
    assert overlay["order"]["timeout"] == 600
    assert overlay["locked_ingredients"]["pipeline-1"]["flag"] == "false"


def test_concurrent_configuration_calls_keep_disk_and_live_state(
    tmp_path,
    monkeypatch,
) -> None:
    from autoskillit.server.tools import tools_config

    ctx = _open_context(tmp_path)
    barrier = Barrier(2)
    real_locked_overlay = tools_config.locked_overlay

    @contextmanager
    def synchronized_locked_overlay(project_dir):
        barrier.wait()
        with real_locked_overlay(project_dir) as transaction:
            yield transaction

    monkeypatch.setattr(tools_config, "locked_overlay", synchronized_locked_overlay)

    def configure_timeout() -> None:
        tools_config._commit_effective_config(ctx, "order", {"timeout": 600}, {})

    def configure_idle() -> None:
        tools_config._commit_effective_config(
            ctx,
            "order",
            {"idle_output_timeout": 45},
            {},
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda fn: fn(), (configure_timeout, configure_idle)))

    overlay = json.loads(tmp_path.joinpath(*_OVERLAY_RELPATH).read_text())
    assert overlay["order"] == {"timeout": 600, "idle_output_timeout": 45}
    assert ctx.config.run_skill.timeout == 600
    assert ctx.config.run_skill.idle_output_timeout == 45
