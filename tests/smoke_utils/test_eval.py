from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit.smoke_utils import (
    build_agent_eval_context,
    build_eval_context,
    compile_eval_scorecard,
    parse_agent_eval_manifests,
    parse_eval_manifests,
)

pytestmark = [pytest.mark.medium]


def test_parse_eval_manifests_creates_directory_tree(tmp_path: Path) -> None:
    """parse_eval_manifests creates {canary_id}/ dirs with resolved.json for all canaries."""
    # Manifests are plain arrays, not wrapped in {"canaries": [...]}
    task_file = tmp_path / "task.md"
    task_file.write_text("Fix the bug.")
    canary_manifest = [
        {"id": "c1", "skill": "/autoskillit:research", "task_file": str(task_file)},
        {"id": "c2", "skill": "/autoskillit:research", "task_file": str(task_file)},
    ]
    variant_manifest = [
        {"id": "v1", "label": "variant 1"},
        {"id": "v2", "label": "variant 2"},
    ]
    result = parse_eval_manifests(
        json.dumps(canary_manifest), json.dumps(variant_manifest), str(tmp_path)
    )
    assert result["success"] == "true"
    eval_run_dir = Path(result["eval_run_dir"])
    assert eval_run_dir.exists()
    for c in ("c1", "c2"):
        assert (eval_run_dir / c / "resolved.json").is_file(), f"Missing {c}/resolved.json"
    manifest_index = json.loads((eval_run_dir / "manifest_index.json").read_text())
    assert manifest_index["canary_ids"] == ["c1", "c2"]
    assert manifest_index["variant_ids"] == ["v1", "v2"]


def test_parse_eval_manifests_writes_resolved_files(tmp_path: Path) -> None:
    """Resolved files contain inlined task_text, detection_criteria, and gap_description."""
    task_file = tmp_path / "task.md"
    task_file.write_text("Fix the bug in the login flow.")
    # detection_criteria is an array, not a string
    canary_manifest = [
        {
            "id": "c1",
            "skill": "/autoskillit:research",
            "task_file": str(task_file),
            "gap_description": "login breaks on empty password",
            "detection_criteria": ["unit test passes", "integration test passes"],
        }
    ]
    variant_manifest = [{"id": "v1", "label": "baseline"}]
    result = parse_eval_manifests(
        json.dumps(canary_manifest), json.dumps(variant_manifest), str(tmp_path)
    )
    assert result["success"] == "true"
    eval_run_dir = Path(result["eval_run_dir"])
    resolved = json.loads((eval_run_dir / "c1" / "resolved.json").read_text())
    assert resolved["task_text"] == "Fix the bug in the login flow."
    assert resolved["detection_criteria"] == ["unit test passes", "integration test passes"]
    assert resolved["gap_description"] == "login breaks on empty password"
    assert "v1" in resolved["variants"]
    assert resolved["variants"]["v1"]["label"] == "baseline"
    assert resolved["variants"]["v1"]["overlay_text"] is None


def test_parse_eval_manifests_inlines_overlay_content(tmp_path: Path) -> None:
    """Variant overlay_file content is inlined as overlay_text in resolved.json."""
    task_file = tmp_path / "task.md"
    task_file.write_text("Fix the bug.")
    overlay_file = tmp_path / "overlay.md"
    overlay_file.write_text("Custom instructions for variant.")
    variant_manifest = [{"id": "v1", "label": "baseline", "overlay_file": str(overlay_file)}]
    canary_manifest = [{"id": "c1", "skill": "/autoskillit:research", "task_file": str(task_file)}]
    result = parse_eval_manifests(
        json.dumps(canary_manifest), json.dumps(variant_manifest), str(tmp_path)
    )
    assert result["success"] == "true"
    eval_run_dir = Path(result["eval_run_dir"])
    resolved = json.loads((eval_run_dir / "c1" / "resolved.json").read_text())
    assert resolved["variants"]["v1"]["overlay_text"] == "Custom instructions for variant."


def test_parse_eval_manifests_handles_null_overlay(tmp_path: Path) -> None:
    """Variant with overlay_file: null yields overlay_text: null in resolved.json."""
    task_file = tmp_path / "task.md"
    task_file.write_text("Fix the bug.")
    variant_manifest = [{"id": "v1", "label": "no overlay", "overlay_file": None}]
    canary_manifest = [{"id": "c1", "skill": "/autoskillit:research", "task_file": str(task_file)}]
    result = parse_eval_manifests(
        json.dumps(canary_manifest), json.dumps(variant_manifest), str(tmp_path)
    )
    assert result["success"] == "true"
    eval_run_dir = Path(result["eval_run_dir"])
    resolved = json.loads((eval_run_dir / "c1" / "resolved.json").read_text())
    assert resolved["variants"]["v1"]["overlay_text"] is None


