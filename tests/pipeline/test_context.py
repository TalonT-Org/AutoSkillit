"""Tests for ToolContext dependency injection container."""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path
from typing import cast, get_args, get_type_hints

import pytest

from autoskillit.config import AutomationConfig
from autoskillit.core import (
    AuditAdmissionStoreAuthority,
    AuditAuthorityMaterializer,
    CommittedDispositionResolver,
    ContextAdmissionStoreAuthority,
    GitHubFetcher,
    GitHubReviewPosterProtocol,
)
from autoskillit.pipeline.audit import DefaultAuditLog, FailureRecord
from autoskillit.pipeline.audit_admission_ledger import DefaultAuditAdmissionLedger
from autoskillit.pipeline.context import ToolContext
from autoskillit.pipeline.context_admission_ledger import (
    DefaultContextAdmissionLedger,
)
from autoskillit.pipeline.gate import DefaultGateState
from autoskillit.pipeline.timings import DefaultTimingLog
from autoskillit.pipeline.tokens import DefaultTokenLog
from tests.fakes import (
    FakeManagedHeadlessSessionLineageStore,
    FakeSkillSessionContractStore,
)

pytestmark = [pytest.mark.layer("pipeline"), pytest.mark.small]

_AUDIT_AUTHORITY_MATERIALIZER = cast(AuditAuthorityMaterializer, object())
_COMMITTED_DISPOSITION_RESOLVER = cast(CommittedDispositionResolver, object())


def _ledger(project_dir: Path) -> DefaultContextAdmissionLedger:
    return DefaultContextAdmissionLedger(
        ContextAdmissionStoreAuthority(
            database_path=(
                project_dir / ".autoskillit" / "temp" / "context-admission" / "ledger.sqlite3"
            ).resolve(),
            expected_owner_id=os.getuid(),
        )
    )


def _audit_ledger(project_dir: Path) -> DefaultAuditAdmissionLedger:
    ledger = DefaultAuditAdmissionLedger(
        AuditAdmissionStoreAuthority(
            database_path=(
                project_dir / ".autoskillit" / "temp" / "audit-admission" / "ledger.sqlite3"
            ).resolve(),
            expected_owner_id=os.getuid(),
        )
    )
    ledger.recover_all()
    return ledger


class _UnusedPluginAuthority:
    def acquire_launch_binding(self, *, backend, load_mode):
        raise AssertionError("this test must not acquire a plugin artifact")


def test_tool_context_fields_accessible(tmp_path):
    """ToolContext exposes all expected fields."""
    ctx = ToolContext(
        config=AutomationConfig(),
        audit=DefaultAuditLog(),
        token_log=DefaultTokenLog(),
        timing_log=DefaultTimingLog(),
        gate=DefaultGateState(enabled=True),
        plugin_authority=_UnusedPluginAuthority(),
        runner=None,
        temp_dir=tmp_path / ".autoskillit" / "temp",
        project_dir=tmp_path,
        skill_session_contract_store=FakeSkillSessionContractStore(),
        managed_headless_session_lineage_store=FakeManagedHeadlessSessionLineageStore(),
        context_admission_ledger=_ledger(tmp_path),
        audit_admission_ledger=_audit_ledger(tmp_path),
        audit_authority_materializer=_AUDIT_AUTHORITY_MATERIALIZER,
        committed_disposition_resolver=_COMMITTED_DISPOSITION_RESOLVER,
    )
    assert ctx.gate.enabled is True
    assert isinstance(ctx.plugin_authority, _UnusedPluginAuthority)


