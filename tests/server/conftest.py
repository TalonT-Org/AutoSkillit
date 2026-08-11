"""Shared fixtures for tests/server/."""

from __future__ import annotations

import json
import os
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
import structlog.contextvars
import structlog.testing

if TYPE_CHECKING:
    from autoskillit.pipeline import ToolContext
    from autoskillit.pipeline.timings import DefaultTimingLog


@pytest.fixture(autouse=True)
def _reset_server_state():
    """Reset module-level server state around each server test.

    Tests that call _initialize() directly set _state._ctx to a mock without
    cleanup. Subsequent tests in the same xdist worker then find a stale mock
    _ctx. Bare FastMCP Client lifespans can then boot from that stale context
    and pre-reveal session-scoped tags, making visibility tests order-dependent.

    Clear before and after each test instead of restoring the previous value,
    because the previous value may itself be leaked state from an earlier test.
    """
    from autoskillit.server import _state
    from autoskillit.server._recipe_generation import get_recipe_generation_store

    _state._ctx = None
    _state._startup_ready = None
    get_recipe_generation_store().clear()
    yield
    _state._ctx = None
    _state._startup_ready = None
    get_recipe_generation_store().clear()


@pytest.fixture(autouse=True)
def _reset_mcp_tags():
    """Reset MCP tag visibility to default (kitchen disabled) before each test.

    The mcp singleton is process-global. Each mcp.enable()/disable() call appends
    a Visibility transform to an internal list — the list never shrinks. Over a
    full test suite (11k+ tests), thousands of accumulated transforms can cause
    version-dependent ordering issues in FastMCP's "last match wins" evaluation.

    Fix: truncate the transforms list back to its fresh state, then explicitly
    disable all gated tags — matching the server/__init__.py import-time baseline
    and preventing orchestrator-path tests from leaking fleet-dispatch or
    kitchen-core enables into subsequent fleet visibility tests.
    """
    from autoskillit.core import ALL_VISIBILITY_TAGS
    from autoskillit.server import mcp

    mcp._transforms.clear()
    for tag in sorted(ALL_VISIBILITY_TAGS):
        mcp.disable(tags={tag})
    from autoskillit.server import _state
    from autoskillit.server.tools._serve_helpers import reset_session_serve_overrides

    if _state._ctx is not None:
        reset_session_serve_overrides(_state._ctx)
    yield
    mcp._transforms.clear()
    for tag in sorted(ALL_VISIBILITY_TAGS):
        mcp.disable(tags={tag})
    from autoskillit.server import _state
    from autoskillit.server.tools._serve_helpers import reset_session_serve_overrides

    if _state._ctx is not None:
        reset_session_serve_overrides(_state._ctx)


@pytest.fixture()
def kitchen_enabled():
    """Enable the kitchen tag on the MCP server for the duration of the test."""
    from autoskillit.core import ALL_VISIBILITY_TAGS
    from autoskillit.server import mcp

    mcp._transforms.clear()
    for tag in sorted(ALL_VISIBILITY_TAGS):
        mcp.disable(tags={tag})
    mcp.enable(tags={"kitchen"})
    yield
    mcp._transforms.clear()
    for tag in sorted(ALL_VISIBILITY_TAGS):
        mcp.disable(tags={tag})


@pytest.fixture()
def headless_enabled():
    """Enable the headless tag on the MCP server for the duration of the test."""
    from autoskillit.core import ALL_VISIBILITY_TAGS
    from autoskillit.server import mcp

    mcp._transforms.clear()
    for tag in sorted(ALL_VISIBILITY_TAGS):
        mcp.disable(tags={tag})
    mcp.enable(tags={"headless"})
    yield
    mcp._transforms.clear()
    for tag in sorted(ALL_VISIBILITY_TAGS):
        mcp.disable(tags={tag})


def assert_step_timed(timing_log: DefaultTimingLog, step_name: str) -> None:
    assert any(e["step_name"] == step_name for e in timing_log.get_report())


def assert_no_timing(timing_log: DefaultTimingLog) -> None:
    assert timing_log.get_report() == []


def _set_mock_kitchen_transition(ctx: MagicMock, *, kitchen_id: str = "") -> None:
    """Give a mock context the typed transition state guaranteed by make_context()."""
    from threading import RLock

    from autoskillit.pipeline import closed_kitchen_open_state, new_kitchen_open_state

    closed_state = closed_kitchen_open_state()
    ctx.kitchen_transition_lock = RLock()
    ctx.kitchen_open_state = (
        new_kitchen_open_state(
            kitchen_id=kitchen_id,
            context_id=closed_state.context_id,
        )
        if kitchen_id
        else closed_state
    )


