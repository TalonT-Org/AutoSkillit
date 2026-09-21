"""Managed-join prelaunch issuance and durable revalidation tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.execution.backends._codex_fixtures import installed_catalog

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def _source_home(tmp_path: Path, *, include_sol: bool = True) -> tuple[Path, bytes]:
    source_home = tmp_path / "source"
    source_home.mkdir(parents=True)
    catalog = installed_catalog()
    if not include_sol:
        models = catalog["models"]
        assert isinstance(models, list)
        catalog["models"] = [
            model
            for model in models
            if isinstance(model, dict) and model.get("slug") != "gpt-5.6-sol"
        ]
    raw_catalog = json.dumps(catalog, sort_keys=True).encode("utf-8")
    (source_home / "models_cache.json").write_bytes(raw_catalog)
    return source_home, raw_catalog


def test_prelaunch_issuance_produces_verifiable_context_from_production_digests(
    tmp_path: Path,
) -> None:
    from autoskillit.core import JoinSpec, SemanticAdaptationContext, SkillSemanticPlan
    from autoskillit.execution.backends import CodexBackend
    from autoskillit.execution.backends._codex_catalog import project_codex_catalog
    from autoskillit.execution.backends._codex_hooks import managed_codex_route_digest
    from autoskillit.hook_registry import HOOK_REGISTRY_HASH
    from autoskillit.hooks._session_binding import resolve_channel_dir
    from autoskillit.server._managed_join_prelaunch import prepare_managed_join_context

    source_home, raw_catalog = _source_home(tmp_path)
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
    attestation = context.managed_join_attestation
    assert attestation is not None
    expected_projection = project_codex_catalog(
        raw_catalog,
        expected_model="gpt-5.6-luna",
        expected_reasoning_effort="high",
    )
    assert attestation.provenance == "autoskillit-server"
    assert attestation.parent_session_id == "abc123"
    assert attestation.launch_context == "interactive"
    assert attestation.resolved_model == "gpt-5.6-luna"
    assert attestation.resolved_reasoning_effort == "high"
    assert attestation.hook_registry_digest == HOOK_REGISTRY_HASH
    assert attestation.fixed_batch_tool_registry_digest == managed_codex_route_digest()
    assert attestation.codex_catalog_digest == expected_projection.projected_sha256.removeprefix(
        "sha256:"
    )
    assert (resolve_channel_dir(state_root) / "managed_join_attestation_abc123.json").is_file()
    plan = SkillSemanticPlan(schema_version=1, join=JoinSpec(required=True))
    assert backend.adapt_skill_semantics(plan, context).unsupported_operation is None


def test_prelaunch_issuance_refuses_unresolvable_model_identity(tmp_path: Path) -> None:
    from autoskillit.execution.backends import ClaudeCodeBackend, CodexBackend
    from autoskillit.server._managed_join_prelaunch import (
        ManagedJoinIssuanceRefusal,
        prepare_managed_join_context,
    )

    state_root = tmp_path / "state"
    source_home, _ = _source_home(tmp_path / "no-default")
    missing_default = prepare_managed_join_context(
        backend=CodexBackend(source_codex_home=source_home),
        configured_model="gpt-5.6-sol",
        state_root=state_root,
        parent_id="missing-default",
        launch_context="interactive",
    )
    assert isinstance(missing_default, ManagedJoinIssuanceRefusal)
    assert "gpt-5.6-sol" in missing_default.reason
    assert not (state_root / ".autoskillit").exists()

    absent_home, _ = _source_home(tmp_path / "absent", include_sol=False)
    absent_model = prepare_managed_join_context(
        backend=CodexBackend(source_codex_home=absent_home),
        configured_model="gpt-5.6-sol",
        state_root=state_root,
        parent_id="absent-model",
        launch_context="interactive",
    )
    assert isinstance(absent_model, ManagedJoinIssuanceRefusal)
    assert "gpt-5.6-sol" in absent_model.reason

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


def test_server_authority_loads_and_revalidates_prelaunch_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.core import CODEX_HOME_ENV_VAR, SemanticAdaptationContext
    from autoskillit.execution.backends import CodexBackend
    from autoskillit.server._managed_join_attestation import (
        DefaultManagedJoinAttestationAuthority,
        ManagedJoinRecordStore,
    )
    from autoskillit.server._managed_join_prelaunch import prepare_managed_join_context

    source_home, _ = _source_home(tmp_path)
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
    attestation = context.managed_join_attestation
    assert attestation is not None
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text(
        '[mcp_servers.autoskillit]\ncommand = "autoskillit"\n', encoding="utf-8"
    )
    backend.configure_managed_session_dir(
        home,
        attestation=attestation,
        route="interactive-parent",
    )
    monkeypatch.setenv(CODEX_HOME_ENV_VAR, str(home))
    record_store = ManagedJoinRecordStore(state_root)

    authority = DefaultManagedJoinAttestationAuthority(
        record_store=record_store,
        backend=backend,
    )
    loaded = authority.find_verified_context(backend="codex", parent_session_id="abc123")
    assert loaded == context
    assert authority.verify(loaded, backend="codex", parent_session_id="abc123") == context

    record_path = record_store.path_for("abc123")
    original_record = record_path.read_text(encoding="utf-8")

    edited = json.loads(original_record)
    edited["attestation"]["resolved_model"] = "other-model"
    record_path.write_text(json.dumps(edited), encoding="utf-8")
    try:
        assert (
            DefaultManagedJoinAttestationAuthority(
                record_store=record_store, backend=backend
            ).find_verified_context(backend="codex", parent_session_id="abc123")
            is None
        )
    finally:
        record_path.write_text(original_record, encoding="utf-8")

    monkeypatch.delenv(CODEX_HOME_ENV_VAR)
    assert (
        DefaultManagedJoinAttestationAuthority(
            record_store=record_store, backend=backend
        ).find_verified_context(backend="codex", parent_session_id="abc123")
        is None
    )
    monkeypatch.setenv(CODEX_HOME_ENV_VAR, str(home))

    config_path = home / "config.toml"
    original_config = config_path.read_text(encoding="utf-8")
    config_path.write_text(
        original_config.replace("join_stop_guard", "removed_join_stop_guard"),
        encoding="utf-8",
    )
    assert (
        DefaultManagedJoinAttestationAuthority(
            record_store=record_store, backend=backend
        ).find_verified_context(backend="codex", parent_session_id="abc123")
        is None
    )
    config_path.write_text(original_config, encoding="utf-8")

    edited = json.loads(original_record)
    edited["attestation"]["hook_registry_digest"] = "0" * 64
    record_path.write_text(json.dumps(edited), encoding="utf-8")
    try:
        assert (
            DefaultManagedJoinAttestationAuthority(
                record_store=record_store, backend=backend
            ).find_verified_context(backend="codex", parent_session_id="abc123")
            is None
        )
    finally:
        record_path.write_text(original_record, encoding="utf-8")

    blocked = DefaultManagedJoinAttestationAuthority(record_store=record_store, backend=backend)
    blocked.set_recovery_gate(lambda: False)
    assert blocked.find_verified_context(backend="codex", parent_session_id="abc123") is None