def test_tool_context_audit_isolation(tmp_path):
    """Two ToolContext instances have independent AuditLog instances."""
    ctx_a = ToolContext(
        config=AutomationConfig(),
        audit=DefaultAuditLog(),
        token_log=DefaultTokenLog(),
        timing_log=DefaultTimingLog(),
        gate=DefaultGateState(),
        plugin_authority=_UnusedPluginAuthority(),
        runner=None,
        temp_dir=tmp_path / "a" / ".autoskillit" / "temp",
        project_dir=tmp_path / "a",
        skill_session_contract_store=FakeSkillSessionContractStore(),
        managed_headless_session_lineage_store=FakeManagedHeadlessSessionLineageStore(),
        context_admission_ledger=_ledger(tmp_path / "a"),
        audit_admission_ledger=_audit_ledger(tmp_path / "a"),
        audit_authority_materializer=_AUDIT_AUTHORITY_MATERIALIZER,
        committed_disposition_resolver=_COMMITTED_DISPOSITION_RESOLVER,
    )
    ctx_b = ToolContext(
        config=AutomationConfig(),
        audit=DefaultAuditLog(),
        token_log=DefaultTokenLog(),
        timing_log=DefaultTimingLog(),
        gate=DefaultGateState(),
        plugin_authority=_UnusedPluginAuthority(),
        runner=None,
        temp_dir=tmp_path / "b" / ".autoskillit" / "temp",
        project_dir=tmp_path / "b",
        skill_session_contract_store=FakeSkillSessionContractStore(),
        managed_headless_session_lineage_store=FakeManagedHeadlessSessionLineageStore(),
        context_admission_ledger=_ledger(tmp_path / "b"),
        audit_admission_ledger=_audit_ledger(tmp_path / "b"),
        audit_authority_materializer=_AUDIT_AUTHORITY_MATERIALIZER,
        committed_disposition_resolver=_COMMITTED_DISPOSITION_RESOLVER,
    )
    ctx_a.audit.record_failure(
        FailureRecord(
            timestamp="2026-01-01T00:00:00",
            skill_command="/test",
            exit_code=1,
            subtype="error",
            needs_retry=False,
            retry_reason="none",
            stderr="",
        )
    )
    assert len(ctx_a.audit.get_report()) == 1
    assert len(ctx_b.audit.get_report()) == 0


def test_gate_state_replacement(tmp_path):
    """ToolContext allows gate field replacement via plain assignment."""
    ctx = ToolContext(
        config=AutomationConfig(),
        audit=DefaultAuditLog(),
        token_log=DefaultTokenLog(),
        timing_log=DefaultTimingLog(),
        gate=DefaultGateState(enabled=False),
        plugin_authority=_UnusedPluginAuthority(),
        runner=None,
        temp_dir=tmp_path / ".autoskillit" / "temp",
        project_dir=tmp_path,
        skill_session_contract_store=FakeSkillSessionContractStore(),
        managed_headless_session_lineage_store=FakeManagedHeadlessSessionLineageStore(),
        context_admission_ledger=_ledger(tmp_path),
        audit_admission_ledger=_audit_ledger(tmp_path),
        audit_authority_materializer=_AUDIT_AUTHORITY_MATERIALIZER,
        committed_disposition_resolver=_COMMITTED_DISPOSITION_RESOLVER,
    )
    assert ctx.gate.enabled is False
    ctx.gate = DefaultGateState(enabled=True)
    assert ctx.gate.enabled is True


def test_toolcontext_new_optional_fields_default_none(tmp_path):
    """New optional service fields default to None when not provided."""
    ctx = ToolContext(
        config=AutomationConfig(),
        audit=DefaultAuditLog(),
        token_log=DefaultTokenLog(),
        timing_log=DefaultTimingLog(),
        gate=DefaultGateState(enabled=True),
        plugin_authority=_UnusedPluginAuthority(),
        runner=None,
        temp_dir=tmp_path / ".autoskillit" / "temp",
        project_dir=tmp_path,
        skill_session_contract_store=FakeSkillSessionContractStore(),
        managed_headless_session_lineage_store=FakeManagedHeadlessSessionLineageStore(),
        context_admission_ledger=_ledger(tmp_path),
        audit_admission_ledger=_audit_ledger(tmp_path),
        audit_authority_materializer=_AUDIT_AUTHORITY_MATERIALIZER,
        committed_disposition_resolver=_COMMITTED_DISPOSITION_RESOLVER,
    )
    assert ctx.executor is None
    assert ctx.tester is None
    assert ctx.recipes is None
    assert ctx.migrations is None
    assert ctx.db_reader is None
    assert ctx.workspace_mgr is None
    assert ctx.clone_mgr is None
    assert ctx.github_client is None
    assert ctx.github_review_poster is None
    assert ctx.backend is None
    assert ctx.input_contract_resolver is None


def test_tool_context_has_backend_field() -> None:
    """ToolContext dataclass exposes backend as an optional CodingAgentBackend Protocol field."""
    import typing

    from autoskillit.core import CodingAgentBackend
    from autoskillit.pipeline.context import ToolContext

    hints = typing.get_type_hints(ToolContext)
    assert "backend" in hints
    args = typing.get_args(hints["backend"])
    assert CodingAgentBackend in args, (
        f"backend type hint {hints['backend']} does not include CodingAgentBackend Protocol"
    )


