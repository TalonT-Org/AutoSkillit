"""Focused durable-result tests for managed fixed-batch MCP access."""

from __future__ import annotations

from dataclasses import replace

import pytest

from autoskillit.core import SkillContractError
from autoskillit.hooks._session_binding import LoadedSkillEntry
from autoskillit.server.tools.tools_execution._fixed_batch_handlers import _page_payload
from autoskillit.server.tools.tools_execution._managed_fixed_batch import (
    ManagedFixedBatchResultStore,
    ManagedLaunchBinding,
)

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium]


def _launch() -> ManagedLaunchBinding:
    source = LoadedSkillEntry(
        skill_name="fixed-batch-skill",
        ts="2026-08-28T00:00:00Z",
        join_required=True,
        child_spawn_cardinality={"worker": 1},
        semantic_digest="semantic-source",
        adaptation_digest="adaptation-source",
        projected_digest="projected-source",
        canonical_digest="canonical-source",
        source_artifact_digest="source-artifact",
        source_artifact_incarnation_id="incarnation-1",
        binding_valid=True,
        binding_error=None,
    )
    return ManagedLaunchBinding(
        request_session_id="request-session",
        managed_parent_id="managed-parent",
        parent_session_id="request-session",
        caller_key="request-key",
        attestation_epoch=1,
        recovery_ready=True,
        selected_source=source,
    )


def test_fixed_batch_result_store_revalidates_the_complete_authority_scope(tmp_path) -> None:
    store = ManagedFixedBatchResultStore(tmp_path)
    launch = _launch()
    reference, digest = store.publish(
        launch=launch,
        batch_id="batch-1",
        assignment_id="assignment-1",
        payload={"result": "complete"},
    )

    assert reference.startswith("fixed-batch-result-")
    assert digest
    assert store.read(
        reference=reference,
        launch=launch,
        batch_id="batch-1",
        assignment_id="assignment-1",
    ) == {"result": "complete"}

    with pytest.raises(SkillContractError, match="authorization"):
        store.read(
            reference=reference,
            launch=replace(launch, managed_parent_id="foreign-parent"),
            batch_id="batch-1",
            assignment_id="assignment-1",
        )


def test_fixed_batch_result_pages_are_byte_bounded_and_utf8_safe() -> None:
    first = _page_payload("aéz", offset=0, page_size=2)
    next_offset = first["next_offset"]
    assert isinstance(next_offset, int)
    second = _page_payload("aéz", offset=next_offset, page_size=4)

    assert first["content"] == "a"
    assert first["next_offset"] == 1
    assert second["content"] == "éz"
    assert second["complete"] is True