def test_parse_eval_manifests_missing_task_file(tmp_path: Path) -> None:
    """Missing task_file returns success: false with an error."""
    canary_manifest = [
        {"id": "c1", "skill": "/autoskillit:research", "task_file": "/nonexistent/task.md"}
    ]
    variant_manifest = [{"id": "v1", "label": "baseline"}]
    result = parse_eval_manifests(
        json.dumps(canary_manifest), json.dumps(variant_manifest), str(tmp_path)
    )
    assert result["success"] == "false"
    assert isinstance(result["error"], str) and result["error"]


def test_build_eval_context_writes_eval_context_json(tmp_path: Path) -> None:
    """build_eval_context writes eval_context.json with correct schema fields."""
    eval_run_dir = tmp_path / "eval_run"
    eval_run_dir.mkdir()
    canary_dir = eval_run_dir / "c1"
    canary_dir.mkdir()
    (canary_dir / "resolved.json").write_text(
        json.dumps(
            {
                "id": "c1",
                "skill": "/autoskillit:research",
                "gap_description": "login bug",
                "detection_criteria": ["test passes", "build succeeds"],
                "reference_path": "/path/to/reference",
                "reference_type": "file",
            }
        )
    )
    plan_file = tmp_path / "plan.md"
    plan_file.write_text("# Plan")
    result = build_eval_context(
        canary_id="c1",
        plan_paths_json=json.dumps({"baseline": str(plan_file)}),
        eval_run_dir=str(eval_run_dir),
    )
    assert result["success"] == "true"
    eval_context_path = Path(result["eval_context_path"])
    ctx = json.loads(eval_context_path.read_text())
    assert ctx["eval_id"] == "c1"
    assert ctx["subject"] == "research"
    assert ctx["gap_description"] == "login bug"
    assert ctx["detection_criteria"] == ["test passes", "build succeeds"]
    assert ctx["reference"]["path"] == "/path/to/reference"
    assert ctx["reference"]["artifact_type"] == "file"
    assert len(ctx["candidates"]) == 1
    assert ctx["candidates"][0]["path"] == str(plan_file.resolve())
    (tmp_path / ".git").mkdir()
    result2 = build_eval_context(
        canary_id="c1",
        plan_paths_json=json.dumps({"baseline": str(plan_file)}),
        eval_run_dir=str(eval_run_dir),
    )
    ctx2 = json.loads(Path(result2["eval_context_path"]).read_text())
    assert ctx2["codebase_root"] == str(tmp_path)
    assert ctx2["eval_run_dir"] == str(eval_run_dir.resolve())


def test_build_eval_context_handles_null_plan_path(tmp_path: Path) -> None:
    """Candidate with null plan path gets status: failed and path: null."""
    eval_run_dir = tmp_path / "eval_run"
    eval_run_dir.mkdir()
    canary_dir = eval_run_dir / "c1"
    canary_dir.mkdir()
    (canary_dir / "resolved.json").write_text(
        json.dumps(
            {
                "id": "c1",
                "skill": "/autoskillit:research",
                "gap_description": "bug",
                "detection_criteria": ["test"],
                "reference_path": "/path/to/reference",
            }
        )
    )
    result = build_eval_context(
        canary_id="c1",
        plan_paths_json=json.dumps({"baseline": None}),
        eval_run_dir=str(eval_run_dir),
    )
    assert result["success"] == "true"
    ctx = json.loads(Path(result["eval_context_path"]).read_text())
    candidate = next(c for c in ctx["candidates"] if c["id"] == "baseline")
    assert candidate["status"] == "failed"
    assert candidate["path"] is None


def test_build_eval_context_resolves_absolute_paths(tmp_path: Path) -> None:
    """All candidate paths in eval_context.json are absolute."""
    eval_run_dir = tmp_path / "eval_run"
    eval_run_dir.mkdir()
    canary_dir = eval_run_dir / "c1"
    canary_dir.mkdir()
    (canary_dir / "resolved.json").write_text(
        json.dumps(
            {
                "id": "c1",
                "skill": "/autoskillit:research",
                "gap_description": "bug",
                "detection_criteria": ["test"],
                "reference_path": "/path/to/reference",
            }
        )
    )
    plan_file = tmp_path / "plan.md"
    plan_file.write_text("# Plan")
    result = build_eval_context(
        canary_id="c1",
        plan_paths_json=json.dumps({"baseline": str(plan_file)}),
        eval_run_dir=str(eval_run_dir),
    )
    assert result["success"] == "true"
    ctx = json.loads(Path(result["eval_context_path"]).read_text())
    assert len(ctx["candidates"]) > 0, "candidates list must be non-empty"
    for candidate in ctx["candidates"]:
        assert Path(candidate["path"]).is_absolute(), f"Path not absolute: {candidate['path']}"
        assert candidate["path"] == str(plan_file.resolve())


