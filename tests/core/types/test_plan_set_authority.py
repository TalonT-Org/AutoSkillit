"""Plan-set authority values reject invalid persisted states at construction."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from autoskillit.core import (
    AllocationKind,
    AllocationRowDef,
    CoverageResultDef,
    CoverageStatus,
    InventoryMode,
    PlanPartRef,
    PlanSetAuthority,
    PlanSetBindingMode,
    PlanSetState,
    compute_bytes_hash,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def _authority(tmp_path: Path, **overrides: object) -> PlanSetAuthority:
    part = tmp_path / "part.md"
    part.write_text("# Plan\n", encoding="utf-8")
    fields: dict[str, object] = {
        "binding_mode": PlanSetBindingMode.RECIPE,
        "execution_generation": "execution",
        "kitchen_id": "kitchen",
        "dispatch_id": "",
        "plan_set_authority_id": "planset-test",
        "revision": 1,
        "parent_authority_digest": None,
        "state": PlanSetState.SEALED,
        "inventory_mode": InventoryMode.NO_ISSUE,
        "issue": None,
        "requirements": (),
        "parts": (
            PlanPartRef(
                1, "P1", "A", str(part), part.stat().st_size, compute_bytes_hash(part.read_bytes())
            ),
        ),
        "allocations": (AllocationRowDef("P-1", "P1", AllocationKind.OWNED, "Step 1.1"),),
        "coverage": CoverageResultDef(CoverageStatus.PASS),
        "unparsed_marker_lines": (),
        "generated_at": "2026-09-18T00:00:00Z",
    }
    fields.update(overrides)
    return PlanSetAuthority.create(**fields)


def test_create_digest_and_canonical_round_trip(tmp_path: Path) -> None:
    authority = _authority(tmp_path)
    assert authority.authority_digest == authority.compute_digest()
    assert PlanSetAuthority.from_dict(json.loads(authority.canonical_bytes)) == authority
    with pytest.raises(ValueError, match="authority_digest"):
        replace(authority, authority_digest="sha256:wrong")
    with pytest.raises(ValueError, match="schema_version"):
        replace(authority, schema_version=999)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"revision": 0}, "revision"),
        ({"revision": 2}, "parent digest"),
        (
            {"state": PlanSetState.SEALED, "coverage": CoverageResultDef(CoverageStatus.FAIL)},
            "passing coverage",
        ),
        (
            {"binding_mode": PlanSetBindingMode.RECIPE, "execution_generation": ""},
            "execution generation",
        ),
        ({"binding_mode": "foreign"}, "binding_mode"),
        (
            {
                "coverage": CoverageResultDef(
                    CoverageStatus.PASS, parts_without_obligations=("P2",)
                )
            },
            "unknown part key",
        ),
        (
            {
                "inventory_mode": InventoryMode.ENUMERATED,
                "coverage": CoverageResultDef(
                    CoverageStatus.PASS, parts_without_obligations=("P1",)
                ),
            },
            "invalid for enumerated inventory",
        ),
    ],
)
def test_invalid_authority_states_fail_closed(
    tmp_path: Path, overrides: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        _authority(tmp_path, **overrides)


@pytest.mark.parametrize("mode", (InventoryMode.NO_ISSUE, InventoryMode.UNENUMERATED))
def test_authority_rejects_omitted_empty_parts(tmp_path: Path, mode: InventoryMode) -> None:
    with pytest.raises(ValueError, match="must match actual empty parts"):
        _authority(tmp_path, inventory_mode=mode, allocations=())


def test_part_ordinals_and_resolved_locators_are_unique(tmp_path: Path) -> None:
    authority = _authority(tmp_path)
    first = authority.parts[0]
    with pytest.raises(ValueError, match="ordinals"):
        _authority(tmp_path, parts=(replace(first, ordinal=2, part_key="P2"),))
    alias = replace(
        first, ordinal=2, part_key="P2", locator=str(tmp_path / "x" / ".." / "part.md")
    )
    with pytest.raises(ValueError, match="duplicate locators"):
        _authority(tmp_path, parts=(first, alias))
    second = tmp_path / "second.md"
    second.write_text("# Another plan\n", encoding="utf-8")
    valid = _authority(
        tmp_path,
        parts=(first, replace(alias, locator=str(second))),
        allocations=authority.allocations
        + (replace(authority.allocations[0], requirement_id="P-2", part_key="P2"),),
    )
    assert [part.part_suffix for part in valid.parts] == ["A", "A"]


def test_standalone_mode_permits_empty_execution_generation(tmp_path: Path) -> None:
    authority = _authority(
        tmp_path,
        binding_mode=PlanSetBindingMode.STANDALONE,
        execution_generation="",
    )
    assert authority.binding_mode is PlanSetBindingMode.STANDALONE


@pytest.mark.parametrize(
    ("allocation", "implementation_step", "obligation"),
    (
        ("stage", "Step 1.1", "pr_opened"),
        ("stage", None, None),
        ("owned", "Step 1.1", "pr_opened"),
        ("shared", "Step 1.1", "pr_opened"),
        ("owned", None, None),
        ("shared", None, None),
    ),
)
def test_allocation_row_rejects_invalid_step_and_obligation_combinations(
    allocation: str, implementation_step: str | None, obligation: str | None
) -> None:
    from autoskillit.core import StageObligation

    allocation_kind = AllocationKind.STAGE if allocation == "stage" else AllocationKind(allocation)
    stage_obligation = StageObligation(obligation) if obligation is not None else None

    with pytest.raises(ValueError):
        AllocationRowDef(
            "R3",
            "P1",
            allocation_kind,
            implementation_step,
            stage_obligation=stage_obligation,
        )


def test_stage_allocation_round_trips_and_owned_row_omits_optional_key() -> None:
    from autoskillit.core import StageObligation

    owned = AllocationRowDef("R1", "P1", AllocationKind.OWNED, "Step 1")
    assert "stage_obligation" not in owned.to_dict()

    stage = AllocationRowDef(
        "P-10",
        "P1",
        AllocationKind.STAGE,
        None,
        stage_obligation=StageObligation.PR_OPENED,
    )
    assert stage.to_dict()["stage_obligation"] == "pr_opened"
    assert AllocationRowDef.from_dict(stage.to_dict()) == stage


def test_stage_allocation_rejects_non_enum_obligation() -> None:
    with pytest.raises(ValueError):
        AllocationRowDef(
            "P-10",
            "P1",
            AllocationKind.STAGE,
            None,
            stage_obligation="pr_opened",
        )


def test_owned_only_authority_canonical_payload_and_digest_are_stable() -> None:
    data = b"# Plan\n"
    authority = PlanSetAuthority.create(
        binding_mode=PlanSetBindingMode.RECIPE,
        execution_generation="execution",
        kitchen_id="kitchen",
        dispatch_id="",
        plan_set_authority_id="planset-golden",
        revision=1,
        parent_authority_digest=None,
        state=PlanSetState.SEALED,
        inventory_mode=InventoryMode.NO_ISSUE,
        issue=None,
        requirements=(),
        parts=(
            PlanPartRef(
                1,
                "P1",
                "A",
                "/plans/P1.md",
                len(data),
                compute_bytes_hash(data),
            ),
        ),
        allocations=(AllocationRowDef("P-1", "P1", AllocationKind.OWNED, "Step 1.1"),),
        coverage=CoverageResultDef(CoverageStatus.PASS),
        unparsed_marker_lines=(),
        generated_at="2026-09-18T00:00:00Z",
    )

    assert json.loads(authority.canonical_bytes) == {
        "allocations": [
            {
                "allocation": "owned",
                "implementation_step": "Step 1.1",
                "part_key": "P1",
                "requirement_id": "P-1",
            }
        ],
        "authority_digest": (
            "sha256:21af00834cc2c3f6484f37e79432b801b784a39d0c924d4d513ef9b628eba66c"
        ),
        "binding_mode": "recipe",
        "coverage": {
            "container_allocated": [],
            "duplicate": [],
            "parts_without_obligations": [],
            "status": "pass",
            "unassigned": [],
            "uncovered": [],
            "unknown": [],
        },
        "dispatch_id": "",
        "execution_generation": "execution",
        "generated_at": "2026-09-18T00:00:00Z",
        "inventory_mode": "no_issue",
        "issue": None,
        "kitchen_id": "kitchen",
        "parent_authority_digest": None,
        "parts": [
            {
                "byte_size": 7,
                "content_digest": (
                    "sha256:c3964bb3b70a957ec9b233c7dd3653f6ba17701ab00facf88ae1393dc6155577"
                ),
                "locator": "/plans/P1.md",
                "ordinal": 1,
                "part_key": "P1",
                "part_suffix": "A",
            }
        ],
        "plan_set_authority_id": "planset-golden",
        "requirements": [],
        "revision": 1,
        "schema_version": 1,
        "state": "sealed",
        "unparsed_marker_lines": [],
    }
    assert authority.authority_digest == (
        "sha256:21af00834cc2c3f6484f37e79432b801b784a39d0c924d4d513ef9b628eba66c"
    )


@pytest.mark.parametrize("mode", (InventoryMode.NO_ISSUE, InventoryMode.UNENUMERATED))
def test_stage_only_part_counts_as_without_implementation_obligations(
    tmp_path: Path, mode: InventoryMode
) -> None:
    from autoskillit.core import StageObligation

    stage_row = AllocationRowDef(
        "P-10",
        "P1",
        AllocationKind.STAGE,
        None,
        stage_obligation=StageObligation.PR_OPENED,
    )
    authority = _authority(
        tmp_path,
        inventory_mode=mode,
        allocations=(stage_row,),
        coverage=CoverageResultDef(CoverageStatus.PASS, parts_without_obligations=("P1",)),
    )

    assert authority.coverage.parts_without_obligations == ("P1",)
    with pytest.raises(ValueError, match="must match actual empty parts"):
        _authority(
            tmp_path,
            inventory_mode=mode,
            allocations=(stage_row,),
            coverage=CoverageResultDef(CoverageStatus.PASS),
        )
