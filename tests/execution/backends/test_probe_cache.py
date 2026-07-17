from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from autoskillit.core import (
    OUTPUT_DISCIPLINE_BLOCK_SHA256,
    OUTPUT_DISCIPLINE_POLICY_VERSION,
    RESPONSE_BACKSTOP_EXEMPTION_REGISTRY_DIGEST,
)
from autoskillit.execution.backends._probe_cache import (
    _SCHEMA_VERSION,
    PROBE_CACHE_TTL,
    PROBE_POLICY_IDENTITY,
    PROBE_SUITE_CONTRACT,
    PROBE_SUITE_CONTRACT_DIGEST,
    ProbeResult,
    read_probe_cache,
    write_probe_cache,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]

_POLICY_IDENTITY = "v1-policy-hash"


def _make_result(
    cli_version: str = "1.0.0",
    policy_identity: str = _POLICY_IDENTITY,
    passed: bool = True,
    failure_detail: str | None = None,
    ts: datetime | None = None,
) -> ProbeResult:
    if ts is None:
        ts = datetime.now(UTC)
    return ProbeResult(
        cli_version=cli_version,
        policy_identity=policy_identity,
        passed=passed,
        failure_detail=failure_detail,
        probe_timestamp=ts.isoformat(),
    )


def _write_raw_cache(path: Path, entries: dict, *, schema_version: int = _SCHEMA_VERSION) -> None:
    payload = {"entries": entries, "schema_version": schema_version}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


class TestReadProbeCache:
    def test_returns_none_on_missing_file(self, tmp_path: Path) -> None:
        assert read_probe_cache(tmp_path / "nope.json", "1.0.0", _POLICY_IDENTITY) is None

    def test_returns_none_on_corrupt_json(self, tmp_path: Path) -> None:
        p = tmp_path / "cache.json"
        p.write_text("NOT VALID JSON")
        assert read_probe_cache(p, "1.0.0", _POLICY_IDENTITY) is None

    def test_returns_none_on_stale_entry(self, tmp_path: Path) -> None:
        stale_ts = (datetime.now(UTC) - PROBE_CACHE_TTL - timedelta(hours=1)).isoformat()
        _write_raw_cache(
            tmp_path / "cache.json",
            {
                "1.0.0": {
                    "policy_identity": _POLICY_IDENTITY,
                    "passed": True,
                    "failure_detail": None,
                    "probe_timestamp": stale_ts,
                },
            },
        )
        assert read_probe_cache(tmp_path / "cache.json", "1.0.0", _POLICY_IDENTITY) is None

    def test_returns_none_on_version_mismatch(self, tmp_path: Path) -> None:
        fresh_ts = datetime.now(UTC).isoformat()
        _write_raw_cache(
            tmp_path / "cache.json",
            {
                "2.0.0": {
                    "policy_identity": _POLICY_IDENTITY,
                    "passed": True,
                    "failure_detail": None,
                    "probe_timestamp": fresh_ts,
                },
            },
        )
        assert read_probe_cache(tmp_path / "cache.json", "1.0.0", _POLICY_IDENTITY) is None

    def test_returns_none_on_policy_version_mismatch(self, tmp_path: Path) -> None:
        fresh_ts = datetime.now(UTC).isoformat()
        _write_raw_cache(
            tmp_path / "cache.json",
            {
                "1.0.0": {
                    "policy_identity": "v1-policy-hash",
                    "passed": True,
                    "failure_detail": None,
                    "probe_timestamp": fresh_ts,
                },
            },
        )
        assert read_probe_cache(tmp_path / "cache.json", "1.0.0", "v2-policy-hash") is None

    def test_returns_none_on_policy_hash_mismatch(self, tmp_path: Path) -> None:
        fresh_ts = datetime.now(UTC).isoformat()
        _write_raw_cache(
            tmp_path / "cache.json",
            {
                "1.0.0": {
                    "policy_identity": "v1-policy-hash-a",
                    "passed": True,
                    "failure_detail": None,
                    "probe_timestamp": fresh_ts,
                },
            },
        )
        assert read_probe_cache(tmp_path / "cache.json", "1.0.0", "v1-policy-hash-b") is None

    def test_returns_probe_result_on_fresh_hit(self, tmp_path: Path) -> None:
        fresh_ts = datetime.now(UTC).isoformat()
        _write_raw_cache(
            tmp_path / "cache.json",
            {
                "1.0.0": {
                    "policy_identity": _POLICY_IDENTITY,
                    "passed": True,
                    "failure_detail": None,
                    "probe_timestamp": fresh_ts,
                },
            },
        )
        result = read_probe_cache(tmp_path / "cache.json", "1.0.0", _POLICY_IDENTITY)
        assert result is not None
        assert result.passed is True
        assert result.cli_version == "1.0.0"
        assert result.policy_identity == _POLICY_IDENTITY

    def test_returns_none_on_schema_version_mismatch(self, tmp_path: Path) -> None:
        fresh_ts = datetime.now(UTC).isoformat()
        _write_raw_cache(
            tmp_path / "cache.json",
            {
                "1.0.0": {
                    "policy_identity": _POLICY_IDENTITY,
                    "passed": True,
                    "failure_detail": None,
                    "probe_timestamp": fresh_ts,
                }
            },
            schema_version=999,
        )
        assert read_probe_cache(tmp_path / "cache.json", "1.0.0", _POLICY_IDENTITY) is None

    def test_returns_none_on_naive_timestamp(self, tmp_path: Path) -> None:
        _write_raw_cache(
            tmp_path / "cache.json",
            {
                "1.0.0": {
                    "policy_identity": _POLICY_IDENTITY,
                    "passed": True,
                    "failure_detail": None,
                    "probe_timestamp": "2000-01-01T00:00:00",
                },
            },
        )
        assert read_probe_cache(tmp_path / "cache.json", "1.0.0", _POLICY_IDENTITY) is None


