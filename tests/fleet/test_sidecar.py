import json
from pathlib import Path

import pytest

from autoskillit.fleet.sidecar import (
    IssueSidecarEntry,
    append_sidecar_entry,
    compute_remaining_issues,
    read_sidecar,
    read_sidecar_from_path,
    sidecar_path,
)

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]

URL1 = "https://github.com/org/repo/issues/101"
URL2 = "https://github.com/org/repo/issues/102"
URL3 = "https://github.com/org/repo/issues/103"
TS = "2026-04-27T12:00:00Z"


class TestSidecarPath:
    def test_deterministic(self, tmp_path: Path) -> None:
        assert sidecar_path("abc-123", tmp_path) == sidecar_path("abc-123", tmp_path)

    def test_contains_dispatch_id(self, tmp_path: Path) -> None:
        p = sidecar_path("my-dispatch", tmp_path)
        assert "my-dispatch_issues.jsonl" in p.name

    def test_in_dispatches_dir(self, tmp_path: Path) -> None:
        p = sidecar_path("d1", tmp_path)
        assert p.parent.name == "dispatches"


class TestAppendSidecarEntry:
    def test_creates_file_on_first_append(self, tmp_path: Path) -> None:
        entry = IssueSidecarEntry(issue_url=URL1, status="completed", ts=TS)
        append_sidecar_entry("d1", entry, tmp_path)
        assert sidecar_path("d1", tmp_path).exists()

    def test_multiple_appends_produce_multiple_lines(self, tmp_path: Path) -> None:
        for url in [URL1, URL2, URL3]:
            append_sidecar_entry(
                "d2", IssueSidecarEntry(issue_url=url, status="completed", ts=TS), tmp_path
            )
        lines = [ln for ln in sidecar_path("d2", tmp_path).read_text().splitlines() if ln.strip()]
        assert len(lines) == 3

    def test_appended_line_is_valid_json(self, tmp_path: Path) -> None:
        entry = IssueSidecarEntry(issue_url=URL1, status="failed", reason="tests failed", ts=TS)
        append_sidecar_entry("d3", entry, tmp_path)
        parsed = json.loads(sidecar_path("d3", tmp_path).read_text().strip())
        assert parsed["issue_url"] == URL1
        assert parsed["status"] == "failed"
        assert parsed["reason"] == "tests failed"

    def test_optional_fields_omitted_when_none(self, tmp_path: Path) -> None:
        entry = IssueSidecarEntry(issue_url=URL1, status="completed", ts=TS)
        append_sidecar_entry("d4", entry, tmp_path)
        parsed = json.loads(sidecar_path("d4", tmp_path).read_text().strip())
        assert "pr_url" not in parsed or parsed.get("pr_url") is None
        assert "reason" not in parsed or parsed.get("reason") is None

    def test_pr_url_written_for_completed(self, tmp_path: Path) -> None:
        entry = IssueSidecarEntry(
            issue_url=URL1,
            status="completed",
            pr_url="https://github.com/org/repo/pull/200",
            ts=TS,
        )
        append_sidecar_entry("d5", entry, tmp_path)
        parsed = json.loads(sidecar_path("d5", tmp_path).read_text().strip())
        assert parsed["pr_url"] == "https://github.com/org/repo/pull/200"


class TestReadSidecar:
    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        assert read_sidecar("nonexistent", tmp_path) == []

    def test_empty_file_returns_empty(self, tmp_path: Path) -> None:
        p = sidecar_path("d6", tmp_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("")
        assert read_sidecar("d6", tmp_path) == []

    def test_truncated_line_skipped(self, tmp_path: Path) -> None:
        p = sidecar_path("d7", tmp_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        good = json.dumps({"issue_url": URL1, "status": "completed", "ts": TS})
        p.write_text(f'{good}\n{{"issue_url":"truncated-bad-json\n')
        entries = read_sidecar("d7", tmp_path)
        assert len(entries) == 1
        assert entries[0].issue_url == URL1

    def test_blank_lines_skipped(self, tmp_path: Path) -> None:
        p = sidecar_path("d8", tmp_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        good = json.dumps({"issue_url": URL1, "status": "completed", "ts": TS})
        p.write_text(f"{good}\n\n\n")
        assert len(read_sidecar("d8", tmp_path)) == 1

    def test_round_trip_fidelity(self, tmp_path: Path) -> None:
        entry = IssueSidecarEntry(
            issue_url=URL1,
            status="completed",
            pr_url="https://github.com/org/repo/pull/200",
            ts=TS,
        )
        append_sidecar_entry("d9", entry, tmp_path)
        [result] = read_sidecar("d9", tmp_path)
        assert result.issue_url == entry.issue_url
        assert result.status == entry.status
        assert result.pr_url == entry.pr_url
        assert result.ts == entry.ts


class TestComputeRemainingIssues:
    def test_empty_sidecar_returns_all(self, tmp_path: Path) -> None:
        originals = [URL1, URL2, URL3]
        assert compute_remaining_issues("d10", originals, tmp_path) == originals

    def test_completed_removed(self, tmp_path: Path) -> None:
        append_sidecar_entry(
            "d11", IssueSidecarEntry(issue_url=URL1, status="completed", ts=TS), tmp_path
        )
        assert compute_remaining_issues("d11", [URL1, URL2], tmp_path) == [URL2]

    def test_failed_removed(self, tmp_path: Path) -> None:
        append_sidecar_entry(
            "d12", IssueSidecarEntry(issue_url=URL1, status="failed", reason="x", ts=TS), tmp_path
        )
        assert compute_remaining_issues("d12", [URL1, URL2], tmp_path) == [URL2]

    def test_preserves_original_order(self, tmp_path: Path) -> None:
        originals = [URL1, URL2, URL3]
        append_sidecar_entry(
            "d13", IssueSidecarEntry(issue_url=URL2, status="completed", ts=TS), tmp_path
        )
        assert compute_remaining_issues("d13", originals, tmp_path) == [URL1, URL3]

    def test_all_done_returns_empty(self, tmp_path: Path) -> None:
        for url in [URL1, URL2]:
            append_sidecar_entry(
                "d14", IssueSidecarEntry(issue_url=url, status="completed", ts=TS), tmp_path
            )
        assert compute_remaining_issues("d14", [URL1, URL2], tmp_path) == []

    def test_crash_scenario_2_of_4_done(self, tmp_path: Path) -> None:
        originals = [URL1, URL2, URL3, "https://github.com/org/repo/issues/104"]
        for url in originals[:2]:
            append_sidecar_entry(
                "d15", IssueSidecarEntry(issue_url=url, status="completed", ts=TS), tmp_path
            )
        remaining = compute_remaining_issues("d15", originals, tmp_path)
        assert remaining == originals[2:]


class TestReadSidecarFromPath:
    def test_nonexistent_parent_returns_empty(self) -> None:
        result = read_sidecar_from_path(Path("/nonexistent/dir/issues.jsonl"))
        assert result == []

    def test_valid_jsonl_parsed_correctly(self, tmp_path: Path) -> None:
        p = tmp_path / "issues.jsonl"
        lines = [
            json.dumps({"issue_url": URL1, "status": "completed", "ts": TS}),
            json.dumps(
                {"issue_url": URL2, "status": "failed", "reason": "tests failed", "ts": TS}
            ),
        ]
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")

        entries = read_sidecar_from_path(p)

        assert len(entries) == 2
        assert all(isinstance(e, IssueSidecarEntry) for e in entries)
        assert entries[0].issue_url == URL1
        assert entries[1].issue_url == URL2
