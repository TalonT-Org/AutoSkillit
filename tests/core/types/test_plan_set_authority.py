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
    ],
)
def test_invalid_authority_states_fail_closed(
    tmp_path: Path, overrides: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        _authority(tmp_path, **overrides)


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
    )
    assert [part.part_suffix for part in valid.parts] == ["A", "A"]


def test_standalone_mode_permits_empty_execution_generation(tmp_path: Path) -> None:
    authority = _authority(
        tmp_path,
        binding_mode=PlanSetBindingMode.STANDALONE,
        execution_generation="",
    )
    assert authority.binding_mode is PlanSetBindingMode.STANDALONE
