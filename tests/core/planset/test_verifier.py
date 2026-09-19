"""Persisted plan-set authorities are untrusted until every bound byte is checked."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import autoskillit.core.planset.verifier as verifier
from autoskillit.core import (
    AllocationKind,
    AllocationRowDef,
    CoverageResultDef,
    CoverageStatus,
    InventoryMode,
    PlanPartRef,
    PlanSetAuthority,
    PlanSetBindingMode,
    PlanSetRejectReason,
    PlanSetState,
    canonical_json_bytes,
    compute_bytes_hash,
    compute_canonical_hash,
    verify_plan_set_authority,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def _bound(
    tmp_path: Path, *, sealed: bool = True, mode: PlanSetBindingMode = PlanSetBindingMode.RECIPE
):
    root = tmp_path / "root"
    root.mkdir(parents=True)
    part = root / "part_a.md"
    part.write_text("# Plan\n", encoding="utf-8")
    authority = PlanSetAuthority.create(
        binding_mode=mode,
        execution_generation="execution",
        kitchen_id="kitchen",
        dispatch_id="",
        plan_set_authority_id="planset-test",
        revision=1,
        parent_authority_digest=None,
        state=PlanSetState.SEALED if sealed else PlanSetState.OPEN,
        inventory_mode=InventoryMode.NO_ISSUE,
        issue=None,
        requirements=(),
        parts=(
            PlanPartRef(
                1, "P1", "A", str(part), part.stat().st_size, compute_bytes_hash(part.read_bytes())
            ),
        ),
        allocations=(AllocationRowDef("P-1", "P1", AllocationKind.OWNED, "Step 1.1"),),
        coverage=CoverageResultDef(
            CoverageStatus.PASS if sealed else CoverageStatus.NOT_EVALUATED
        ),
        unparsed_marker_lines=(),
        generated_at="2026-09-18T00:00:00Z",
    )
    path = root / "authority.json"
    path.write_bytes(authority.canonical_bytes)
    return root, part, path, authority


def _verify(root: Path, part: Path, authority_path: Path, **overrides: object):
    args: dict[str, object] = {
        "allowed_root": root,
        "expected_execution_generation": "execution",
        "expected_kitchen_id": "kitchen",
        "current_plan_path": part,
        "require_sealed": True,
    }
    args.update(overrides)
    return verify_plan_set_authority(authority_path, **args)


def test_valid_authority_returns_exact_part_evidence(tmp_path: Path) -> None:
    root, part, path, authority = _bound(tmp_path)
    result = _verify(root, part, path)
    assert result.accepted and result.evidence is not None
    assert result.evidence.part_key == "P1"
    assert result.evidence.part_ordinal == 1
    assert result.evidence.part_count == 1
    assert result.evidence.part_suffix == "A"
    assert result.evidence.plan_set_authority_digest == authority.authority_digest
    assert result.evidence.to_dict()["status"] == "admitted"


@pytest.mark.parametrize(
    ("kind", "reason"),
    [
        ("escape", PlanSetRejectReason.PATH_ESCAPE),
        ("symlink", PlanSetRejectReason.SYMLINK),
        ("hardlink", PlanSetRejectReason.HARDLINK),
        ("oversized", PlanSetRejectReason.OVERSIZED),
        ("world_writable", PlanSetRejectReason.WORLD_WRITABLE),
    ],
)
def test_authority_file_guards_have_distinct_reasons(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    reason: PlanSetRejectReason,
) -> None:
    root, part, path, _ = _bound(tmp_path)
    candidate = path
    if kind == "escape":
        candidate = tmp_path / "outside.json"
        candidate.write_bytes(path.read_bytes())
    elif kind == "symlink":
        candidate = root / "link.json"
        candidate.symlink_to(path)
    elif kind == "hardlink":
        candidate = root / "hardlink.json"
        os.link(path, candidate)
    elif kind == "oversized":
        monkeypatch.setattr(verifier, "PLAN_SET_MAX_AUTHORITY_BYTES", 10)
    else:
        path.chmod(0o666)
    assert _verify(root, part, candidate).reason is reason


def test_metadata_drift_is_distinct_from_containment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, part, path, _ = _bound(tmp_path)
    real_fstat = os.fstat
    calls = 0

    def changed_fstat(fd: int):
        nonlocal calls
        value = real_fstat(fd)
        calls += 1
        if calls == 2:
            values = list(value)
            values[6] += 1
            return os.stat_result(values)
        return value

    monkeypatch.setattr(os, "fstat", changed_fstat)
    assert _verify(root, part, path).reason is PlanSetRejectReason.METADATA_DRIFT


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("noncanonical", PlanSetRejectReason.AUTHORITY_NOT_CANONICAL),
        ("schema", PlanSetRejectReason.SCHEMA_VERSION),
        ("digest", PlanSetRejectReason.AUTHORITY_DIGEST),
    ],
)
def test_authority_json_rejections(
    tmp_path: Path, mutation: str, reason: PlanSetRejectReason
) -> None:
    root, part, path, authority = _bound(tmp_path)
    payload = authority.to_dict()
    if mutation == "noncanonical":
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    else:
        payload["schema_version" if mutation == "schema" else "authority_digest"] = (
            999 if mutation == "schema" else "sha256:wrong"
        )
        path.write_bytes(canonical_json_bytes(payload))
    assert _verify(root, part, path).reason is reason


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ({"expected_execution_generation": "other"}, PlanSetRejectReason.EXECUTION_GENERATION),
        ({"expected_kitchen_id": "other"}, PlanSetRejectReason.KITCHEN_ID),
    ],
)
def test_execution_identity_rejections(
    tmp_path: Path, override: dict[str, str], reason: PlanSetRejectReason
) -> None:
    root, part, path, _ = _bound(tmp_path)
    assert _verify(root, part, path, **override).reason is reason


def test_open_standalone_and_nonmember_rejections(tmp_path: Path) -> None:
    root, part, path, _ = _bound(tmp_path, sealed=False)
    assert _verify(root, part, path).reason is PlanSetRejectReason.AUTHORITY_NOT_SEALED
    root, part, path, _ = _bound(tmp_path / "standalone", mode=PlanSetBindingMode.STANDALONE)
    assert _verify(root, part, path).reason is PlanSetRejectReason.BINDING_MODE
    root, part, path, _ = _bound(tmp_path / "member")
    other = root / "other.md"
    other.write_text("# Other\n", encoding="utf-8")
    assert _verify(root, other, path).reason is PlanSetRejectReason.PART_NOT_FOUND


def test_mutated_and_missing_member_have_distinct_reasons(tmp_path: Path) -> None:
    root, part, path, _ = _bound(tmp_path)
    part.write_text("# Mutated\n", encoding="utf-8")
    assert _verify(root, part, path).reason is PlanSetRejectReason.PART_CONTENT_CHANGED
    part.unlink()
    assert _verify(root, part, path).reason is PlanSetRejectReason.PART_FILE_MISSING


def test_post_read_hashing_uses_bound_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, part, path, authority = _bound(tmp_path)
    original = verifier.compute_bytes_hash
    hashed: list[bytes] = []

    def counted(data: bytes) -> str:
        hashed.append(data)
        return original(data)

    monkeypatch.setattr(verifier, "compute_bytes_hash", counted)
    result = _verify(root, part, path)
    assert result.accepted and part.read_bytes() in hashed
    payload = json.loads(path.read_bytes())
    payload.pop("authority_digest")
    assert (
        compute_canonical_hash(payload, domain=verifier.PLAN_SET_AUTHORITY_DOMAIN)
        == authority.authority_digest
    )