def test_toolcontext_optional_fields_all_have_protocol_annotations() -> None:
    """Every field(default=None) on ToolContext must be annotated with a Protocol from core.

    Self-closing: automatically discovers new optional fields without requiring manual
    updates to this test. If a new service field is added to ToolContext with the wrong
    type (e.g. a concrete class), or if it lacks any recognized Protocol annotation,
    this test fails immediately.
    """
    import inspect

    from autoskillit.core import types as core_types

    # Discover all Protocol class names defined in core/types.py
    core_protocol_names = {
        name
        for name, obj in inspect.getmembers(core_types, inspect.isclass)
        if any("Protocol" in str(b) for b in getattr(obj, "__mro__", [])[1:])
        and name != "Protocol"
    }

    # All optional service fields — exactly those declared with field(default=None)
    optional_fields = {
        name: f for name, f in ToolContext.__dataclass_fields__.items() if f.default is None
    }

    violations: list[str] = []
    for field_name, field_obj in optional_fields.items():
        annotation_str = str(field_obj.type)
        if not any(proto in annotation_str for proto in core_protocol_names):
            violations.append(
                f"ToolContext.{field_name}: annotation '{annotation_str}' contains no "
                f"Protocol from core/types.py (known protocols: {sorted(core_protocol_names)})"
            )

    assert not violations, (
        "Optional ToolContext fields must be annotated with core Protocols:\n"
        + "\n".join(violations)
    )


def test_headless_executor_protocol_accepts_timeout() -> None:
    """HeadlessExecutor.run() signature must include optional timeout and stale_threshold."""
    import inspect

    from autoskillit.core import HeadlessExecutor

    sig = inspect.signature(HeadlessExecutor.run)
    params = sig.parameters
    assert "timeout" in params, "HeadlessExecutor.run missing timeout param"
    assert "stale_threshold" in params, "HeadlessExecutor.run missing stale_threshold param"
    # Both must be keyword-only with None default
    assert params["timeout"].default is None
    assert params["stale_threshold"].default is None


def test_headless_executor_protocol_accepts_idle_output_timeout() -> None:
    """HeadlessExecutor.run() signature must include optional idle_output_timeout."""
    import inspect

    from autoskillit.core import HeadlessExecutor

    sig = inspect.signature(HeadlessExecutor.run)
    params = sig.parameters
    assert "idle_output_timeout" in params, (
        "HeadlessExecutor.run missing idle_output_timeout param"
    )
    assert params["idle_output_timeout"].default is None


def test_recipe_repository_protocol_has_rich_methods() -> None:
    """RecipeRepository protocol must expose load_and_validate, validate_from_path, list_all."""
    from autoskillit.core import RecipeRepository

    for method in ("load_and_validate", "validate_from_path", "list_all"):
        assert hasattr(RecipeRepository, method), f"RecipeRepository missing {method}"


def _make_ctx(tmp_path: Path) -> ToolContext:
    """Helper: minimal ToolContext with no optional fields."""
    return ToolContext(
        config=AutomationConfig(),
        audit=DefaultAuditLog(),
        token_log=DefaultTokenLog(),
        timing_log=DefaultTimingLog(),
        gate=DefaultGateState(enabled=True),
        plugin_authority=_UnusedPluginAuthority(),
        runner=None,
        temp_dir=tmp_path / ".autoskillit" / "temp",
        project_dir=tmp_path,
        skill_session_contract_store=FakeSkillSessionContractStore(),
        managed_headless_session_lineage_store=FakeManagedHeadlessSessionLineageStore(),
        context_admission_ledger=_ledger(tmp_path),
        audit_admission_ledger=_audit_ledger(tmp_path),
        audit_authority_materializer=_AUDIT_AUTHORITY_MATERIALIZER,
        committed_disposition_resolver=_COMMITTED_DISPOSITION_RESOLVER,
    )


def test_toolcontext_github_client_annotated_with_protocol():
    """github_client annotation must reference GitHubFetcher protocol."""
    hints = get_type_hints(ToolContext)
    assert GitHubFetcher in get_args(hints["github_client"])