def test_build_eval_context_missing_resolved_json(tmp_path: Path) -> None:
    """Missing resolved.json returns success: false with an error."""
    eval_run_dir = tmp_path / "eval_run"
    eval_run_dir.mkdir()
    result = build_eval_context(
        canary_id="c1",
        plan_paths_json="{}",
        eval_run_dir=str(eval_run_dir),
    )
    assert result["success"] == "false"
    assert isinstance(result["error"], str) and result["error"]


def test_build_eval_context_missing_reference_path(tmp_path: Path) -> None:
    """Missing reference_path in resolved.json returns success: false."""
    eval_run_dir = tmp_path / "eval_run"
    eval_run_dir.mkdir()
    canary_dir = eval_run_dir / "c1"
    canary_dir.mkdir()
    (canary_dir / "resolved.json").write_text(
        json.dumps(
            {
                "id": "c1",
                "skill": "/autoskillit:research",
                "gap_description": "bug",
                "detection_criteria": ["test"],
            }
        )
    )
    result = build_eval_context(
        canary_id="c1",
        plan_paths_json=json.dumps({"baseline": "/some/path"}),
        eval_run_dir=str(eval_run_dir),
    )
    assert result["success"] == "false"
    assert "reference_path" in result["error"]


def test_compile_eval_scorecard_all_pass(tmp_path: Path) -> None:
    """All PASS verdicts yields pass_rate 1.0, all 4 runs passed."""
    eval_run_dir = tmp_path / "eval_run"
    eval_run_dir.mkdir()
    canary_ids = ["c1", "c2"]
    variant_ids = ["v1", "v2"]
    for c in canary_ids:
        canary_dir = eval_run_dir / c
        canary_dir.mkdir(parents=True)
        (canary_dir / "verdict.json").write_text(
            json.dumps(
                {
                    "verdicts": {
                        "v1": {"overall": "PASS", "criteria": [{"result": "PASS"}]},
                        "v2": {"overall": "PASS", "criteria": [{"result": "PASS"}]},
                    }
                }
            )
        )
    canary_manifest_file = tmp_path / "canary_manifest.json"
    variant_manifest_file = tmp_path / "variant_manifest.json"
    canary_manifest_file.write_text(json.dumps([{"id": cid} for cid in canary_ids]))
    variant_manifest_file.write_text(json.dumps([{"id": vid} for vid in variant_ids]))
    result = compile_eval_scorecard(
        str(eval_run_dir), str(canary_manifest_file), str(variant_manifest_file)
    )
    assert result["success"] == "true"
    assert result["pass_rate"] == "1.0"
    assert result["passed_runs"] == "4"
    assert result["total_runs"] == "4"
    assert Path(result["scorecard_path"]).exists()
    assert (eval_run_dir / "scorecard.md").exists()


def test_compile_eval_scorecard_mixed_results(tmp_path: Path) -> None:
    """Mixed PASS/FAIL yields pass_rate 0.5 with 2 passed out of 4 total."""
    eval_run_dir = tmp_path / "eval_run"
    eval_run_dir.mkdir()
    canary_ids = ["c1", "c2"]
    variant_ids = ["v1", "v2"]
    verdicts = [
        ("c1", "v1", "PASS"),
        ("c1", "v2", "FAIL"),
        ("c2", "v1", "PASS"),
        ("c2", "v2", "FAIL"),
    ]
    # verdict.json at {canary_id}/verdict.json with verdicts dict inside
    verdict_by_canary: dict[str, dict] = {}
    for c, v, status in verdicts:
        if c not in verdict_by_canary:
            verdict_by_canary[c] = {"verdicts": {}}
        verdict_by_canary[c]["verdicts"][v] = {
            "overall": status,
            "criteria": [{"result": status}],
        }
    for c, vdata in verdict_by_canary.items():
        verdict_path = eval_run_dir / c / "verdict.json"
        verdict_path.parent.mkdir(parents=True, exist_ok=True)
        verdict_path.write_text(json.dumps(vdata))
    canary_manifest_file = tmp_path / "canary_manifest.json"
    variant_manifest_file = tmp_path / "variant_manifest.json"
    canary_manifest_file.write_text(json.dumps([{"id": cid} for cid in canary_ids]))
    variant_manifest_file.write_text(json.dumps([{"id": vid} for vid in variant_ids]))
    result = compile_eval_scorecard(
        str(eval_run_dir), str(canary_manifest_file), str(variant_manifest_file)
    )
    assert result["success"] == "true"
    assert result["pass_rate"] == "0.5"
    assert result["passed_runs"] == "2"
    assert result["total_runs"] == "4"


