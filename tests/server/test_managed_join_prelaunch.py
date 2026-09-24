"""Managed-join prelaunch issuance and durable revalidation tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from autoskillit.core import ManagedJoinRefusalReason
from tests.execution.backends._codex_fixtures import (
    installed_catalog,
    managed_source_home,
    use_bundled_catalog,
)

if TYPE_CHECKING:
    from autoskillit.core import SemanticAdaptationContext
    from autoskillit.execution.backends import CodexBackend
    from autoskillit.server._managed_join_attestation import (
        DefaultManagedJoinAttestationAuthority,
        ManagedJoinRecordStore,
    )

pytestmark = [pytest.mark.layer("server"), pytest.mark.small, pytest.mark.model_contract]


@pytest.mark.parametrize(
    ("configured_model", "expected_model"),
    [("gpt-6-luna", "gpt-6-luna"), ("gpt-6-sol", "gpt-6-sol")],
)
def test_prelaunch_issuance_admits_native_gpt6_models_with_catalog_effort(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    configured_model: str,
    expected_model: str,
) -> None:
    from autoskillit.core import SemanticAdaptationContext
    from autoskillit.execution.backends import CodexBackend
    from autoskillit.server.managed_join_prelaunch import prepare_managed_join_context

    source_home, raw_catalog = managed_source_home(tmp_path)
    use_bundled_catalog(monkeypatch, raw_catalog)
    context = prepare_managed_join_context(
        backend=CodexBackend(source_codex_home=source_home),
        configured_model=configured_model,
        state_root=tmp_path / "state",
        parent_id=f"native-{configured_model}",
        launch_context="interactive",
    )

    assert isinstance(context, SemanticAdaptationContext)
    attestation = context.managed_join_attestation
    assert attestation is not None
    assert attestation.resolved_model == expected_model
    assert attestation.resolved_reasoning_effort == "medium"


@pytest.mark.parametrize(
    "retired_model",
    ["gpt-5.6-luna", "gpt-5.6-sol", "gpt-5.6-terra"],
)
def test_prelaunch_issuance_refuses_retired_native_models_before_attestation(
    tmp_path: Path,
    retired_model: str,
) -> None:
    from autoskillit.execution.backends import CodexBackend
    from autoskillit.server.managed_join_prelaunch import (
        ManagedJoinIssuanceRefusal,
        prepare_managed_join_context,
    )

    source_home, _ = managed_source_home(tmp_path)
    state_root = tmp_path / "state"
    refusal = prepare_managed_join_context(
        backend=CodexBackend(source_codex_home=source_home),
        configured_model=retired_model,
        state_root=state_root,
        parent_id=f"retired-{retired_model}",
        launch_context="interactive",
    )

    assert isinstance(refusal, ManagedJoinIssuanceRefusal)
    assert not (state_root / ".autoskillit").exists()


def test_prelaunch_issuance_produces_verifiable_context_from_production_digests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.core import (
        CODEX_EFFORT_MAPPING,
        JoinSpec,
        SemanticAdaptationContext,
        SkillSemanticPlan,
    )
    from autoskillit.execution.backends import CodexBackend
    from autoskillit.execution.backends._codex_catalog import project_codex_catalog
    from autoskillit.execution.backends._codex_hooks import managed_codex_route_digest
    from autoskillit.hook_registry import HOOK_REGISTRY_HASH
    from autoskillit.hooks._session_binding import resolve_channel_dir
    from autoskillit.server.managed_join_prelaunch import prepare_managed_join_context

    source_home, raw_catalog = managed_source_home(tmp_path)
    state_root = tmp_path / "state"
    backend = CodexBackend(source_codex_home=source_home)
    use_bundled_catalog(monkeypatch, raw_catalog)
    (source_home / "models_cache.json").write_text("source cache is not issuance authority")

    context = prepare_managed_join_context(
        backend=backend,
        configured_model="haiku",
        state_root=state_root,
        parent_id="abc123",
        launch_context="interactive",
    )

    assert isinstance(context, SemanticAdaptationContext)
    attestation = context.managed_join_attestation
    assert attestation is not None
    expected_projection = project_codex_catalog(
        raw_catalog,
        expected_model="gpt-6-luna",
        expected_reasoning_effort=CODEX_EFFORT_MAPPING["haiku"],
    )
    assert attestation.provenance == "autoskillit-server"
    assert attestation.parent_session_id == "abc123"
    assert attestation.launch_context == "interactive"
    assert attestation.resolved_model == "gpt-6-luna"
    assert attestation.resolved_reasoning_effort == CODEX_EFFORT_MAPPING["haiku"]
    assert attestation.hook_registry_digest == HOOK_REGISTRY_HASH
    assert attestation.fixed_batch_tool_registry_digest == managed_codex_route_digest()
    assert attestation.codex_catalog_digest == expected_projection.projected_sha256.removeprefix(
        "sha256:"
    )
    assert context.managed_codex_catalog == expected_projection.canonical_projected_bytes
    assert "managed_codex_catalog" not in context.canonical_payload
    assert (resolve_channel_dir(state_root) / "managed_join_attestation_abc123.json").is_file()
    plan = SkillSemanticPlan(schema_version=1, join=JoinSpec(required=True))
    assert backend.adapt_skill_semantics(plan, context).unsupported_operation is None


def test_prelaunch_issuance_refuses_unresolvable_model_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.execution.backends import ClaudeCodeBackend, CodexBackend
    from autoskillit.server.managed_join_prelaunch import (
        ManagedJoinIssuanceRefusal,
        prepare_managed_join_context,
    )

    state_root = tmp_path / "state"
    source_home, _ = managed_source_home(tmp_path / "no-default")
    missing_default_catalog = installed_catalog()
    models = missing_default_catalog["models"]
    assert isinstance(models, list)
    sol = models[0]
    assert isinstance(sol, dict)
    sol.pop("default_reasoning_level")
    raw_catalog = json.dumps(missing_default_catalog).encode("utf-8")
    use_bundled_catalog(monkeypatch, raw_catalog)
    missing_default = prepare_managed_join_context(
        backend=CodexBackend(source_codex_home=source_home),
        configured_model="gpt-6-sol",
        state_root=state_root,
        parent_id="missing-default",
        launch_context="interactive",
    )
    assert isinstance(missing_default, ManagedJoinIssuanceRefusal)
    assert "gpt-6-sol" in missing_default.reason

    absent_home, absent_catalog = managed_source_home(tmp_path / "absent", include_sol=False)
    use_bundled_catalog(monkeypatch, absent_catalog)
    absent_model = prepare_managed_join_context(
        backend=CodexBackend(source_codex_home=absent_home),
        configured_model="gpt-6-sol",
        state_root=state_root,
        parent_id="absent-model",
        launch_context="interactive",
    )
    assert isinstance(absent_model, ManagedJoinIssuanceRefusal)
    assert "gpt-6-sol" in absent_model.reason

    unsupported = prepare_managed_join_context(
        backend=ClaudeCodeBackend(),
        configured_model="haiku",
        state_root=state_root,
        parent_id="unsupported",
        launch_context="interactive",
    )
    assert isinstance(unsupported, ManagedJoinIssuanceRefusal)
    assert "managed fixed-batch route" in unsupported.reason
    assert not (state_root / ".autoskillit").exists()


def test_prelaunch_issuance_refuses_malformed_catalog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A catalog missing its ``models`` list surfaces a clear ``ManagedJoinIssuanceRefusal``."""
    import json

    from autoskillit.execution.backends import CodexBackend
    from autoskillit.server.managed_join_prelaunch import (
        ManagedJoinIssuanceRefusal,
        prepare_managed_join_context,
    )

    source_home = tmp_path / "source"
    source_home.mkdir(parents=True)
    (source_home / "models_cache.json").write_text(
        json.dumps({"version": "missing-models-list"}), encoding="utf-8"
    )
    use_bundled_catalog(monkeypatch, b'{"version":"missing-models-list"}')

    # ``gpt-6-luna`` is a valid Codex model id but is not in the
    # CODEX_EFFORT_MAPPING shortcut table, so ``resolve_managed_parent_identity``
    # falls through to the catalog-parsing branch — which is the site guarded
    # against a missing ``models`` list.
    refusal = prepare_managed_join_context(
        backend=CodexBackend(source_codex_home=source_home),
        configured_model="gpt-6-luna",
        state_root=tmp_path / "state",
        parent_id="malformed-catalog",
        launch_context="interactive",
    )
    assert isinstance(refusal, ManagedJoinIssuanceRefusal)
    assert "models" in refusal.reason


