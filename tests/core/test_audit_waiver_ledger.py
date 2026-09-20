"""Human waiver records and their explicit-root ledger are strict inputs."""

from __future__ import annotations

from dataclasses import asdict, replace
from datetime import date, timedelta
from pathlib import Path

import pytest

from autoskillit.core import AuditFindingWaiver, read_stable_contained_bytes
from autoskillit.core.audit.audit_semantic_codec import load_audit_finding_waivers
from autoskillit.core.io import dump_yaml_str

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def _record() -> AuditFindingWaiver:
    return AuditFindingWaiver(
        waiver_id="decision-1",
        requirement_id="REQ-1",
        finding_row_digest="sha256:" + "a" * 64,
        plan_set_id="plans-1",
        scope_id="scope-1",
        part_id="part-1",
        rationale="A human approved this exact unresolved finding.",
        issue=4313,
        approved_by="reviewer",
        added_date=date(2026, 1, 1),
        review_date=date(2026, 2, 1),
    )


def test_waiver_validation_and_expiry() -> None:
    waiver = _record()
    assert waiver.is_stale(as_of=date(2026, 7, 31)) is False
    assert waiver.is_stale(as_of=date(2026, 8, 1)) is True
    assert replace(waiver, review_date=date(2026, 8, 1)).added_date == waiver.added_date

    with pytest.raises(ValueError, match="rationale"):
        replace(waiver, rationale="placeholder")
    with pytest.raises(ValueError, match="approved_by"):
        replace(waiver, approved_by="")
    with pytest.raises(ValueError, match="waiver_id"):
        replace(waiver, waiver_id="not a slug")
    with pytest.raises(ValueError, match="review_date"):
        replace(waiver, review_date=waiver.added_date - timedelta(days=1))


def test_ledger_rejects_malformed_and_duplicate_entries(tmp_path: Path) -> None:
    ledger = tmp_path / ".autoskillit/waivers/audit-findings.yaml"
    ledger.parent.mkdir(parents=True)

    def load() -> tuple[AuditFindingWaiver, ...]:
        return load_audit_finding_waivers(
            waiver_root=tmp_path,
            reader=read_stable_contained_bytes,
            max_size_bytes=10_000,
        )

    ledger.write_text("waivers: []\n", encoding="utf-8")
    assert load() == ()

    ledger.write_text("waivers: [invalid]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="list of mappings"):
        load()

    record = asdict(_record())
    ledger.write_text(dump_yaml_str({"waivers": [record, record]}), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate waiver IDs"):
        load()