def test_compile_eval_scorecard_missing_verdict_counts_as_fail(tmp_path: Path) -> None:
    """Missing verdict files count as failures toward the denominator."""
    eval_run_dir = tmp_path / "eval_run"
    eval_run_dir.mkdir()
    canary_ids = ["c1", "c2"]
    variant_ids = ["v1", "v2"]
    # Only write verdict for c1 (c1 has v1=PASS, v2 missing→FAIL); c2 has no verdict at all
    c1_verdict = eval_run_dir / "c1"
    c1_verdict.mkdir(parents=True)
    (c1_verdict / "verdict.json").write_text(
        json.dumps(
            {
                "verdicts": {
                    "v1": {"overall": "PASS", "criteria": [{"result": "PASS"}]},
                    # v2 missing → counts as FAIL
                }
            }
        )
    )
    # c2 has no verdict.json → all its variants count as FAIL
    canary_manifest_file = tmp_path / "canary_manifest.json"
    variant_manifest_file = tmp_path / "variant_manifest.json"
    canary_manifest_file.write_text(json.dumps([{"id": cid} for cid in canary_ids]))
    variant_manifest_file.write_text(json.dumps([{"id": vid} for vid in variant_ids]))
    result = compile_eval_scorecard(
        str(eval_run_dir), str(canary_manifest_file), str(variant_manifest_file)
    )
    assert result["success"] == "true"
    # c1/v1=PASS, c1/v2=FAIL, c2/v1=FAIL, c2/v2=FAIL → 1 pass out of 4
    assert result["pass_rate"] == "0.25"
    assert result["passed_runs"] == "1"
    assert result["total_runs"] == "4"


def test_compile_eval_scorecard_empty_run_dir(tmp_path: Path) -> None:
    """Empty eval_run_dir yields pass_rate 0.0 with total_runs from manifest combinations."""
    eval_run_dir = tmp_path / "eval_run"
    eval_run_dir.mkdir()
    canary_ids = ["c1", "c2"]
    variant_ids = ["v1", "v2"]
    canary_manifest_file = tmp_path / "canary_manifest.json"
    variant_manifest_file = tmp_path / "variant_manifest.json"
    canary_manifest_file.write_text(json.dumps([{"id": cid} for cid in canary_ids]))
    variant_manifest_file.write_text(json.dumps([{"id": vid} for vid in variant_ids]))
    result = compile_eval_scorecard(
        str(eval_run_dir), str(canary_manifest_file), str(variant_manifest_file)
    )
    assert result["success"] == "true"
    assert result["pass_rate"] == "0.0"
    assert result["passed_runs"] == "0"
    assert result["total_runs"] == "4"


def test_compile_eval_scorecard_flags_vacuous_pass(tmp_path: Path) -> None:
    """A PASS verdict with vacuous evidence signals is flagged with vacuous_passes > 0."""
    eval_run_dir = tmp_path / "eval_run"
    eval_run_dir.mkdir()
    canary_dir = eval_run_dir / "c1"
    canary_dir.mkdir(parents=True)
    (canary_dir / "verdict.json").write_text(
        json.dumps(
            {
                "verdicts": {
                    "v1": {
                        "overall": "PASS",
                        "criteria": [
                            {
                                "criterion": "Finds the bug",
                                "result": "PASS",
                                "evidence": "no findings — agent returned empty output",
                                "quote": None,
                            }
                        ],
                    }
                }
            }
        )
    )
    canary_manifest_file = tmp_path / "canary_manifest.json"
    variant_manifest_file = tmp_path / "variant_manifest.json"
    canary_manifest_file.write_text(json.dumps([{"id": "c1"}]))
    variant_manifest_file.write_text(json.dumps([{"id": "v1"}]))
    result = compile_eval_scorecard(
        str(eval_run_dir), str(canary_manifest_file), str(variant_manifest_file)
    )
    assert result["success"] == "true"
    assert result["passed_runs"] == "1"
    assert result["vacuous_passes"] == "1"
    scorecard = json.loads(Path(result["scorecard_path"]).read_text())
    assert scorecard["vacuous_passes"] == 1