def test_prelaunch_issuance_converts_bundled_acquisition_errors_to_refusal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.execution.backends import CodexBackend, _codex_managed_route
    from autoskillit.execution.backends._codex_catalog import (
        CodexCatalogAcquisitionError,
    )
    from autoskillit.server.managed_join_prelaunch import (
        ManagedJoinIssuanceRefusal,
        prepare_managed_join_context,
    )

    monkeypatch.setattr(_codex_managed_route.shutil, "which", lambda _binary: "/usr/bin/codex")

    def fail_acquisition(*args, **kwargs):
        del args, kwargs
        raise CodexCatalogAcquisitionError("deadline_exceeded")

    monkeypatch.setattr(
        _codex_managed_route,
        "acquire_bundled_codex_catalog",
        fail_acquisition,
    )

    refusal = prepare_managed_join_context(
        backend=CodexBackend(source_codex_home=tmp_path / "unused-source"),
        configured_model="haiku",
        state_root=tmp_path,
        parent_id="acquisition-failed",
        launch_context="interactive",
    )

    assert isinstance(refusal, ManagedJoinIssuanceRefusal)
    assert refusal.reason == "deadline_exceeded"


def test_authority_atomically_caches_complete_catalog_context() -> None:
    import hashlib

    from autoskillit.core import CODEX_MODEL_ALIASES
    from autoskillit.server._managed_join_attestation import (
        DefaultManagedJoinAttestationAuthority,
    )

    catalog = b'{"models":[]}'
    authority = DefaultManagedJoinAttestationAuthority()
    context = authority.issue(
        backend="codex",
        launch_context="direct",
        parent_session_id="complete-context",
        direct_tool_mode=True,
        resolved_model=CODEX_MODEL_ALIASES["sonnet"],
        resolved_reasoning_effort="high",
        codex_catalog_digest=hashlib.sha256(catalog).hexdigest(),
        managed_codex_catalog=catalog,
        fixed_batch_tool_registry_digest="a" * 64,
        hook_registry_digest="b" * 64,
        skill_load_applies=True,
        guards_apply=True,
    )

    assert context.managed_codex_catalog == catalog
    assert (
        authority.verify(context, backend="codex", parent_session_id="complete-context") is context
    )