def _make_mock_ctx(config=None) -> MagicMock:
    """Return a minimal mock ToolContext with a gate."""
    from copy import deepcopy
    from threading import RLock

    from autoskillit.config import AutomationConfig, OutputBudgetConfig, QuotaGuardConfig
    from autoskillit.core import InstallationVersion, SkillResolver
    from autoskillit.pipeline import NoActiveRecipe
    from autoskillit.server._factory import make_recipe_execution

    gate = MagicMock()
    gate.enabled = False
    ctx = MagicMock()
    ctx.gate = gate
    ctx.config = (
        deepcopy(config) if config is not None else AutomationConfig(features={"fleet": True})
    )
    ctx._baseline_config = deepcopy(ctx.config)
    ctx._session_config_overrides = {}
    ctx.temp_dir = (
        Path(__file__).resolve().parents[2] / ".autoskillit" / "temp" / "tests" / uuid4().hex
    )
    ctx.project_dir = ctx.temp_dir / "project"
    ctx.project_dir.mkdir(parents=True, exist_ok=True)
    ctx.skill_resolver = MagicMock(spec=SkillResolver)
    ctx.skill_resolver.resolve_effective.return_value = None
    ctx.kitchen_id = f"test-{uuid4().hex}"
    ctx.kitchen_process_identity = None
    ctx.kitchen_tracker_key = None
    ctx.tracker_leases = {}
    ctx.tracker_leases_lock = RLock()
    ctx.config.output_budget = OutputBudgetConfig()
    ctx.config.quota_guard = QuotaGuardConfig()
    ctx.config.subsets.disabled = []  # REQ-VIS-008: no subsets disabled by default
    ctx.active_recipe_ingredients = None
    ctx.gate_infrastructure_ready = False
    _set_mock_kitchen_transition(ctx)
    ctx.recipe_execution_lock = RLock()
    ctx.recipe_initialization_state = NoActiveRecipe()
    ctx.recipe_execution_factory = make_recipe_execution
    ctx.audit_admission_ledger.create_or_get_installation.return_value = InstallationVersion(
        "test-installation"
    )
    # Issue #4399: open_kitchen's `_use_global_enable` branch (formerly
    # `_skip_notify`) now sends `await ctx.send_notification(...)` after global
    # re-enables. A bare MagicMock is not awaitable, so provide a default
    # AsyncMock for any caller that exercises that branch.
    ctx.send_notification = AsyncMock()
    return ctx


_SUCCESS_JSON = (
    '{"type": "result", "subtype": "success", "is_error": false,'
    ' "result": "done", "session_id": "s1"}'
)


@pytest.fixture(autouse=True)
def _suppress_nudge(monkeypatch):
    """Prevent the contract-nudge gate from firing on mock EARLY_STOP results.

    Server command-building tests use mock results that lack the per-invocation
    completion marker, causing _retry_fsm to classify them as EARLY_STOP. With
    the widened nudge gate (no provider_extras guard), the nudge fires and
    appends an extra runner call that breaks call_args_list[-1] assertions.
    """

    async def _noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        "autoskillit.execution.headless._headless_execute._attempt_contract_nudge", _noop
    )


@pytest.fixture(autouse=True)
def _suppress_pre_session_index(monkeypatch):
    """Prevent validate_pre_session_index from consuming mock runner results.

    On hosts where /tmp/.git exists as a directory (e.g. WSL2),
    is_git_main_checkout(Path("/tmp")) returns True, causing an unexpected
    subprocess call that shifts the mock result queue.
    """

    async def _noop(*_args, **_kwargs):
        return False

    monkeypatch.setattr(
        "autoskillit.execution.headless._headless_execute.validate_pre_session_index", _noop
    )


@pytest.fixture(autouse=True)
def _patch_kitchen_reaper(monkeypatch):
    """Neutralize reaper calls inside _open_kitchen_handler for unit tests.

    _open_kitchen_handler now calls discover_campaign_state_files and
    reap_stale_dispatches_async. Existing kitchen handler tests rely on
    filesystem absence to make these no-ops; this fixture makes that
    guarantee explicit and stable regardless of host filesystem state.
    """
    monkeypatch.setattr(
        "autoskillit.server.tools.tools_kitchen.discover_campaign_state_files",
        lambda _project_dir: [],
    )

    async def _noop_reaper(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        "autoskillit.server.tools.tools_kitchen.reap_stale_dispatches_async", _noop_reaper
    )


