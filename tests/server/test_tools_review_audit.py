"""Server-owned MCP boundary tests for review-audit ingestion."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from autoskillit.core import ChildTaskTranscript, DiffAnchorAuthority

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]

_TOOL_NAMES = frozenset({"plan_review_audit", "collect_review_audit", "finalize_review_audit"})
_HEAD_SHA = "a" * 40


def _write_gate_authority(
    tmp_path: Path,
    *,
    gate_state: str = "valid_false",
    with_anchor: bool = False,
) -> dict[str, str]:
    checkout_root = tmp_path / "checkout"
    checkout_root.mkdir()
    review_output_dir = checkout_root / "review"
    review_output_dir.mkdir()
    snapshot_dir = review_output_dir / "gate_snapshot.x"
    snapshot_dir.mkdir()

    metrics_path = snapshot_dir / "metrics.before"
    metrics_path.write_text(json.dumps({"_head_sha": _HEAD_SHA}), encoding="utf-8")
    diff_path = snapshot_dir / "annotated_diff"
    diff_path.write_text("snapshot header\n[L42]+changed line\n", encoding="utf-8")
    lines_path = snapshot_dir / "valid_lines"
    lines_path.write_text(json.dumps({"src/app.py": [42, 43]}), encoding="utf-8")

    authority_path = review_output_dir / "gate_authority.json"
    authority = {
        "state": gate_state,
        "reason_code": "none",
        "experimental_audit_state": "pending" if gate_state == "valid_true" else "not_required",
        "snapshot": {
            "head_sha": _HEAD_SHA,
            "base_sha": "b" * 40,
            "merge_base_sha": "c" * 40,
            "base_repo_full_name": "acme/repo",
            "diff_sha256": "d" * 64,
            "profile_id": "local_git_pinned_v1",
        },
        "annotation_generation_id": "generation-1",
        "authority_path": str(authority_path),
        "snapshot_dir": str(snapshot_dir),
        "metrics_marker_snapshot_path": str(metrics_path),
        "annotated_diff_snapshot_path": str(diff_path),
        "hunk_ranges_snapshot_path": str(snapshot_dir / "hunk_ranges"),
        "valid_lines_snapshot_path": str(lines_path),
        "mode": "local",
        "checkout_root": str(checkout_root),
        "pr_number": "7",
        "diff_metrics_path": str(snapshot_dir / "metrics-source.json"),
        "annotated_diff_path": str(snapshot_dir / "diff-source.txt"),
        "hunk_ranges_path": str(snapshot_dir / "ranges-source.json"),
        "valid_lines_path": str(snapshot_dir / "lines-source.json"),
    }
    authority_path.write_text(json.dumps(authority), encoding="utf-8")

    anchor_path = ""
    if with_anchor:
        anchor_file = review_output_dir / "anchor_authority.json"
        anchor = DiffAnchorAuthority.authoritative(
            repository="acme/repo",
            pr_number=7,
            head_sha=_HEAD_SHA,
            generation_id="generation-1",
            right_side_lines={"src/app.py": [42, 43]},
            left_side_lines={},
        )
        anchor_file.write_text(json.dumps(anchor.to_wire()), encoding="utf-8")
        anchor_path = str(anchor_file)

    return {
        "authority_path": str(authority_path),
        "review_output_dir": str(review_output_dir),
        "checkout_root": str(checkout_root),
        "anchor_authority_path": anchor_path,
    }


async def _plan_manifest(
    tmp_path: Path, *, gate_state: str = "valid_false", with_anchor: bool = False
):
    from autoskillit.server.tools.tools_review_audit import plan_review_audit

    inputs = _write_gate_authority(tmp_path, gate_state=gate_state, with_anchor=with_anchor)
    encoded = await plan_review_audit(
        authority_path=inputs["authority_path"],
        review_output_dir=inputs["review_output_dir"],
        anchor_authority_path=inputs["anchor_authority_path"],
        repository="acme/repo",
    )
    result = json.loads(encoded)
    assert result["success"] is True
    return inputs, result


def _transcript(handle: str, slot: dict[str, Any], final_text: str) -> ChildTaskTranscript:
    return ChildTaskTranscript(
        child_id=handle,
        transcript_locator=f"test://{handle}",
        assignment_prompt="",
        assignment_label=str(slot["slot_token"]),
        terminal=True,
        final_text=final_text,
        final_stop_reason="end_turn",
        output_limit_stops=0,
    )


class _FakeLocator:
    def __init__(self, transcripts: dict[str, ChildTaskTranscript]) -> None:
        self.transcripts = transcripts
        self.calls: list[str] = []

    def read_child_task(self, handle: str) -> ChildTaskTranscript | None:
        self.calls.append(handle)
        return self.transcripts.get(handle)


class _FakeBackend:
    def __init__(self, locator: _FakeLocator) -> None:
        self.locator = locator

    def session_locator(self) -> _FakeLocator:
        return self.locator


def _install_backend(monkeypatch: pytest.MonkeyPatch, backend: object) -> None:
    import autoskillit.server as server

    monkeypatch.setattr(server, "_get_ctx", lambda: SimpleNamespace(backend=backend))


@pytest.mark.anyio
async def test_review_audit_tools_are_hidden_when_conditional_tags_are_disabled() -> None:
    from autoskillit.server import mcp

    visible = {tool.name for tool in await mcp.list_tools()}
    assert _TOOL_NAMES.isdisjoint(visible)


@pytest.mark.anyio
async def test_review_audit_tools_are_registered_and_visible_to_headless_skills(
    headless_enabled,
) -> None:
    from autoskillit.server import mcp
    from autoskillit.server.lifecycle._session_scope import SCOPE_ANY, TOOL_SESSION_SCOPES

    visible = {tool.name: tool for tool in await mcp.list_tools()}
    assert _TOOL_NAMES <= visible.keys()
    for name in _TOOL_NAMES:
        assert {"autoskillit", "kitchen", "kitchen-core", "headless"} <= visible[name].tags
        assert TOOL_SESSION_SCOPES[name] is SCOPE_ANY


@pytest.mark.anyio
async def test_plan_review_audit_returns_manifest_and_rejects_outside_authority(
    tmp_path: Path,
) -> None:
    from autoskillit.server.tools.tools_review_audit import plan_review_audit
    from autoskillit.smoke_utils.review._constants import _STANDARD_REVIEW_DIMENSIONS

    inputs, result = await _plan_manifest(tmp_path)
    manifest_path = Path(result["manifest_path"])
    assert manifest_path.is_relative_to(Path(inputs["review_output_dir"]))
    slots = result["slots"]
    assert isinstance(slots, list)
    assert {slot["slot_id"] for slot in slots} == set(_STANDARD_REVIEW_DIMENSIONS)

    outside = await plan_review_audit(
        authority_path=str(tmp_path / "outside.json"),
        review_output_dir=inputs["review_output_dir"],
        repository="acme/repo",
    )
    assert json.loads(outside)["success"] is False


@pytest.mark.anyio
async def test_collect_review_audit_uses_injected_locator_for_transcript_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.server.tools.tools_review_audit import collect_review_audit

    _inputs, planned = await _plan_manifest(tmp_path)
    arch_slot = next(slot for slot in planned["slots"] if slot["slot_id"] == "arch")
    handle = "child-arch"
    locator = _FakeLocator({handle: _transcript(handle, arch_slot, "[")})
    _install_backend(monkeypatch, _FakeBackend(locator))

    result = json.loads(
        await collect_review_audit(
            manifest_path=planned["manifest_path"],
            handles={"arch": handle},
        )
    )
    assert result["success"] is True
    assert result["state"] == "relaunch_required"
    relaunch = next(item for item in result["relaunch"] if item["slot_id"] == "arch")
    assert relaunch["reason_code"] == "malformed_json"
    assert locator.calls == [handle]

    invalid = json.loads(
        await collect_review_audit(
            manifest_path=planned["manifest_path"],
            handles=None,  # type: ignore[arg-type]
        )
    )
    assert invalid["success"] is False


@pytest.mark.anyio
async def test_review_audit_collection_and_finalization_require_a_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.server.tools.tools_review_audit import (
        collect_review_audit,
        finalize_review_audit,
    )

    _install_backend(monkeypatch, None)
    collected = json.loads(await collect_review_audit(manifest_path="unused", handles={}))
    finalized = json.loads(
        await finalize_review_audit(
            manifest_path="unused",
            handles={},
            dispositions=[],
            prior_resolved_findings=[],
            final_snapshot_state="fresh",
        )
    )
    expected = {"success": False, "error": "backend_unavailable"}
    assert collected == expected
    assert finalized == expected


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("malformed_slot", "expected_verdict"),
    ((None, "approved"), ("arch", "needs_human")),
)
async def test_finalize_review_audit_derives_verdict_from_transcripts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    malformed_slot: str | None,
    expected_verdict: str,
) -> None:
    from autoskillit.server.tools.tools_review_audit import finalize_review_audit

    _inputs, planned = await _plan_manifest(tmp_path)
    transcripts: dict[str, ChildTaskTranscript] = {}
    handles: dict[str, str] = {}
    for index, slot in enumerate(planned["slots"]):
        slot_id = str(slot["slot_id"])
        handle = f"empty-{index}"
        handles[slot_id] = handle
        output = "[" if slot_id == malformed_slot else "```json\n[]\n```"
        transcripts[handle] = _transcript(handle, slot, output)
    locator = _FakeLocator(transcripts)
    _install_backend(monkeypatch, _FakeBackend(locator))

    result = json.loads(
        await finalize_review_audit(
            manifest_path=planned["manifest_path"],
            handles=handles,
            dispositions=[],
            prior_resolved_findings=[],
            final_snapshot_state="fresh",
        )
    )
    assert result["success"] is True
    assert result["verdict"] == expected_verdict
    if malformed_slot is not None:
        assert result["audit_state"] == "degraded"


@pytest.mark.anyio
async def test_finalize_review_audit_rejects_unknown_candidate_disposition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.server.tools.tools_review_audit import finalize_review_audit
    from tests.smoke_utils._experimental_helpers import _experimental_candidate

    _inputs, planned = await _plan_manifest(tmp_path, gate_state="valid_true", with_anchor=True)
    transcripts: dict[str, ChildTaskTranscript] = {}
    handles: dict[str, str] = {}
    for index, slot in enumerate(planned["slots"]):
        slot_id = str(slot["slot_id"])
        handle = f"candidate-{index}"
        handles[slot_id] = handle
        if slot["kind"] == "experimental":
            candidates = [_experimental_candidate(str(slot["dimension"]))]
            output = f"```json\n{json.dumps(candidates)}\n```"
        else:
            output = "```json\n[]\n```"
        transcripts[handle] = _transcript(handle, slot, output)
    locator = _FakeLocator(transcripts)
    _install_backend(monkeypatch, _FakeBackend(locator))

    result = json.loads(
        await finalize_review_audit(
            manifest_path=planned["manifest_path"],
            handles=handles,
            dispositions=[
                {
                    "candidate_id": "unknown-candidate",
                    "reason_code": "accepted",
                    "explanation": "No such candidate was emitted.",
                }
            ],
            prior_resolved_findings=[],
            final_snapshot_state="fresh",
        )
    )
    assert result["success"] is True
    assert result["audit_state"] == "degraded"
    assert result["disposition_errors"]


def test_review_audit_handler_imports_do_not_shadow_ingestion_functions() -> None:
    from autoskillit.server.tools.tools_review_audit import _handlers
    from autoskillit.smoke_utils import review as review_audit

    assert all(
        value is not review_audit.collect_review_audit for value in vars(_handlers).values()
    )
    assert all(
        value is not review_audit.finalize_review_audit for value in vars(_handlers).values()
    )