def _prepared_managed_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[CodexBackend, ManagedJoinRecordStore, Path, SemanticAdaptationContext]:
    from autoskillit.core import CODEX_HOME_ENV_VAR, SemanticAdaptationContext
    from autoskillit.execution.backends import CodexBackend
    from autoskillit.server._managed_join_attestation import ManagedJoinRecordStore
    from autoskillit.server.managed_join_prelaunch import prepare_managed_join_context

    (tmp_path / ".autoskillit").mkdir(parents=True, exist_ok=True)
    source_home, raw_catalog = managed_source_home(tmp_path)
    use_bundled_catalog(monkeypatch, raw_catalog)
    state_root = tmp_path / "state"
    backend = CodexBackend(source_codex_home=source_home)
    context = prepare_managed_join_context(
        backend=backend,
        configured_model="haiku",
        state_root=state_root,
        parent_id="abc123",
        launch_context="interactive",
    )
    assert isinstance(context, SemanticAdaptationContext)
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text(
        '[mcp_servers.autoskillit]\ncommand = "autoskillit"\n', encoding="utf-8"
    )
    backend.configure_managed_session_dir(
        home, adaptation_context=context, route="interactive-parent"
    )
    (source_home / "models_cache.json").unlink()
    monkeypatch.setenv(CODEX_HOME_ENV_VAR, str(home))
    return backend, ManagedJoinRecordStore(state_root), home, context