def test_toolcontext_github_review_poster_is_injectable_and_protocol_typed(tmp_path):
    """The authoritative review poster is an optional, replaceable L0 protocol."""

    class _Poster:
        async def post(self, request):
            raise AssertionError(f"unexpected review publication: {request!r}")

    poster = _Poster()
    ctx = dataclasses.replace(_make_ctx(tmp_path), github_review_poster=poster)
    hints = get_type_hints(ToolContext)

    assert ctx.github_review_poster is poster
    assert GitHubReviewPosterProtocol in get_args(hints["github_review_poster"])
    assert isinstance(ctx.github_review_poster, GitHubReviewPosterProtocol)


def test_toolcontext_response_log_annotated_with_mcp_response_store_protocol() -> None:
    """ToolContext.response_log must be annotated with the McpResponseLog protocol.

    response_log uses default_factory (Null Object pattern) not field(default=None),
    so it is excluded from test_toolcontext_optional_fields_all_have_protocol_annotations.
    This test closes that coverage gap.
    """
    from typing import get_type_hints

    from autoskillit.core import McpResponseLog

    hints = get_type_hints(ToolContext)
    assert "response_log" in hints, "ToolContext must have a response_log field"
    assert hints["response_log"] is McpResponseLog, (
        f"ToolContext.response_log must be annotated with McpResponseLog protocol, "
        f"got: {hints['response_log']!r}"
    )


def test_tool_context_has_timing_log_field(tmp_path):
    """ToolContext.timing_log is a non-None TimingLog instance."""
    from autoskillit.core import TimingLog

    ctx = _make_ctx(tmp_path)
    assert ctx.timing_log is not None
    assert isinstance(ctx.timing_log, TimingLog)


@pytest.mark.anyio
async def test_toolcontext_default_background_wired_with_audit(tmp_path):
    """ToolContext background supervisor records failures to ctx.audit."""
    from autoskillit.pipeline.background import DefaultBackgroundSupervisor

    audit = DefaultAuditLog()
    ctx = ToolContext(
        config=AutomationConfig(),
        audit=audit,
        token_log=DefaultTokenLog(),
        timing_log=DefaultTimingLog(),
        gate=DefaultGateState(),
        plugin_authority=_UnusedPluginAuthority(),
        runner=None,
        temp_dir=tmp_path / ".autoskillit" / "temp",
        project_dir=tmp_path,
        skill_session_contract_store=FakeSkillSessionContractStore(),
        managed_headless_session_lineage_store=FakeManagedHeadlessSessionLineageStore(),
        context_admission_ledger=_ledger(tmp_path),
        audit_admission_ledger=_audit_ledger(tmp_path),
        audit_authority_materializer=_AUDIT_AUTHORITY_MATERIALIZER,
        committed_disposition_resolver=_COMMITTED_DISPOSITION_RESOLVER,
    )
    assert isinstance(ctx.background, DefaultBackgroundSupervisor)

    async def _fail() -> None:
        raise RuntimeError("deliberate test failure")

    ctx.background.submit(_fail(), label="test-task")
    await ctx.background.drain()

    records = audit.get_report()
    assert any(r.subtype == "background_exception" for r in records)


# ---------------------------------------------------------------------------
# token_factory field
# ---------------------------------------------------------------------------


def test_tool_context_has_token_factory_field():
    """ToolContext dataclass exposes token_factory as an optional callable Protocol field."""
    import typing

    from autoskillit.core import TokenFactory
    from autoskillit.pipeline.context import ToolContext

    fields = {f.name: f for f in dataclasses.fields(ToolContext)}
    assert "token_factory" in fields
    assert fields["token_factory"].default is None

    hints = typing.get_type_hints(ToolContext)
    assert "token_factory" in hints
    # Annotation must be a union that includes the TokenFactory Protocol (callable)
    args = typing.get_args(hints["token_factory"])
    assert TokenFactory in args, (
        f"token_factory type hint {hints['token_factory']} does not include TokenFactory Protocol"
    )


def test_tool_context_has_fleet_lock_field():
    """ToolContext has a fleet_lock field defaulting to None."""
    from autoskillit.pipeline.context import ToolContext

    field_info = ToolContext.__dataclass_fields__["fleet_lock"]
    assert field_info.default is None


def test_tool_context_recipe_identity_defaults(tmp_path):
    ctx = _make_ctx(tmp_path)
    assert ctx.recipe_name == ""
    assert ctx.recipe_content_hash == ""
    assert ctx.recipe_composite_hash == ""
    assert ctx.recipe_version == ""