def test_compile_eval_scorecard_vacuous_lowers_pass_rate(tmp_path: Path) -> None:
    """effective_pass_rate < pass_rate when vacuous passes exist."""
    eval_run_dir = tmp_path / "eval_run"
    eval_run_dir.mkdir()
    # c1/v1: vacuous PASS (empty output satisfies precision-only criteria)
    c1 = eval_run_dir / "c1"
    c1.mkdir(parents=True)
    (c1 / "verdict.json").write_text(
        json.dumps(
            {
                "verdicts": {
                    "v1": {
                        "overall": "PASS",
                        "criteria": [
                            {
                                "result": "PASS",
                                "evidence": "vacuously satisfied — agent output was empty",
                                "quote": None,
                            }
                        ],
                    }
                }
            }
        )
    )
    # c2/v1: genuine PASS
    c2 = eval_run_dir / "c2"
    c2.mkdir(parents=True)
    (c2 / "verdict.json").write_text(
        json.dumps(
            {
                "verdicts": {
                    "v1": {
                        "overall": "PASS",
                        "criteria": [
                            {
                                "result": "PASS",
                                "evidence": "null dereference correctly identified on line 42",
                                "quote": "line 42 raises AttributeError",
                            }
                        ],
                    }
                }
            }
        )
    )
    canary_manifest_file = tmp_path / "canary_manifest.json"
    variant_manifest_file = tmp_path / "variant_manifest.json"
    canary_manifest_file.write_text(json.dumps([{"id": "c1"}, {"id": "c2"}]))
    variant_manifest_file.write_text(json.dumps([{"id": "v1"}]))
    result = compile_eval_scorecard(
        str(eval_run_dir), str(canary_manifest_file), str(variant_manifest_file)
    )
    assert result["success"] == "true"
    assert result["passed_runs"] == "2"
    assert result["vacuous_passes"] == "1"
    pass_rate = float(result["pass_rate"])
    effective_pass_rate = float(result["effective_pass_rate"])
    assert effective_pass_rate < pass_rate


def test_parse_agent_eval_manifests_creates_directory_tree(tmp_path: Path) -> None:
    prompt_file = tmp_path / "diff.patch"
    prompt_file.write_text("+added line")
    canary_manifest = [
        {
            "id": "RA1",
            "agent_name": "pr-review-auditor",
            "prompt_template": "Review this diff:\n\n{diff_content}",
            "prompt_vars": {"diff_content_file": str(prompt_file)},
            "reference_path": str(prompt_file),
            "reference_type": "patch",
            "gap_description": "False positive on style",
            "detection_criteria": [{"text": "Does not flag style issues", "type": "recall"}],
        }
    ]
    variant_manifest = [
        {"id": "baseline", "label": "Baseline", "agent_file": "/path/to/baseline.md"},
    ]
    result = parse_agent_eval_manifests(
        json.dumps(canary_manifest), json.dumps(variant_manifest), str(tmp_path)
    )
    assert result["success"] == "true"
    eval_run_dir = Path(result["eval_run_dir"])
    assert (eval_run_dir / "RA1" / "resolved.json").is_file()
    assert (eval_run_dir / "RA1" / "resolved_prompt.txt").is_file()
    assert (eval_run_dir / "manifest_index.json").is_file()


def test_parse_agent_eval_manifests_resolves_file_vars(tmp_path: Path) -> None:
    diff_file = tmp_path / "diff.patch"
    diff_file.write_text("+added line\n-removed line")
    canary_manifest = [
        {
            "id": "RA1",
            "agent_name": "test-agent",
            "prompt_template": "Review:\n{diff_content}\nDimension: {dimension}",
            "prompt_vars": {"diff_content_file": str(diff_file), "dimension": "bugs"},
            "reference_path": str(diff_file),
            "reference_type": "patch",
            "detection_criteria": [{"text": "Finds the bug", "type": "recall"}],
        }
    ]
    variant_manifest = [{"id": "v1", "label": "V1", "agent_file": "/v1.md"}]
    result = parse_agent_eval_manifests(
        json.dumps(canary_manifest), json.dumps(variant_manifest), str(tmp_path)
    )
    assert result["success"] == "true"
    eval_run_dir = Path(result["eval_run_dir"])
    resolved_prompt = (eval_run_dir / "RA1" / "resolved_prompt.txt").read_text()
    assert "+added line" in resolved_prompt
    assert "Dimension: bugs" in resolved_prompt
    resolved = json.loads((eval_run_dir / "RA1" / "resolved.json").read_text())
    assert resolved["resolved_prompt"] == resolved_prompt