def _issue_context(
    authority: DefaultManagedJoinAttestationAuthority,
    *,
    model: str = "gpt-6-luna",
    direct_tool_mode: bool = True,
) -> SemanticAdaptationContext:
    return authority.issue(
        backend="codex",
        launch_context="interactive",
        parent_session_id="abc123",
        direct_tool_mode=direct_tool_mode,
        resolved_model=model,
        resolved_reasoning_effort="medium",
        codex_catalog_digest="a" * 64,
        fixed_batch_tool_registry_digest="b" * 64,
        hook_registry_digest="c" * 64,
        skill_load_applies=True,
        guards_apply=True,
    )


def test_server_authority_loads_and_revalidates_prelaunch_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autoskillit.core import SemanticAdaptationContext
    from autoskillit.server._managed_join_attestation import DefaultManagedJoinAttestationAuthority

    backend, record_store, _, context = _prepared_managed_home(tmp_path, monkeypatch)
    original_reader = type(backend).read_managed_session_catalog
    catalog_reads = 0

    def count_catalog_read(self, generated_home):
        nonlocal catalog_reads
        catalog_reads += 1
        return original_reader(self, generated_home)

    monkeypatch.setattr(type(backend), "read_managed_session_catalog", count_catalog_read)
    authority = DefaultManagedJoinAttestationAuthority(record_store=record_store, backend=backend)

    loaded = authority.find_verified_context(backend="codex", parent_session_id="abc123")
    assert isinstance(loaded, SemanticAdaptationContext)
    assert loaded.managed_join_attestation == context.managed_join_attestation
    assert loaded.managed_codex_catalog == context.managed_codex_catalog
    assert authority.verify(loaded, backend="codex", parent_session_id="abc123") is loaded
    assert catalog_reads == 1
    assert authority.find_verified_context(backend="codex", parent_session_id="abc123") is loaded
    assert catalog_reads == 2


def test_cached_context_refuses_later_home_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autoskillit.core import ManagedJoinVerificationRefusal, SemanticAdaptationContext
    from autoskillit.server._managed_join_attestation import DefaultManagedJoinAttestationAuthority

    backend, record_store, home, _ = _prepared_managed_home(tmp_path, monkeypatch)
    authority = DefaultManagedJoinAttestationAuthority(record_store=record_store, backend=backend)
    first = authority.find_verified_context(backend="codex", parent_session_id="abc123")
    assert isinstance(first, SemanticAdaptationContext)

    config_path = home / "config.toml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "join_stop_guard", "removed_join_stop_guard"
        ),
        encoding="utf-8",
    )
    second = authority.find_verified_context(backend="codex", parent_session_id="abc123")
    assert isinstance(second, ManagedJoinVerificationRefusal)
    assert second.reason is ManagedJoinRefusalReason.HOME_DRIFT
    assert "missing guards: join_stop_guard" in "; ".join(second.detail)


_RELOAD_CASES = (
    ("model_changed", ManagedJoinRefusalReason.HOME_DRIFT, "wrong resolved model (attested"),
    ("home_unset", ManagedJoinRefusalReason.HOME_UNAVAILABLE, "CODEX_HOME is unset"),
    ("catalog_missing", ManagedJoinRefusalReason.HOME_UNREADABLE, "catalog is unreadable"),
    ("catalog_tampered", ManagedJoinRefusalReason.HOME_DRIFT, "managed Codex catalog is invalid"),
    ("catalog_symlinked", ManagedJoinRefusalReason.HOME_UNREADABLE, "catalog is unreadable"),
    ("catalog_oversized", ManagedJoinRefusalReason.HOME_UNREADABLE, "catalog is unreadable"),
    ("guard_removed", ManagedJoinRefusalReason.HOME_DRIFT, "missing guards: join_stop_guard"),
    (
        "hook_digest_changed",
        ManagedJoinRefusalReason.REGISTRY_DIGEST_MISMATCH,
        "hook_registry_digest",
    ),
    ("recovery_blocked", ManagedJoinRefusalReason.RECOVERY_BLOCKED, ""),
)