# --- Group P-2: project_dir env inheritance ---


def test_toolcontext_has_project_dir_field():
    """ToolContext dataclass has a project_dir field of type Path."""

    from autoskillit.pipeline.context import ToolContext

    field_names = {f.name for f in dataclasses.fields(ToolContext)}
    assert "project_dir" in field_names


# --- Sentinel guard tests ---


def test_toolcontext_raises_typeerror_when_temp_dir_unset(tmp_path):
    with pytest.raises(TypeError, match="temp_dir"):
        ToolContext(
            config=AutomationConfig(),
            audit=DefaultAuditLog(),
            token_log=DefaultTokenLog(),
            timing_log=DefaultTimingLog(),
            gate=DefaultGateState(),
            plugin_authority=_UnusedPluginAuthority(),
            runner=None,
            project_dir=tmp_path,
            skill_session_contract_store=FakeSkillSessionContractStore(),
            managed_headless_session_lineage_store=(FakeManagedHeadlessSessionLineageStore()),
            context_admission_ledger=_ledger(tmp_path),
            audit_admission_ledger=_audit_ledger(tmp_path),
            audit_authority_materializer=_AUDIT_AUTHORITY_MATERIALIZER,
            committed_disposition_resolver=_COMMITTED_DISPOSITION_RESOLVER,
        )


def test_toolcontext_raises_typeerror_when_project_dir_unset(tmp_path):
    with pytest.raises(TypeError, match="project_dir"):
        ToolContext(
            config=AutomationConfig(),
            audit=DefaultAuditLog(),
            token_log=DefaultTokenLog(),
            timing_log=DefaultTimingLog(),
            gate=DefaultGateState(),
            plugin_authority=_UnusedPluginAuthority(),
            runner=None,
            temp_dir=tmp_path / ".autoskillit" / "temp",
            skill_session_contract_store=FakeSkillSessionContractStore(),
            managed_headless_session_lineage_store=(FakeManagedHeadlessSessionLineageStore()),
            context_admission_ledger=_ledger(tmp_path),
        )


def test_toolcontext_requires_il0_managed_lineage_store_protocol(tmp_path):
    from autoskillit.core import ManagedHeadlessSessionLineageStore

    assert (
        get_type_hints(ToolContext)["managed_headless_session_lineage_store"]
        is ManagedHeadlessSessionLineageStore
    )
    with pytest.raises(
        TypeError,
        match="managed_headless_session_lineage_store",
    ):
        ToolContext(
            config=AutomationConfig(),
            audit=DefaultAuditLog(),
            token_log=DefaultTokenLog(),
            timing_log=DefaultTimingLog(),
            gate=DefaultGateState(),
            plugin_authority=_UnusedPluginAuthority(),
            runner=None,
            temp_dir=tmp_path / ".autoskillit" / "temp",
            project_dir=tmp_path,
            skill_session_contract_store=FakeSkillSessionContractStore(),
            context_admission_ledger=_ledger(tmp_path),
            audit_admission_ledger=_audit_ledger(tmp_path),
            audit_authority_materializer=_AUDIT_AUTHORITY_MATERIALIZER,
            committed_disposition_resolver=_COMMITTED_DISPOSITION_RESOLVER,
        )


def test_toolcontext_raises_typeerror_when_context_ledger_unset(tmp_path):
    with pytest.raises(TypeError, match="context_admission_ledger"):
        ToolContext(
            config=AutomationConfig(),
            audit=DefaultAuditLog(),
            token_log=DefaultTokenLog(),
            timing_log=DefaultTimingLog(),
            gate=DefaultGateState(),
            plugin_authority=_UnusedPluginAuthority(),
            runner=None,
            temp_dir=tmp_path / ".autoskillit" / "temp",
            project_dir=tmp_path,
            skill_session_contract_store=FakeSkillSessionContractStore(),
            managed_headless_session_lineage_store=(FakeManagedHeadlessSessionLineageStore()),
        )


def test_toolcontext_raises_typeerror_when_audit_ledger_unset(tmp_path):
    with pytest.raises(TypeError, match="audit_admission_ledger"):
        ToolContext(
            config=AutomationConfig(),
            audit=DefaultAuditLog(),
            token_log=DefaultTokenLog(),
            timing_log=DefaultTimingLog(),
            gate=DefaultGateState(),
            plugin_authority=_UnusedPluginAuthority(),
            runner=None,
            temp_dir=tmp_path / ".autoskillit" / "temp",
            project_dir=tmp_path,
            skill_session_contract_store=FakeSkillSessionContractStore(),
            managed_headless_session_lineage_store=(FakeManagedHeadlessSessionLineageStore()),
            context_admission_ledger=_ledger(tmp_path),
        )


