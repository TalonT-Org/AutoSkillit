"""Opt-in doctor repair preserves evidence and never rewrites future schemas."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium]


def _cache(home: Path) -> Path:
    return home / ".autoskillit" / "retiring_cache.json"


def _record(index: int) -> dict[str, object]:
    return {
        "record_id": f"record-{index}",
        "artifact_kind": "projection",
        "semantic_key": f"projection:{index}",
        "managed_path": f"/tmp/projection-{index}",
        "manifest_path": f"/tmp/projection-{index}/manifest.json",
        "incarnation_id": f"0000000000004000800000000000000{index}",
        "manifest_schema_version": 1,
        "artifact_digest": format(index, "064x"),
        "retired_at": "2026-01-01T00:00:00+00:00",
        "not_before": "2026-01-01T00:00:00+00:00",
        "schema_version": 2,
    }


def _write_cache(home: Path, records: list[dict[str, object]]) -> Path:
    from autoskillit.core import write_versioned_json

    cache = _cache(home)
    write_versioned_json(
        cache,
        {"records": records, "legacy_evidence": []},
        schema_version=2,
        strict_durability=True,
    )
    return cache


def test_repair_rebuilds_a_corrupt_retiring_cache(tmp_path, monkeypatch) -> None:
    from autoskillit.core import (
        RetiringCacheState,
        read_retiring_cache,
        repair_corrupt_retiring_cache,
    )

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    cache = _cache(tmp_path)
    cache.parent.mkdir(parents=True)
    original = b"{not-json"
    cache.write_bytes(original)

    result = repair_corrupt_retiring_cache()

    assert result.repaired
    assert result.sidecar is not None
    assert result.sidecar.read_bytes() == original
    assert read_retiring_cache().state is RetiringCacheState.EXACT_V2


@pytest.mark.parametrize("output_json", [False, True], ids=["human", "json"])
def test_repair_reports_the_quarantine_sidecar(tmp_path, monkeypatch, capsys, output_json) -> None:
    import autoskillit.cli.doctor as doctor_module

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    cache = _cache(tmp_path)
    cache.parent.mkdir(parents=True)
    cache.write_text("{not-json")
    monkeypatch.setattr(doctor_module, "_collect_doctor_results", lambda: [])

    doctor_module.run_doctor_repairs(output_json=output_json)

    output = capsys.readouterr().out
    sidecar = next(cache.parent.glob("retiring_cache.corrupt-*.json"))
    if output_json:
        payload = json.loads(output)
        repair = next(
            result for result in payload["results"] if result["check"] == "retiring_cache_repair"
        )
        assert str(sidecar) in repair["message"]
    else:
        assert "info:" in output
        assert str(sidecar) in output
    assert "salvaged=0, quarantined=0" in output


def test_repair_preserves_every_interpretable_record(tmp_path, monkeypatch) -> None:
    from autoskillit.core import read_retiring_cache, repair_corrupt_retiring_cache

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    cache = _write_cache(tmp_path, [_record(index) for index in range(1, 4)])
    original = cache.read_bytes() + b" trailing-garbage"
    cache.write_bytes(original)

    result = repair_corrupt_retiring_cache()
    repaired = read_retiring_cache()

    assert result.salvaged == 3
    assert result.sidecar is not None and result.sidecar.read_bytes() == original
    assert tuple(record.record_id for record in repaired.records) == (
        "record-1",
        "record-2",
        "record-3",
    )


def test_repair_excludes_every_record_with_an_ambiguous_id(tmp_path, monkeypatch) -> None:
    from autoskillit.core import (
        RetiringCacheState,
        read_retiring_cache,
        repair_corrupt_retiring_cache,
    )

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    duplicate = _record(1)
    cache = _write_cache(tmp_path, [duplicate, dict(duplicate), _record(2)])
    original = cache.read_bytes()

    result = repair_corrupt_retiring_cache()
    repaired = read_retiring_cache()

    assert result.salvaged == 1
    assert result.sidecar is not None and result.sidecar.read_bytes() == original
    assert repaired.state is RetiringCacheState.EXACT_V2
    assert tuple(record.record_id for record in repaired.records) == ("record-2",)


def test_repair_is_a_no_op_on_a_healthy_cache(tmp_path, monkeypatch) -> None:
    from autoskillit.core import repair_corrupt_retiring_cache

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    cache = _write_cache(tmp_path, [_record(1)])
    before = cache.read_bytes()

    result = repair_corrupt_retiring_cache()

    assert not result.repaired
    assert cache.read_bytes() == before
    assert not list(cache.parent.glob("retiring_cache.corrupt-*.json"))


def test_repair_refuses_an_unsupported_future_cache(tmp_path, monkeypatch) -> None:
    from autoskillit.core import RetiringCacheState, repair_corrupt_retiring_cache

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    cache = _cache(tmp_path)
    cache.parent.mkdir(parents=True)
    cache.write_text(json.dumps({"schema_version": 3, "records": []}))
    before = cache.read_bytes()

    result = repair_corrupt_retiring_cache()

    assert not result.repaired
    assert result.state is RetiringCacheState.UNSUPPORTED_FUTURE
    assert cache.read_bytes() == before
    assert not list(cache.parent.glob("retiring_cache.corrupt-*.json"))


@pytest.mark.parametrize("state", ["corrupt", "future"])
def test_doctor_without_repair_never_mutates(tmp_path, monkeypatch, capsys, state) -> None:
    import autoskillit.cli.doctor as doctor_module

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    cache = _cache(tmp_path)
    cache.parent.mkdir(parents=True)
    if state == "corrupt":
        cache.write_text("{not-json")
    else:
        cache.write_text(json.dumps({"schema_version": 3, "records": []}))
    before = cache.read_bytes()
    monkeypatch.setattr(doctor_module, "_collect_doctor_results", lambda: [])

    doctor_module.run_doctor(output_json=False)
    capsys.readouterr()

    assert cache.read_bytes() == before
    assert not list(cache.parent.glob("retiring_cache.corrupt-*.json"))
