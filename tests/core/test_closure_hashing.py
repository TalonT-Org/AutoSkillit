"""Tests for canonical hash utilities."""

from __future__ import annotations

import hashlib

import pytest

from autoskillit.core.closure_hashing import (
    compute_canonical_hash,
    compute_file_hash,
    compute_report_hash,
    compute_request_hash,
    compute_row_hash,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


class TestComputeCanonicalHash:
    def test_deterministic(self) -> None:
        payload = {"b": 2, "a": 1, "c": 3}
        first = compute_canonical_hash(payload)
        second = compute_canonical_hash(payload)
        assert first == second

    def test_domain_separated(self) -> None:
        payload = {"x": 1}
        h_a = compute_canonical_hash(payload, domain="domain-a")
        h_b = compute_canonical_hash(payload, domain="domain-b")
        assert h_a != h_b

    def test_structural_order_independent(self) -> None:
        p1 = {"a": 1, "b": 2, "c": {"x": 1, "y": 2}}
        p2 = {"c": {"y": 2, "x": 1}, "b": 2, "a": 1}
        assert compute_canonical_hash(p1) == compute_canonical_hash(p2)

    def test_format(self) -> None:
        h = compute_canonical_hash({"x": 1})
        assert h.startswith("sha256:")
        digest = h.removeprefix("sha256:")
        assert len(digest) == 64
        assert all(c in "0123456789abcdef" for c in digest)

    def test_matches_manual_sha256(self) -> None:
        import json

        payload = {"a": 1, "b": 2}
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        prefix = b"autoskillit-closure-v1\n"
        expected = "sha256:" + hashlib.sha256(prefix + canonical).hexdigest()
        assert compute_canonical_hash(payload) == expected


class TestComputeFileHash:
    def test_matches_raw_sha256(self, tmp_path) -> None:
        import hashlib

        f = tmp_path / "data.txt"
        f.write_bytes(b"hello world")
        expected = "sha256:" + hashlib.sha256(b"hello world").hexdigest()
        assert compute_file_hash(f) == expected
        assert compute_file_hash(str(f)) == expected


class TestComputeRequestHash:
    def test_covers_all_inputs(self) -> None:
        h0 = compute_request_hash(
            "sha256:" + "a" * 64,
            ["sha256:" + "1" * 64],
            "main",
            "diff1",
            "target1",
        )
        h1 = compute_request_hash(
            "sha256:" + "b" * 64,
            ["sha256:" + "1" * 64],
            "main",
            "diff1",
            "target1",
        )
        h2 = compute_request_hash(
            "sha256:" + "a" * 64,
            ["sha256:" + "2" * 64],
            "main",
            "diff1",
            "target1",
        )
        h3 = compute_request_hash(
            "sha256:" + "a" * 64,
            ["sha256:" + "1" * 64],
            "develop",
            "diff1",
            "target1",
        )
        h4 = compute_request_hash(
            "sha256:" + "a" * 64,
            ["sha256:" + "1" * 64],
            "main",
            "diff2",
            "target1",
        )
        h5 = compute_request_hash(
            "sha256:" + "a" * 64,
            ["sha256:" + "1" * 64],
            "main",
            "diff1",
            "target2",
        )
        assert h0 != h1
        assert h0 != h2
        assert h0 != h3
        assert h0 != h4
        assert h0 != h5

    def test_plan_order_matters(self) -> None:
        plan_a = "sha256:" + "1" * 64
        plan_b = "sha256:" + "2" * 64
        h1 = compute_request_hash("sha256:" + "a" * 64, [plan_a, plan_b], "", "", "")
        h2 = compute_request_hash("sha256:" + "a" * 64, [plan_b, plan_a], "", "", "")
        assert h1 != h2


class TestComputeRowHash:
    def test_covers_content(self) -> None:
        base = compute_row_hash("REQ-1", "text", "COVERED", "evidence")
        assert base != compute_row_hash("REQ-2", "text", "COVERED", "evidence")
        assert base != compute_row_hash("REQ-1", "other", "COVERED", "evidence")
        assert base != compute_row_hash("REQ-1", "text", "MISSING", "evidence")
        assert base != compute_row_hash("REQ-1", "text", "COVERED", "different")


class TestComputeReportHash:
    def test_order_sensitive(self) -> None:
        rows = ["sha256:" + str(i).zfill(64) for i in range(2)]
        h1 = compute_report_hash("sha256:" + "0" * 64, rows, "GO")
        h2 = compute_report_hash("sha256:" + "0" * 64, list(reversed(rows)), "GO")
        assert h1 != h2

    def test_covers_request_and_verdict(self) -> None:
        rows: list[str] = []
        h1 = compute_report_hash("sha256:" + "0" * 64, rows, "GO")
        h2 = compute_report_hash("sha256:" + "1" * 64, rows, "GO")
        h3 = compute_report_hash("sha256:" + "0" * 64, rows, "NO GO")
        assert h1 != h2
        assert h1 != h3