def test_probe_policy_identity_uses_output_discipline_authorities() -> None:
    assert PROBE_POLICY_IDENTITY == (
        f"v{OUTPUT_DISCIPLINE_POLICY_VERSION}-{OUTPUT_DISCIPLINE_BLOCK_SHA256}-"
        f"{RESPONSE_BACKSTOP_EXEMPTION_REGISTRY_DIGEST}-{PROBE_SUITE_CONTRACT_DIGEST}"
    )
    assert PROBE_SUITE_CONTRACT == (
        "generated-codex-child-v1",
        "deep-investigate-codex-v2",
        "deep-investigate-claude-200k-v2",
    )


def test_probe_cache_schema_is_version_two() -> None:
    assert _SCHEMA_VERSION == 2


class TestWriteProbeCache:
    def test_creates_file(self, tmp_path: Path) -> None:
        p = tmp_path / "cache.json"
        write_probe_cache(p, _make_result())
        assert p.exists()
        raw = json.loads(p.read_text())
        assert "entries" in raw
        assert "1.0.0" in raw["entries"]
        assert raw["entries"]["1.0.0"]["policy_identity"] == _POLICY_IDENTITY

    def test_preserves_other_entries(self, tmp_path: Path) -> None:
        p = tmp_path / "cache.json"
        write_probe_cache(p, _make_result(cli_version="1.0.0"))
        write_probe_cache(p, _make_result(cli_version="2.0.0"))
        raw = json.loads(p.read_text())
        assert "1.0.0" in raw["entries"]
        assert "2.0.0" in raw["entries"]

    def test_swallows_oserror(self, tmp_path: Path) -> None:
        p = tmp_path / "readonly-dir" / "cache.json"
        (tmp_path / "readonly-dir").mkdir()
        (tmp_path / "readonly-dir").chmod(0o444)
        try:
            write_probe_cache(p, _make_result())
        finally:
            (tmp_path / "readonly-dir").chmod(0o755)
        assert not p.exists()

    def test_overwrites_same_version(self, tmp_path: Path) -> None:
        p = tmp_path / "cache.json"
        write_probe_cache(p, _make_result(cli_version="1.0.0", passed=True))
        write_probe_cache(p, _make_result(cli_version="1.0.0", passed=False))
        raw = json.loads(p.read_text())
        assert raw["entries"]["1.0.0"]["passed"] is False