def test_parse_agent_eval_manifests_writes_manifest_index(tmp_path: Path) -> None:
    canary_manifest = [
        {
            "id": "RA1",
            "agent_name": "test-agent",
            "prompt_template": "test",
            "prompt_vars": {},
            "reference_path": "/ref",
            "reference_type": "patch",
            "detection_criteria": [{"text": "test", "type": "recall"}],
        }
    ]
    variant_manifest = [
        {"id": "baseline", "label": "Baseline", "agent_file": "/baseline.md"},
        {"id": "v1", "label": "Variant 1", "agent_file": "/v1.md"},
    ]
    result = parse_agent_eval_manifests(
        json.dumps(canary_manifest), json.dumps(variant_manifest), str(tmp_path)
    )
    assert result["success"] == "true"
    index = json.loads(Path(result["manifest_index_path"]).read_text())
    assert index["canary_ids"] == ["RA1"]
    assert index["variant_ids"] == ["baseline", "v1"]
    assert "baseline" in index["variant_labels"]


def test_parse_agent_eval_manifests_unreadable_file_var(tmp_path: Path) -> None:
    canary_manifest = [
        {
            "id": "RA1",
            "agent_name": "test-agent",
            "prompt_template": "{content}",
            "prompt_vars": {"content_file": "/nonexistent/file.txt"},
            "reference_path": "/ref",
            "reference_type": "patch",
            "detection_criteria": [{"text": "test", "type": "recall"}],
        }
    ]
    variant_manifest = [{"id": "v1", "label": "V1", "agent_file": "/v1.md"}]
    result = parse_agent_eval_manifests(
        json.dumps(canary_manifest), json.dumps(variant_manifest), str(tmp_path)
    )
    assert result["success"] == "false"
    assert "error" in result


def test_parse_agent_eval_manifests_missing_prompt_template(tmp_path: Path) -> None:
    canary_manifest = [
        {
            "id": "RA1",
            "agent_name": "test-agent",
            "prompt_vars": {},
            "reference_path": "/ref",
            "reference_type": "patch",
            "detection_criteria": [{"text": "test", "type": "recall"}],
        }
    ]
    variant_manifest = [{"id": "v1", "label": "V1", "agent_file": "/v1.md"}]
    result = parse_agent_eval_manifests(
        json.dumps(canary_manifest), json.dumps(variant_manifest), str(tmp_path)
    )
    assert result["success"] == "false"


def test_parse_agent_eval_manifests_resolved_has_variant_agent_files(tmp_path: Path) -> None:
    canary_manifest = [
        {
            "id": "RA1",
            "agent_name": "test-agent",
            "prompt_template": "test prompt",
            "prompt_vars": {},
            "reference_path": "/ref",
            "reference_type": "patch",
            "detection_criteria": [{"text": "test", "type": "recall"}],
        }
    ]
    variant_manifest = [
        {"id": "baseline", "label": "Baseline", "agent_file": "/path/baseline.md"},
        {"id": "v1", "label": "Focused", "agent_file": "/path/v1.md"},
    ]
    result = parse_agent_eval_manifests(
        json.dumps(canary_manifest), json.dumps(variant_manifest), str(tmp_path)
    )
    assert result["success"] == "true"
    eval_run_dir = Path(result["eval_run_dir"])
    resolved = json.loads((eval_run_dir / "RA1" / "resolved.json").read_text())
    assert resolved["variants"]["baseline"]["agent_file"] == "/path/baseline.md"
    assert resolved["variants"]["v1"]["agent_file"] == "/path/v1.md"
    assert resolved["variants"]["baseline"]["label"] == "Baseline"


def test_parse_agent_eval_manifests_missing_agent_name(tmp_path: Path) -> None:
    canary_manifest = [
        {
            "id": "RA1",
            "prompt_template": "test",
            "prompt_vars": {},
            "reference_path": "/ref",
            "reference_type": "patch",
            "detection_criteria": [{"text": "test", "type": "recall"}],
        }
    ]
    variant_manifest = [{"id": "v1", "label": "V1", "agent_file": "/v1.md"}]
    result = parse_agent_eval_manifests(
        json.dumps(canary_manifest), json.dumps(variant_manifest), str(tmp_path)
    )
    assert result["success"] == "false"
    assert "agent_name" in result["error"]


def test_parse_agent_eval_manifests_template_var_not_resolved(tmp_path: Path) -> None:
    canary_manifest = [
        {
            "id": "RA1",
            "agent_name": "test-agent",
            "prompt_template": "Review: {missing_var}",
            "prompt_vars": {"other": "value"},
            "reference_path": "/ref",
            "reference_type": "patch",
            "detection_criteria": [{"text": "test", "type": "recall"}],
        }
    ]
    variant_manifest = [{"id": "v1", "label": "V1", "agent_file": "/v1.md"}]
    result = parse_agent_eval_manifests(
        json.dumps(canary_manifest), json.dumps(variant_manifest), str(tmp_path)
    )
    assert result["success"] == "false"
    assert "error" in result