def test_toolcontext_accepts_explicit_path_fields(tmp_path):
    ctx = ToolContext(
        config=AutomationConfig(),
        audit=DefaultAuditLog(),
        token_log=DefaultTokenLog(),
        timing_log=DefaultTimingLog(),
        gate=DefaultGateState(),
        plugin_authority=_UnusedPluginAuthority(),
        runner=None,
        temp_dir=tmp_path / ".autoskillit" / "temp",
        project_dir=tmp_path,
        skill_session_contract_store=FakeSkillSessionContractStore(),
        managed_headless_session_lineage_store=FakeManagedHeadlessSessionLineageStore(),
        context_admission_ledger=_ledger(tmp_path),
        audit_admission_ledger=_audit_ledger(tmp_path),
        audit_authority_materializer=_AUDIT_AUTHORITY_MATERIALIZER,
        committed_disposition_resolver=_COMMITTED_DISPOSITION_RESOLVER,
    )
    assert ctx.temp_dir == tmp_path / ".autoskillit" / "temp"
    assert ctx.project_dir == tmp_path


def test_tool_context_has_quota_refresh_task_field():
    """ToolContext must have a quota_refresh_task field defaulting to None."""
    fields = {f.name: f for f in dataclasses.fields(ToolContext)}
    assert "quota_refresh_task" in fields
    assert fields["quota_refresh_task"].default is None


def test_tool_context_has_input_contract_resolver_field() -> None:
    """ToolContext dataclass exposes input_contract_resolver as an optional Protocol field."""
    import typing

    from autoskillit.core import InputContractResolver
    from autoskillit.pipeline.context import ToolContext

    fields = {f.name: f for f in dataclasses.fields(ToolContext)}
    assert "input_contract_resolver" in fields
    assert fields["input_contract_resolver"].default is None

    hints = typing.get_type_hints(ToolContext)
    assert "input_contract_resolver" in hints
    args = typing.get_args(hints["input_contract_resolver"])
    assert InputContractResolver in args, (
        f"input_contract_resolver type hint {hints['input_contract_resolver']} "
        f"does not include InputContractResolver Protocol"
    )


def test_toolcontext_protocol_fields_documented_in_docstring() -> None:
    """Every protocol-typed field on ToolContext must appear in the Fields docstring."""
    import inspect
    from typing import Union, get_args, get_origin, get_type_hints

    from autoskillit.pipeline.context import ToolContext

    hints = get_type_hints(ToolContext)
    docstring = inspect.getdoc(ToolContext) or ""

    # Extract field names mentioned in the docstring Fields section
    lines = docstring.split("\n")
    fields_start = None
    for i, line in enumerate(lines):
        if line.strip() == "Fields":
            for j in range(i + 1, len(lines)):
                candidate = lines[j].strip()
                if candidate and not all(c == "-" for c in candidate):
                    fields_start = j
                    break
            break
    assert fields_start is not None, "ToolContext docstring missing Fields section"

    documented = set()
    for line in lines[fields_start:]:
        stripped = line.strip()
        if not stripped:
            continue
        if ":" in stripped and not line.startswith(" "):
            field_name = stripped.split(":")[0].strip()
            documented.add(field_name)

    # Find all protocol-typed fields (those importing from core.types)
    protocol_module_prefixes = ("autoskillit.core.types.",)

    def _is_protocol_type(annotation) -> bool:
        """Check if an annotation references a Protocol from core.types."""
        if get_origin(annotation) is Union:
            for arg in get_args(annotation):
                if arg is type(None):
                    continue
                if _is_protocol_type(arg):
                    return True
            return False
        return hasattr(annotation, "__module__") and any(
            annotation.__module__.startswith(p) for p in protocol_module_prefixes
        )

    protocol_fields = []
    for f in dataclasses.fields(ToolContext):
        ann = hints.get(f.name)
        if ann is not None and _is_protocol_type(ann):
            protocol_fields.append(f.name)

    missing = sorted(set(protocol_fields) - documented)
    assert not missing, f"Protocol-typed fields missing from ToolContext docstring: {missing}"