@pytest.mark.parametrize(("tamper", "expected_reason", "expected_detail"), _RELOAD_CASES)
def test_reload_refusal_names_failing_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tamper: str,
    expected_reason: ManagedJoinRefusalReason,
    expected_detail: str,
) -> None:
    from autoskillit.core import CODEX_HOME_ENV_VAR, ManagedJoinVerificationRefusal
    from autoskillit.execution.backends._codex_catalog import CODEX_CATALOG_LIMIT
    from autoskillit.server._managed_join_attestation import DefaultManagedJoinAttestationAuthority

    backend, record_store, home, _ = _prepared_managed_home(tmp_path, monkeypatch)
    record_path = record_store.path_for("abc123")
    catalog_path = home / "autoskillit-models.json"
    if tamper in {"model_changed", "hook_digest_changed"}:
        document = json.loads(record_path.read_text(encoding="utf-8"))
        field = "resolved_model" if tamper == "model_changed" else "hook_registry_digest"
        document["attestation"][field] = "other-model" if tamper == "model_changed" else "0" * 64
        record_path.write_text(json.dumps(document), encoding="utf-8")
    elif tamper == "home_unset":
        monkeypatch.delenv(CODEX_HOME_ENV_VAR)
    elif tamper == "catalog_missing":
        catalog_path.unlink()
    elif tamper == "catalog_tampered":
        catalog_path.write_bytes(b'{"models":["tampered"]}')
    elif tamper == "catalog_symlinked":
        replacement = tmp_path / "replacement-models-cache.json"
        replacement.write_bytes(catalog_path.read_bytes())
        catalog_path.unlink()
        catalog_path.symlink_to(replacement)
    elif tamper == "catalog_oversized":
        catalog_path.write_bytes(b" " * (CODEX_CATALOG_LIMIT + 1))
    elif tamper == "guard_removed":
        config_path = home / "config.toml"
        config_path.write_text(
            config_path.read_text(encoding="utf-8").replace(
                "join_stop_guard", "removed_join_stop_guard"
            ),
            encoding="utf-8",
        )
    else:
        assert tamper == "recovery_blocked"

    authority = DefaultManagedJoinAttestationAuthority(record_store=record_store, backend=backend)
    if tamper == "recovery_blocked":
        authority.set_recovery_gate(lambda: False)
    result = authority.find_verified_context(backend="codex", parent_session_id="abc123")
    assert isinstance(result, ManagedJoinVerificationRefusal)
    assert result.reason is expected_reason
    if expected_detail:
        assert expected_detail in "; ".join(result.detail)
    else:
        assert result.detail == ()


_VERIFY_CASES = (
    ("none", ManagedJoinRefusalReason.NO_CONTEXT),
    ("no_attestation", ManagedJoinRefusalReason.NO_ATTESTATION),
    ("other_authority", ManagedJoinRefusalReason.NOT_ISSUED),
    ("backend_mismatch", ManagedJoinRefusalReason.BACKEND_MISMATCH),
    ("parent_mismatch", ManagedJoinRefusalReason.PARENT_MISMATCH),
    ("stale_epoch", ManagedJoinRefusalReason.STALE_EPOCH),
    ("mode_not_admitted", ManagedJoinRefusalReason.MODE_NOT_ADMITTED),
    ("recovery_blocked", ManagedJoinRefusalReason.RECOVERY_BLOCKED),
)