@pytest.fixture
def build_ctx(tmp_path):
    """Factory: build_ctx(**overrides) → minimal ToolContext with overrides applied."""
    from autoskillit.config.settings import AutomationConfig
    from autoskillit.core import (
        AuditAdmissionStoreAuthority,
        ContextAdmissionStoreAuthority,
        SkillResolver,
    )
    from autoskillit.pipeline.audit import DefaultAuditLog
    from autoskillit.pipeline.audit_admission_ledger import DefaultAuditAdmissionLedger
    from autoskillit.pipeline.context import ToolContext
    from autoskillit.pipeline.context_admission_ledger import (
        DefaultContextAdmissionLedger,
    )
    from autoskillit.pipeline.gate import DefaultGateState
    from autoskillit.pipeline.run_skill_completion import (
        DefaultRunSkillCompletionAuthority,
    )
    from autoskillit.pipeline.timings import DefaultTimingLog
    from autoskillit.pipeline.tokens import DefaultTokenLog
    from autoskillit.server._audit_authority_materializer import (
        DefaultAuditAuthorityMaterializer,
        DefaultCommittedDispositionResolver,
    )
    from tests.fakes import (
        FakeLaunchResolver,
        FakeManagedHeadlessSessionLineageStore,
        FakePluginArtifactAuthority,
        FakeSkillSessionContractStore,
    )

    owned_authorities = []
    context_count = 0

    def _factory(**overrides):
        nonlocal context_count
        context_count += 1
        if "plugin_authority" in overrides:
            plugin_authority = overrides.pop("plugin_authority")
        else:
            plugin_authority = FakePluginArtifactAuthority(tmp_path)
            owned_authorities.append(plugin_authority)
        audit_admission_ledger = DefaultAuditAdmissionLedger(
            AuditAdmissionStoreAuthority(
                database_path=(
                    tmp_path
                    / ".autoskillit"
                    / "temp"
                    / f"audit-admission-{context_count}"
                    / "ledger.sqlite3"
                ).resolve(),
                expected_owner_id=os.getuid(),
            )
        )
        audit_admission_ledger.recover_all()
        ctx = ToolContext(
            config=AutomationConfig(features={"fleet": True}),
            audit=DefaultAuditLog(),
            token_log=DefaultTokenLog(),
            timing_log=DefaultTimingLog(),
            gate=DefaultGateState(enabled=False),
            plugin_authority=plugin_authority,
            runner=None,
            launch_resolver=FakeLaunchResolver(),
            temp_dir=tmp_path / ".autoskillit" / "temp",
            project_dir=tmp_path,
            skill_session_contract_store=FakeSkillSessionContractStore(),
            managed_headless_session_lineage_store=(FakeManagedHeadlessSessionLineageStore()),
            context_admission_ledger=DefaultContextAdmissionLedger(
                ContextAdmissionStoreAuthority(
                    database_path=(
                        tmp_path
                        / ".autoskillit"
                        / "temp"
                        / f"context-admission-{context_count}"
                        / "ledger.sqlite3"
                    ).resolve(),
                    expected_owner_id=os.getuid(),
                )
            ),
            audit_admission_ledger=audit_admission_ledger,
            audit_authority_materializer=DefaultAuditAuthorityMaterializer(audit_admission_ledger),
            committed_disposition_resolver=DefaultCommittedDispositionResolver(
                audit_admission_ledger
            ),
            run_skill_completion=DefaultRunSkillCompletionAuthority(),
        )
        ctx.skill_resolver = MagicMock(spec=SkillResolver)
        ctx.skill_resolver.resolve_effective.return_value = None
        for field_name, value in overrides.items():
            setattr(ctx, field_name, value)
        return ctx

    yield _factory
    for plugin_authority in owned_authorities:
        plugin_authority.close()


@pytest.fixture
def build_ctx_open(build_ctx):
    """build_ctx variant with gate open — returns a factory callable like build_ctx."""
    from autoskillit.pipeline.gate import DefaultGateState

    def _factory(**overrides):
        ctx = build_ctx(**overrides)
        ctx.gate = DefaultGateState(enabled=True)
        return ctx

    return _factory