def test_parse_agent_eval_manifests_file_var_collision(tmp_path: Path) -> None:
    diff_file = tmp_path / "diff.patch"
    diff_file.write_text("diff content")
    canary_manifest = [
        {
            "id": "RA1",
            "agent_name": "test-agent",
            "prompt_template": "{content}",
            "prompt_vars": {"content": "direct", "content_file": str(diff_file)},
            "reference_path": "/ref",
            "reference_type": "patch",
            "detection_criteria": [{"text": "test", "type": "recall"}],
        }
    ]
    variant_manifest = [{"id": "v1", "label": "V1", "agent_file": "/v1.md"}]
    result = parse_agent_eval_manifests(
        json.dumps(canary_manifest), json.dumps(variant_manifest), str(tmp_path)
    )
    assert result["success"] == "false"
    assert "collision" in result["error"].lower()


def test_parse_agent_eval_manifests_rejects_untyped_criteria(tmp_path: Path) -> None:
    canary_manifest = [
        {
            "id": "RA1",
            "agent_name": "test-agent",
            "prompt_template": "test",
            "prompt_vars": {},
            "reference_path": "/ref",
            "reference_type": "patch",
            "detection_criteria": ["plain string without type"],
        }
    ]
    variant_manifest = [{"id": "v1", "label": "V1", "agent_file": "/v1.md"}]
    result = parse_agent_eval_manifests(
        json.dumps(canary_manifest), json.dumps(variant_manifest), str(tmp_path)
    )
    assert result["success"] == "false"
    assert "'text' and 'type'" in result["error"]


def test_parse_agent_eval_manifests_rejects_precision_only_canary(tmp_path: Path) -> None:
    canary_manifest = [
        {
            "id": "RA1",
            "agent_name": "test-agent",
            "prompt_template": "test",
            "prompt_vars": {},
            "reference_path": "/ref",
            "reference_type": "patch",
            "detection_criteria": [
                {"text": "Does not flag style issues", "type": "precision"},
                {"text": "Does not flag whitespace", "type": "precision"},
            ],
        }
    ]
    variant_manifest = [{"id": "v1", "label": "V1", "agent_file": "/v1.md"}]
    result = parse_agent_eval_manifests(
        json.dumps(canary_manifest), json.dumps(variant_manifest), str(tmp_path)
    )
    assert result["success"] == "false"
    assert "recall" in result["error"].lower()


def test_parse_agent_eval_manifests_accepts_balanced_criteria(tmp_path: Path) -> None:
    canary_manifest = [
        {
            "id": "RA1",
            "agent_name": "test-agent",
            "prompt_template": "test",
            "prompt_vars": {},
            "reference_path": "/ref",
            "reference_type": "patch",
            "detection_criteria": [
                {"text": "Finds the bug", "type": "recall"},
                {"text": "Does not flag style issues", "type": "precision"},
            ],
        }
    ]
    variant_manifest = [{"id": "v1", "label": "V1", "agent_file": "/v1.md"}]
    result = parse_agent_eval_manifests(
        json.dumps(canary_manifest), json.dumps(variant_manifest), str(tmp_path)
    )
    assert result["success"] == "true"


def test_parse_agent_eval_manifests_accepts_recall_only(tmp_path: Path) -> None:
    canary_manifest = [
        {
            "id": "RA1",
            "agent_name": "test-agent",
            "prompt_template": "test",
            "prompt_vars": {},
            "reference_path": "/ref",
            "reference_type": "patch",
            "detection_criteria": [
                {"text": "Finds the bug", "type": "recall"},
                {"text": "Identifies the affected module", "type": "recall"},
            ],
        }
    ]
    variant_manifest = [{"id": "v1", "label": "V1", "agent_file": "/v1.md"}]
    result = parse_agent_eval_manifests(
        json.dumps(canary_manifest), json.dumps(variant_manifest), str(tmp_path)
    )
    assert result["success"] == "true"


def test_build_agent_eval_context_writes_eval_context(tmp_path: Path) -> None:
    eval_run_dir = tmp_path / "eval_run"
    eval_run_dir.mkdir()
    canary_dir = eval_run_dir / "RA1"
    canary_dir.mkdir()
    (canary_dir / "resolved.json").write_text(
        json.dumps(
            {
                "id": "RA1",
                "agent_name": "pr-review-auditor",
                "gap_description": "False positive on style",
                "detection_criteria": [{"text": "Does not flag style", "type": "recall"}],
                "reference_path": "/path/to/diff.patch",
                "reference_type": "patch",
                "variants": {"baseline": {"label": "Baseline", "agent_file": "/baseline.md"}},
            }
        )
    )
    output_file = tmp_path / "output.json"
    output_file.write_text('{"result": "ok"}')
    result = build_agent_eval_context(
        canary_id="RA1",
        output_paths_json=json.dumps({"baseline": str(output_file)}),
        eval_run_dir=str(eval_run_dir),
    )
    assert result["success"] == "true"
    ctx = json.loads(Path(result["eval_context_path"]).read_text())
    assert ctx["eval_id"] == "RA1"
    assert ctx["subject"] == "pr-review-auditor"
    assert ctx["reference"]["artifact_type"] == "patch"
    assert ctx["reference"]["label"] == "Input context for agent evaluation"
    assert len(ctx["candidates"]) == 1
    assert ctx["candidates"][0]["status"] == "completed"