@pytest.mark.parametrize(("scenario", "expected_reason"), _VERIFY_CASES)
def test_verify_refusal_names_failing_check(
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
    expected_reason: ManagedJoinRefusalReason,
) -> None:
    from autoskillit.core import ManagedJoinVerificationRefusal, SemanticAdaptationContext
    from autoskillit.server._managed_join_attestation import DefaultManagedJoinAttestationAuthority

    authority = DefaultManagedJoinAttestationAuthority()
    context = _issue_context(authority)
    backend, parent_id = "codex", "abc123"
    if scenario == "none":
        candidate = None
    elif scenario == "no_attestation":
        candidate = SemanticAdaptationContext()
    elif scenario == "other_authority":
        candidate = _issue_context(DefaultManagedJoinAttestationAuthority(), model="other-model")
    elif scenario == "mode_not_admitted":
        candidate = _issue_context(authority, direct_tool_mode=False)
    else:
        candidate = context
    if scenario == "backend_mismatch":
        backend = "claude"
    elif scenario == "parent_mismatch":
        parent_id = "otherparent"
    elif scenario == "stale_epoch":
        monkeypatch.setattr(authority, "_activation_epoch", authority.activation_epoch + 1)
    elif scenario == "recovery_blocked":
        authority.set_recovery_gate(lambda: False)

    result = authority.verify(candidate, backend=backend, parent_session_id=parent_id)
    assert isinstance(result, ManagedJoinVerificationRefusal)
    assert result.reason is expected_reason


_STRUCTURAL_CASES = (
    ("ambiguous", ManagedJoinRefusalReason.AMBIGUOUS_CONTEXT, ""),
    ("store_unavailable", ManagedJoinRefusalReason.RECORD_STORE_UNAVAILABLE, ""),
    ("record_unavailable", ManagedJoinRefusalReason.RECORD_UNAVAILABLE, ""),
    ("route_mismatch", ManagedJoinRefusalReason.ROUTE_MISMATCH, "record route"),
    (
        "tool_digest_changed",
        ManagedJoinRefusalReason.REGISTRY_DIGEST_MISMATCH,
        "fixed_batch_tool_registry_digest",
    ),
    ("backend_not_managed", ManagedJoinRefusalReason.BACKEND_NOT_MANAGED, ""),
)


@pytest.mark.parametrize(("scenario", "expected_reason", "expected_detail"), _STRUCTURAL_CASES)
def test_find_verified_context_structural_refusals(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
    expected_reason: ManagedJoinRefusalReason,
    expected_detail: str,
) -> None:
    from types import SimpleNamespace
    from typing import cast

    from autoskillit.core import ManagedJoinVerificationRefusal
    from autoskillit.server._managed_join_attestation import DefaultManagedJoinAttestationAuthority

    backend, record_store, _, context = _prepared_managed_home(tmp_path, monkeypatch)
    parent_id = "abc123"
    if scenario == "ambiguous":
        authority = DefaultManagedJoinAttestationAuthority(
            record_store=record_store, backend=backend
        )
        _issue_context(authority, model="first-model")
        _issue_context(authority, model="second-model")
    elif scenario == "store_unavailable":
        authority = DefaultManagedJoinAttestationAuthority(backend=backend)
    else:
        if scenario == "record_unavailable":
            parent_id = "otherparent"
        elif scenario == "route_mismatch":
            record_store.write(context, route="leaf")
        elif scenario == "tool_digest_changed":
            record_path = record_store.path_for(parent_id)
            document = json.loads(record_path.read_text(encoding="utf-8"))
            document["attestation"]["fixed_batch_tool_registry_digest"] = "0" * 64
            record_path.write_text(json.dumps(document), encoding="utf-8")
        elif scenario == "backend_not_managed":
            backend = cast(
                "CodexBackend",
                SimpleNamespace(
                    name="codex",
                    capabilities=SimpleNamespace(managed_fixed_batch_route_capable=False),
                ),
            )
        authority = DefaultManagedJoinAttestationAuthority(
            record_store=record_store, backend=backend
        )

    result = authority.find_verified_context(backend="codex", parent_session_id=parent_id)
    assert isinstance(result, ManagedJoinVerificationRefusal)
    assert result.reason is expected_reason
    if expected_detail:
        assert expected_detail in "; ".join(result.detail)
    else:
        assert result.detail == ()


def test_every_refusal_reason_is_exercised() -> None:
    expected = {case[1] for case in _RELOAD_CASES}
    expected.update(case[1] for case in _VERIFY_CASES)
    expected.update(case[1] for case in _STRUCTURAL_CASES)
    assert expected == set(ManagedJoinRefusalReason)