@pytest.fixture()
def forbid_artifact_reads(monkeypatch: pytest.MonkeyPatch):
    """Return an arming callable that fails any persisted-artifact read.

    Shared by attested-path tests that must prove a value came from a tool
    response, never from re-reading the recipe artifact off disk.
    """
    import autoskillit.server.tools.tools_recipe as tools_recipe

    def _forbidden(*_args, **_kwargs):
        raise AssertionError("credential must come from responses, not payload.json")

    return lambda: monkeypatch.setattr(tools_recipe, "load_recipe_artifact", _forbidden)


_READY_RECIPE_ENVELOPE = "remediation"
_READY_RECIPE_ATTESTED_STEP = "investigate"
_READY_RECIPE_OVERRIDES = {
    "issue_url": "https://github.com/TalonT-Org/AutoSkillit/issues/4411",
    "task_description": "test task",
}


@dataclass(frozen=True)
class ReadyRecipeContext:
    """A real attested ToolContext plus the delivered credential and step body.

    ``tool_ctx.recipe_initialization_state`` is a genuine ``ReadyRecipe`` —
    built via the production ``open_kitchen`` -> ``complete_recipe_initialization``
    flow, not the low-level ``bind_recipe`` -> ``build_recipe_execution_snapshot``
    -> ``install_recipe_execution`` chain (those functions precondition on
    ``InitializingRecipe`` -> ``ReadyRecipe`` staging that only the production
    flow performs).
    """

    tool_ctx: ToolContext
    receipt: dict[str, Any]
    step_body: dict[str, Any]
    step_name: str = _READY_RECIPE_ATTESTED_STEP

    @property
    def credential(self) -> dict[str, Any]:
        from autoskillit.core import RECIPE_EXECUTION_CREDENTIAL_WIRE_KEY

        return self.receipt[RECIPE_EXECUTION_CREDENTIAL_WIRE_KEY]

    @property
    def template_digest(self) -> str:
        return self.credential["invocation_template_digests"][self.step_name]

    @property
    def with_args(self) -> dict[str, Any]:
        return self.step_body["with"]


@pytest.fixture
async def tool_ctx_ready_recipe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    tool_ctx_kitchen_open,
    forbid_artifact_reads,
) -> ReadyRecipeContext:
    """A ToolContext driven through real production initialization into ReadyRecipe.

    Required entry point for attested-path server tests: ``tool_ctx_kitchen_open``
    alone leaves ``recipe_initialization_state = NoActiveRecipe()`` and cannot
    reach the attested ``run_skill`` branch. This fixture drives the same
    ``open_kitchen`` -> credit sections -> pull step -> ``complete_recipe_initialization``
    flow test_attestation_delivery_reachability.py exercises, promoted to a
    shared fixture, so tests use a genuinely attested context rather than a
    mock of one. The credential-artifact-read poison is armed after the step
    pull completes (get_recipe_section legitimately reads the artifact to
    serve pages) and stays armed for the returned context's remaining calls.
    """
    from autoskillit.recipe import _api_cache
    from autoskillit.recipe._api_cache import LoadCache
    from autoskillit.server.tools.tools_recipe import complete_recipe_initialization
    from tests.server._helpers import (
        _credit_initialization_sections,
        _open_kitchen_patched,
        _pull_step_section,
    )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_api_cache, "_LOAD_CACHE", LoadCache())

    envelope = await _open_kitchen_patched(
        _READY_RECIPE_ENVELOPE, _READY_RECIPE_OVERRIDES, monkeypatch
    )
    assert envelope["success"] is True
    assert envelope["delivery_bound_spill"] is True

    await _credit_initialization_sections(envelope)
    step_body = await _pull_step_section(envelope, _READY_RECIPE_ATTESTED_STEP)
    forbid_artifact_reads()
    receipt = json.loads(await complete_recipe_initialization(envelope["initialization_id"]))
    assert receipt["success"] is True

    return ReadyRecipeContext(tool_ctx=tool_ctx_kitchen_open, receipt=receipt, step_body=step_body)


@contextmanager
def assert_all_logs_carry_context(*expected_keys: str) -> Generator[list[dict], None, None]:
    with structlog.testing.capture_logs(
        processors=[structlog.contextvars.merge_contextvars]
    ) as logs:
        yield logs
    for entry in logs:
        for key in expected_keys:
            assert key in entry, f"Log record missing {key!r}: {entry}"