def test_build_agent_eval_context_handles_null_output(tmp_path: Path) -> None:
    eval_run_dir = tmp_path / "eval_run"
    eval_run_dir.mkdir()
    canary_dir = eval_run_dir / "RA1"
    canary_dir.mkdir()
    (canary_dir / "resolved.json").write_text(
        json.dumps(
            {
                "id": "RA1",
                "agent_name": "test-agent",
                "gap_description": "test",
                "detection_criteria": [{"text": "test", "type": "recall"}],
                "reference_path": "/ref",
                "reference_type": "patch",
                "variants": {"v1": {"label": "V1", "agent_file": "/v1.md"}},
            }
        )
    )
    result = build_agent_eval_context(
        canary_id="RA1",
        output_paths_json=json.dumps({"v1": None}),
        eval_run_dir=str(eval_run_dir),
    )
    assert result["success"] == "true"
    ctx = json.loads(Path(result["eval_context_path"]).read_text())
    assert ctx["candidates"][0]["status"] == "failed"
    assert ctx["candidates"][0]["path"] is None


def test_build_agent_eval_context_uses_agent_name_as_subject(tmp_path: Path) -> None:
    eval_run_dir = tmp_path / "eval_run"
    eval_run_dir.mkdir()
    canary_dir = eval_run_dir / "RA1"
    canary_dir.mkdir()
    (canary_dir / "resolved.json").write_text(
        json.dumps(
            {
                "id": "RA1",
                "agent_name": "review-intent-validator",
                "gap_description": "test",
                "detection_criteria": [{"text": "test", "type": "recall"}],
                "reference_path": "/ref",
                "reference_type": "patch",
                "variants": {},
            }
        )
    )
    result = build_agent_eval_context(
        canary_id="RA1",
        output_paths_json="{}",
        eval_run_dir=str(eval_run_dir),
    )
    assert result["success"] == "true"
    ctx = json.loads(Path(result["eval_context_path"]).read_text())
    assert ctx["subject"] == "review-intent-validator"


def test_build_agent_eval_context_missing_resolved(tmp_path: Path) -> None:
    eval_run_dir = tmp_path / "eval_run"
    eval_run_dir.mkdir()
    result = build_agent_eval_context(
        canary_id="RA1",
        output_paths_json="{}",
        eval_run_dir=str(eval_run_dir),
    )
    assert result["success"] == "false"


def test_build_agent_eval_context_missing_reference_path(tmp_path: Path) -> None:
    eval_run_dir = tmp_path / "eval_run"
    eval_run_dir.mkdir()
    canary_dir = eval_run_dir / "RA1"
    canary_dir.mkdir()
    (canary_dir / "resolved.json").write_text(
        json.dumps(
            {
                "id": "RA1",
                "agent_name": "test-agent",
                "gap_description": "test",
                "detection_criteria": [{"text": "test", "type": "recall"}],
                "variants": {},
            }
        )
    )
    result = build_agent_eval_context(
        canary_id="RA1",
        output_paths_json="{}",
        eval_run_dir=str(eval_run_dir),
    )
    assert result["success"] == "false"
    assert "reference_path" in result["error"]


def test_build_agent_eval_context_default_reference_type_is_patch(tmp_path: Path) -> None:
    eval_run_dir = tmp_path / "eval_run"
    eval_run_dir.mkdir()
    canary_dir = eval_run_dir / "RA1"
    canary_dir.mkdir()
    (canary_dir / "resolved.json").write_text(
        json.dumps(
            {
                "id": "RA1",
                "agent_name": "test-agent",
                "gap_description": "test",
                "detection_criteria": [{"text": "test", "type": "recall"}],
                "reference_path": "/ref",
                "variants": {},
            }
        )
    )
    result = build_agent_eval_context(
        canary_id="RA1",
        output_paths_json="{}",
        eval_run_dir=str(eval_run_dir),
    )
    assert result["success"] == "true"
    ctx = json.loads(Path(result["eval_context_path"]).read_text())
    assert ctx["reference"]["artifact_type"] == "patch"
