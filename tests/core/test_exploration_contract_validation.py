"""Direct validation tests for typed exploration contracts."""

from __future__ import annotations

import base64
import json
from dataclasses import replace
from typing import Any

import pytest

from autoskillit.core import (
    ContinuationCursor,
    ExplorationDispatchConventions,
    ExplorationDispatchMaterialization,
    ExplorationQuerySpec,
    ExplorationTaskSpec,
    ExplorationVectorApplicabilityId,
    ExplorationVectorDef,
    ExplorationVectorDisposition,
    RelationshipKind,
    RepositoryIdentity,
    RepositoryProfileId,
    RepositorySnapshot,
    SkillContractError,
    normalize_parent_sandbox_mode,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def _snapshot(**changes: object) -> RepositorySnapshot:
    snapshot = RepositorySnapshot(
        identity=RepositoryIdentity("local-repository", "unborn"),
        tree_digest="tree",
        collector_manifest_digest="collectors",
    )
    return replace(snapshot, **changes)


@pytest.mark.parametrize(
    ("changes", "valid"),
    [
        ({}, True),
        ({"stale": True}, True),
        ({"truncated": True, "truncation_reason": "bounded"}, True),
        ({"stale": True, "truncated": True, "truncation_reason": "bounded"}, False),
        ({"truncated": True}, False),
        ({"truncation_reason": "bounded"}, False),
    ],
)
def test_repository_snapshot_state_invariants(changes: dict[str, object], valid: bool) -> None:
    if valid:
        assert _snapshot(**changes)
    else:
        with pytest.raises(ValueError):
            _snapshot(**changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"query": ""},
        {"query": "   "},
        {"max_results": 0},
        {
            "required_profiles": (
                RepositoryProfileId.GENERIC_PYTHON,
                RepositoryProfileId.GENERIC_PYTHON,
            )
        },
    ],
)
def test_exploration_query_rejects_invalid_identity_inputs(changes: dict[str, object]) -> None:
    values: dict[str, Any] = {"query": "symbol", "max_results": 10}
    values.update(changes)

    with pytest.raises(ValueError):
        ExplorationQuerySpec(**values)


def _cursor_token(payload: object) -> str:
    return base64.urlsafe_b64encode(json.dumps(payload).encode("ascii")).decode("ascii")


def test_continuation_cursor_round_trips_exact_authority() -> None:
    cursor = ContinuationCursor("result", 4, 20, "authority")

    assert ContinuationCursor.decode(cursor.encode()) == cursor


@pytest.mark.parametrize(
    "payload",
    [
        {"a": "authority", "d": "result", "o": 0, "p": 20, "v": 1},
        {"a": "authority", "d": "result", "o": True, "p": 20, "v": 2},
        {"a": "authority", "d": "result", "o": 0, "p": "20", "v": 2},
        {"d": "result", "o": 0, "p": 20, "v": 2},
    ],
)
def test_continuation_cursor_rejects_invalid_wire_payloads(payload: object) -> None:
    with pytest.raises(ValueError, match="invalid continuation cursor"):
        ContinuationCursor.decode(_cursor_token(payload))


@pytest.mark.parametrize(("offset", "page_size"), [(-1, 1), (0, 0)])
def test_continuation_cursor_rejects_invalid_bounds(offset: int, page_size: int) -> None:
    with pytest.raises(ValueError):
        ContinuationCursor("result", offset, page_size)


def test_dispatch_contracts_validate_native_vocabulary_and_digests() -> None:
    conventions = ExplorationDispatchConventions("spawn_agent", "role", "message")
    materialization = ExplorationDispatchMaterialization(
        {"semantic-code-navigator": "replacement"},
        "plan-digest",
        {"semantic-code-navigator": "definition-digest"},
        "test preamble",
    )

    assert conventions.launcher == "spawn_agent"
    assert materialization.replacements["semantic-code-navigator"] == "replacement"
    with pytest.raises(ValueError, match="identifiers must be valid"):
        ExplorationDispatchConventions("not-valid", "role", "message")
    with pytest.raises(ValueError, match="description argument must be valid"):
        ExplorationDispatchConventions("spawn_agent", "role", "message", description_argument="")
    with pytest.raises(ValueError, match="router-plan digest"):
        replace(materialization, router_plan_digest="")
    with pytest.raises(ValueError, match="marker replacements"):
        replace(materialization, replacements={})
    with pytest.raises(ValueError, match="role-definition digest"):
        replace(materialization, role_definition_digests={"other": "digest"})


def _valid_vector() -> ExplorationVectorDef:
    return ExplorationVectorDef(
        id="inspect-symbols",
        disposition=ExplorationVectorDisposition.MIGRATED,
        rationale="Inspect repository symbols.",
        applicability=ExplorationVectorApplicabilityId.ALWAYS,
        role="semantic-code-navigator",
        profile=RepositoryProfileId.GENERIC_PYTHON,
        relationship_classes=(RelationshipKind.REFERENCES,),
        task=ExplorationTaskSpec(
            "inspect-symbols-task",
            "inspect-symbols-frontier",
            RepositoryProfileId.GENERIC_PYTHON,
            scope=("src",),
        ),
        body="Inspect symbols.",
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("rationale", ""),
        ("relationship_classes", (RelationshipKind.REFERENCES, RelationshipKind.REFERENCES)),
        ("body", "autoskillit:exploration-vector"),
        ("role", None),
    ],
)
def test_exploration_vector_rejects_invalid_invariant_families(
    field: str,
    value: object,
) -> None:
    with pytest.raises(SkillContractError):
        replace(_valid_vector(), **{field: value})


def test_exploration_vector_rejects_dependency_and_profile_inconsistency() -> None:
    vector = _valid_vector()
    with pytest.raises(SkillContractError, match="dependencies must be unique"):
        replace(vector, task=replace(vector.task, depends_on=("prior", "prior")))
    with pytest.raises(SkillContractError, match="cannot depend on itself"):
        replace(vector, task=replace(vector.task, depends_on=(vector.task.task_id,)))
    with pytest.raises(SkillContractError, match="profile must match"):
        replace(vector, task=replace(vector.task, profile=RepositoryProfileId.LANGUAGE_NEUTRAL))
    with pytest.raises(SkillContractError, match="remain prose"):
        replace(vector, disposition=ExplorationVectorDisposition.RETAINED)


@pytest.mark.parametrize("mode", ["read-only", "workspace-write"])
def test_parent_sandbox_normalization_accepts_closed_modes(mode: str) -> None:
    assert normalize_parent_sandbox_mode(mode) == mode


def test_parent_sandbox_normalization_rejects_unknown_mode() -> None:
    with pytest.raises(SkillContractError, match="unsupported parent sandbox mode"):
        normalize_parent_sandbox_mode("danger-full-access")
