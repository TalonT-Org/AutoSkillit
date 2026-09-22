"""Shared fixtures for managed-join server tests.

Single owner of the canonical ``_sample_attestation`` and ``_isolated_state_dir``
helpers used by ``tests/server/test_managed_join_record_store.py`` and
``tests/server/test_write_managed_parent_binding.py``.
"""

from __future__ import annotations

from pathlib import Path


def isolated_state_dir(tmp_path: Path) -> Path:
    """Return a project root whose ``.autoskillit`` directory is unique per test."""
    (tmp_path / ".autoskillit").mkdir(parents=True, exist_ok=True)
    return tmp_path


def sample_attestation(parent_session_id: str = "abc123"):
    """Build a valid ``ManagedJoinAttestation`` for one managed parent."""
    from autoskillit.core import (
        MANAGED_JOIN_ATTESTATION_SCHEMA_VERSION,
        ManagedJoinAttestation,
        SemanticAdaptationContext,
    )

    attestation = ManagedJoinAttestation(
        schema_version=MANAGED_JOIN_ATTESTATION_SCHEMA_VERSION,
        backend="codex",
        launch_context="interactive",
        parent_session_id=parent_session_id,
        activation_epoch=0,
        direct_tool_mode=True,
        resolved_model="gpt-5.6-luna",
        resolved_reasoning_effort="high",
        codex_catalog_digest="a" * 64,
        fixed_batch_tool_registry_digest="b" * 64,
        hook_registry_digest="c" * 64,
        skill_load_applies=True,
        guards_apply=True,
        provenance="autoskillit-server",
    )
    return SemanticAdaptationContext(managed_join_attestation=attestation)


def sample_attestation_only(parent_session_id: str = "parent-1"):
    """Build a bare ``ManagedJoinAttestation`` (no wrapping context)."""
    from autoskillit.core import (
        MANAGED_JOIN_ATTESTATION_SCHEMA_VERSION,
        ManagedJoinAttestation,
    )

    return ManagedJoinAttestation(
        schema_version=MANAGED_JOIN_ATTESTATION_SCHEMA_VERSION,
        backend="codex",
        launch_context="interactive",
        parent_session_id=parent_session_id,
        activation_epoch=0,
        direct_tool_mode=True,
        resolved_model="gpt-5.6-luna",
        resolved_reasoning_effort="high",
        codex_catalog_digest="a" * 64,
        fixed_batch_tool_registry_digest="b" * 64,
        hook_registry_digest="c" * 64,
        skill_load_applies=True,
        guards_apply=True,
        provenance="autoskillit-server",
    )
