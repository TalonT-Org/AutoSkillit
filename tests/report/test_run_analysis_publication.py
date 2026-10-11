"""Publication and completion contracts for frozen run analyses."""

from __future__ import annotations

import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier
from typing import Any

import pytest

from autoskillit.core import ArtifactLease, ArtifactLeaseContention
from autoskillit.execution import REPORT_INDEX_SCHEMA_VERSION
from autoskillit.report.deck import _html as deck_html
from autoskillit.report.deck._analysis import (
    _json_bytes,
    finish_run_analysis,
    prepare_run_analysis,
    write_run_analysis_packet,
)
from tests.report._fixtures import session_row, token_measure

pytestmark = [pytest.mark.medium]

_OUTPUT_FILES = {"measurements.json", "findings.json", "report.html", "analysis.json"}
_GENERATED_AT = datetime(2026, 10, 11, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def frozen_packet() -> dict[str, Any]:
    rows = [
        session_row(
            "run-a",
            schema_version=REPORT_INDEX_SCHEMA_VERSION,
            session_id="native-run-a",
            time_ms=1_800_000_000_000,
            timestamp="2026-10-10T12:00:00Z",
            harness="claude-code",
            provider="anthropic",
            model="model-a",
            skill="make-plan",
            recipe="recipe-a",
            step="scope",
            level="skill",
            success=True,
            input_tokens=token_measure(1),
            output_tokens=token_measure(0),
        ),
        session_row(
            "run-b",
            schema_version=REPORT_INDEX_SCHEMA_VERSION,
            session_id="native-run-b",
            time_ms=1_800_000_000_001,
            timestamp="2026-10-10T12:00:01Z",
            harness="claude-code",
            provider="anthropic",
            model="model-a",
            skill="make-plan",
            recipe="recipe-a",
            step="scope",
            level="skill",
            success=True,
            input_tokens=token_measure(2),
            output_tokens=token_measure(0),
        ),
    ]
    return prepare_run_analysis(
        rows,
        index_schema_version=REPORT_INDEX_SCHEMA_VERSION,
        render_bundle=deck_html.load_render_bundle(),
    )


def _dated_dir(root: Path, token: str) -> Path:
    return root / f"2026-10-11_120000_{token}"


def _candidate(
    packet: dict[str, Any],
    status: dict[str, Any],
    *,
    role: str = "research-synthesizer",
    model: str | None = "gpt-test",
    prompt: str | None = "Compare the frozen cohort evidence.",
    text: str = "The observed input average differs across the retained runs.",
) -> dict[str, Any]:
    evidence_id = next(
        key for key, item in packet["evidence"].items() if item["view_id"] == "cohort"
    )
    return {
        "snapshot_digest": status["snapshot_digest"],
        "interpreter": {"role": role, "model": model, "prompt": prompt},
        "findings": [{"view_id": "cohort", "text": text, "evidence_ids": [evidence_id]}],
    }


def _write_candidate(status: dict[str, Any], candidate: dict[str, Any]) -> Path:
    path = Path(status["candidate_path"])
    path.write_bytes(_json_bytes(candidate))
    return path


def _finish(output_dir: Path, *, generated_at: datetime = _GENERATED_AT) -> dict[str, Any]:
    return finish_run_analysis(output_dir, analysis_generated_at=generated_at)


def _complete(
    output_dir: Path,
    packet: dict[str, Any],
    *,
    candidate: dict[str, Any] | None = None,
    generated_at: datetime = _GENERATED_AT,
) -> dict[str, Any]:
    status = write_run_analysis_packet(output_dir, packet)
    _write_candidate(status, candidate or _candidate(packet, status))
    _finish(output_dir, generated_at=generated_at)
    return status


def _published_bytes(output_dir: Path) -> dict[str, bytes]:
    return {name: (output_dir / name).read_bytes() for name in sorted(_OUTPUT_FILES)}


def _manifest_without_timestamp(content: bytes) -> bytes:
    manifest = json.loads(content)
    manifest["generated_at_ms"] = 0
    return _json_bytes(manifest)


def _report_without_timestamp(content: bytes) -> bytes:
    html = content.decode("utf-8")
    match = re.search(r'<script[^>]*id="deck-data"[^>]*>(.*?)</script>', html, re.DOTALL)
    assert match is not None
    payload = json.loads(match.group(1))
    payload["analysis"]["generated_at_ms"] = 0
    return (html[: match.start(1)] + deck_html.embed_json(payload) + html[match.end(1) :]).encode(
        "utf-8"
    )


def _report_payload(content: bytes) -> dict[str, Any]:
    html = content.decode("utf-8")
    match = re.search(r'<script[^>]*id="deck-data"[^>]*>(.*?)</script>', html, re.DOTALL)
    assert match is not None
    return json.loads(match.group(1))


def test_repeat_completion_reuses_interpretation_and_preserves_prior_files(
    tmp_path: Path, frozen_packet: dict[str, Any]
) -> None:
    root = tmp_path / "analyses"
    first_dir = _dated_dir(root, "first")
    first_status = _complete(first_dir, frozen_packet, generated_at=_GENERATED_AT)
    first_bytes = _published_bytes(first_dir)

    second_dir = _dated_dir(root, "second")
    second_status = write_run_analysis_packet(second_dir, frozen_packet)

    assert second_status["interpretation_required"] is False
    assert second_status["input_fingerprint"] == first_status["input_fingerprint"]
    assert second_status["snapshot_digest"] == first_status["snapshot_digest"]
    assert Path(second_status["candidate_path"]).read_bytes() == first_bytes["findings.json"]

    _finish(second_dir, generated_at=datetime(2026, 10, 11, 12, 0, 0, 124_000, tzinfo=UTC))
    second_bytes = _published_bytes(second_dir)

    assert set(path.name for path in first_dir.iterdir()) == _OUTPUT_FILES
    assert set(path.name for path in second_dir.iterdir()) == _OUTPUT_FILES
    assert _published_bytes(first_dir) == first_bytes
    assert second_bytes["measurements.json"] == first_bytes["measurements.json"]
    assert second_bytes["findings.json"] == first_bytes["findings.json"]
    first_manifest = json.loads(first_bytes["analysis.json"])
    second_manifest = json.loads(second_bytes["analysis.json"])
    assert first_manifest["generated_at_ms"] != second_manifest["generated_at_ms"]
    assert set(first_manifest) == {
        "input_fingerprint",
        "snapshot_digest",
        "findings_digest",
        "generated_at_ms",
        "anchor_ms",
        "coverage",
        "cohort",
        "interpreter",
    }
    assert first_manifest["anchor_ms"] == frozen_packet["payload"]["generated_at_ms"]
    assert (
        first_manifest["findings_digest"]
        == hashlib.sha256(first_bytes["findings.json"]).hexdigest()
    )
    assert (
        _report_payload(first_bytes["report.html"])["analysis"]["generated_at_ms"]
        == (first_manifest["generated_at_ms"])
    )
    assert _manifest_without_timestamp(
        first_bytes["analysis.json"]
    ) == _manifest_without_timestamp(second_bytes["analysis.json"])
    assert _report_without_timestamp(first_bytes["report.html"]) == _report_without_timestamp(
        second_bytes["report.html"]
    )
    second_before_retry = _published_bytes(second_dir)
    with pytest.raises(ValueError, match="already completed"):
        _finish(second_dir)
    assert _published_bytes(second_dir) == second_before_retry


def test_first_completed_candidate_wins_provenance(
    tmp_path: Path, frozen_packet: dict[str, Any]
) -> None:
    root = tmp_path / "analyses"
    first_dir = _dated_dir(root, "candidate-a")
    second_dir = _dated_dir(root, "candidate-b")
    first_status = write_run_analysis_packet(first_dir, frozen_packet)
    second_status = write_run_analysis_packet(second_dir, frozen_packet)
    first = _candidate(
        frozen_packet, first_status, role="first-interpreter", text="First finding."
    )
    second = _candidate(
        frozen_packet, second_status, role="second-interpreter", text="Second finding."
    )
    _write_candidate(first_status, first)
    _write_candidate(second_status, second)

    _finish(first_dir, generated_at=_GENERATED_AT)
    _finish(second_dir, generated_at=_GENERATED_AT)

    first_findings = json.loads((first_dir / "findings.json").read_bytes())
    second_findings = json.loads((second_dir / "findings.json").read_bytes())
    second_manifest = json.loads((second_dir / "analysis.json").read_bytes())
    assert first_findings == first
    assert second_findings == first
    assert second_manifest["interpreter"] == first["interpreter"]


@pytest.mark.parametrize("invalid", ("extra-key", "bad-digest", "cross-view", "empty-findings"))
def test_invalid_candidates_never_publish_a_manifest(
    tmp_path: Path, frozen_packet: dict[str, Any], invalid: str
) -> None:
    output_dir = _dated_dir(tmp_path / "analyses", invalid)
    status = write_run_analysis_packet(output_dir, frozen_packet)
    candidate = _candidate(frozen_packet, status)

    if invalid == "extra-key":
        candidate["unexpected"] = True
    elif invalid == "bad-digest":
        candidate["snapshot_digest"] = "0" * 64
    elif invalid == "cross-view":
        finding = candidate["findings"][0]
        other = next(
            key
            for key, item in frozen_packet["evidence"].items()
            if item["view_id"] != finding["view_id"]
        )
        finding["evidence_ids"] = [other]
    else:
        candidate["findings"] = []
    _write_candidate(status, candidate)

    with pytest.raises(ValueError):
        _finish(output_dir)

    assert not (output_dir / "analysis.json").exists()


def test_empty_corpus_requires_explicit_no_observations_finding(
    tmp_path: Path,
) -> None:
    packet = prepare_run_analysis(
        [],
        index_schema_version=REPORT_INDEX_SCHEMA_VERSION,
        render_bundle=deck_html.load_render_bundle(),
    )
    output_dir = _dated_dir(tmp_path / "empty", "snapshot")
    status = write_run_analysis_packet(output_dir, packet)
    candidate = {
        "snapshot_digest": status["snapshot_digest"],
        "interpreter": {"role": "research-synthesizer", "model": None, "prompt": None},
        "findings": [
            {
                "view_id": "cohort",
                "text": "No observations in the frozen corpus.",
                "evidence_ids": [],
            }
        ],
    }
    _write_candidate(status, candidate)

    _finish(output_dir)

    assert json.loads((output_dir / "findings.json").read_bytes()) == candidate


@pytest.mark.parametrize("name", ("analysis-output", "2026-13-11_120000_token"))
def test_writer_requires_a_dated_new_directory(
    tmp_path: Path,
    frozen_packet: dict[str, Any],
    name: str,
) -> None:
    root = tmp_path / "analyses"
    malformed = root / name
    with pytest.raises(ValueError, match="YYYY-MM-DD_HHMMSS"):
        write_run_analysis_packet(malformed, frozen_packet)
    assert not malformed.exists()

    existing = _dated_dir(root, "existing")
    existing.mkdir(parents=True)
    marker = existing / "keep.txt"
    marker.write_text("existing", encoding="utf-8")
    with pytest.raises(FileExistsError):
        write_run_analysis_packet(existing, frozen_packet)
    assert marker.read_text(encoding="utf-8") == "existing"


@pytest.mark.parametrize("corrupt", ("measurements.json", "findings.json", "report.html"))
def test_corrupt_completed_artifacts_cannot_supply_reused_findings(
    tmp_path: Path, frozen_packet: dict[str, Any], corrupt: str
) -> None:
    root = tmp_path / "analyses"
    first_dir = _dated_dir(root, "completed")
    _complete(first_dir, frozen_packet)
    retained = first_dir / corrupt
    retained.write_bytes(retained.read_bytes() + b" ")
    next_dir = _dated_dir(root, "retry")

    with pytest.raises(ValueError):
        write_run_analysis_packet(next_dir, frozen_packet)

    assert not next_dir.exists()


def test_finish_uses_frozen_render_bundle_without_live_asset_lookup(
    tmp_path: Path, frozen_packet: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def unexpected_asset_read(asset: str) -> str:
        pytest.fail(f"publication read live asset {asset!r} after analysis was frozen")

    monkeypatch.setattr(deck_html, "_asset_text", unexpected_asset_read)
    output_dir = _dated_dir(tmp_path / "analyses", "frozen-assets")
    status = write_run_analysis_packet(output_dir, frozen_packet)
    _write_candidate(status, _candidate(frozen_packet, status))

    _finish(output_dir)

    assert (output_dir / "report.html").exists()


def test_finish_contention_leaves_unfinished_files_unchanged_and_can_retry(
    tmp_path: Path,
    frozen_packet: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    acquire = ArtifactLease.acquire_exclusive

    def acquire_without_wait(cls: type[ArtifactLease], lock_path: Path, *, timeout: float) -> Any:
        return acquire(lock_path, timeout=0.0)

    monkeypatch.setattr(ArtifactLease, "acquire_exclusive", classmethod(acquire_without_wait))
    root = tmp_path / "analyses"
    output_dir = _dated_dir(root, "contended")
    status = write_run_analysis_packet(output_dir, frozen_packet)
    candidate_path = _write_candidate(status, _candidate(frozen_packet, status))
    unfinished = {
        name: (output_dir / name).read_bytes()
        for name in ("measurements.json", candidate_path.name)
    }
    winner_dir = _dated_dir(root, "winner")
    winner_status = write_run_analysis_packet(winner_dir, frozen_packet)
    _write_candidate(
        winner_status,
        _candidate(frozen_packet, winner_status, role="winner", text="Winning finding."),
    )

    with ArtifactLease.acquire_exclusive(root / ".publication.lock", timeout=0.0):
        with pytest.raises(ArtifactLeaseContention):
            _finish(output_dir)
        assert {name: (output_dir / name).read_bytes() for name in unfinished} == unfinished
        assert not (output_dir / "analysis.json").exists()

    _finish(winner_dir)
    _finish(output_dir)
    assert set(path.name for path in output_dir.iterdir()) == _OUTPUT_FILES
    assert (output_dir / "findings.json").read_bytes() == (
        winner_dir / "findings.json"
    ).read_bytes()


def test_concurrent_finishes_keep_one_candidate_provenance(
    tmp_path: Path, frozen_packet: dict[str, Any]
) -> None:
    root = tmp_path / "analyses"
    output_dirs = (_dated_dir(root, "race-a"), _dated_dir(root, "race-b"))
    statuses = [write_run_analysis_packet(path, frozen_packet) for path in output_dirs]
    candidates = [
        _candidate(frozen_packet, statuses[0], role="race-a", text="Finding A."),
        _candidate(frozen_packet, statuses[1], role="race-b", text="Finding B."),
    ]
    for status, candidate in zip(statuses, candidates, strict=True):
        _write_candidate(status, candidate)
    barrier = Barrier(2)

    def finish_after_barrier(path: Path) -> dict[str, Any]:
        barrier.wait(timeout=5)
        return _finish(path)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(finish_after_barrier, path) for path in output_dirs]
        results = [future.result(timeout=10) for future in futures]

    findings = [json.loads((path / "findings.json").read_bytes()) for path in output_dirs]
    assert all(result["completed"] for result in results)
    assert findings[0] == findings[1]
    assert findings[0] in candidates
