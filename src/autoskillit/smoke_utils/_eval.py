"""Eval manifest parsing and context building for smoke_utils sub-modules."""

from __future__ import annotations

import json
from pathlib import Path

import regex as re

from autoskillit.smoke_utils._helpers import _load_json, try_load_json

VALID_CRITERION_TYPES: frozenset[str] = frozenset({"precision", "recall", "recognition"})
REQUIRED_CRITERION_KEYS: frozenset[str] = frozenset({"text", "type"})


def parse_eval_manifests(
    canary_manifest: str,
    variant_manifest: str,
    output_dir: str,
) -> dict[str, str]:
    """Read canary and variant manifest files, create eval run directory tree.

    Creates a timestamped eval_run_dir under output_dir/runs/, writes per-canary
    resolved.json files with inlined task_text and overlay_text, and writes
    manifest_index.json with canary_ids, variant_ids, and directory paths.
    """
    from datetime import datetime

    from autoskillit.core import atomic_write  # noqa: PLC0415

    if not Path(output_dir).is_absolute():
        raise ValueError(f"output_dir must be absolute, got {output_dir!r}")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    eval_run_dir = Path(output_dir) / "runs" / timestamp
    eval_run_dir.mkdir(parents=True, exist_ok=True)

    try:
        canaries = _load_json(canary_manifest)
        variants = _load_json(variant_manifest)
    except (OSError, json.JSONDecodeError) as exc:
        return {"success": "false", "error": f"Failed to read manifest: {exc}"}

    try:
        canary_ids = [c["id"] for c in canaries]
        variant_ids = [v["id"] for v in variants]
    except (KeyError, TypeError) as exc:
        return {"success": "false", "error": f"Invalid manifest schema: {exc}"}

    for canary in canaries:
        canary_dir = eval_run_dir / canary["id"]
        canary_dir.mkdir(parents=True, exist_ok=True)

        resolved = dict(canary)
        resolved["variants"] = {}
        resolved["task_text"] = ""

        task_file = canary.get("task_file")
        if not task_file:
            return {
                "success": "false",
                "error": f"Canary {canary.get('id', '?')} missing task_file",
            }
        try:
            resolved["task_text"] = Path(task_file).read_text()
        except OSError as exc:
            return {"success": "false", "error": f"Failed to read task_file: {exc}"}

        for variant in variants:
            variant_dir = canary_dir / variant["id"]
            variant_dir.mkdir(parents=True, exist_ok=True)

            overlay_text: str | None = None
            if variant.get("overlay_file"):
                try:
                    overlay_text = Path(variant["overlay_file"]).read_text()
                except OSError as exc:
                    return {"success": "false", "error": f"Failed to read overlay_file: {exc}"}

            resolved["variants"][variant["id"]] = {
                "label": variant.get("label", variant["id"]),
                "overlay_text": overlay_text,
            }

        atomic_write(canary_dir / "resolved.json", json.dumps(resolved, indent=2))

    manifest_index = {
        "canary_ids": canary_ids,
        "variant_ids": variant_ids,
        "variant_labels": {v["id"]: v.get("label", v["id"]) for v in variants},
    }
    for canary_id in canary_ids:
        manifest_index[f"path_{canary_id}"] = str(eval_run_dir / canary_id)

    manifest_index_path = eval_run_dir / "manifest_index.json"
    atomic_write(manifest_index_path, json.dumps(manifest_index, indent=2))

    return {
        "success": "true",
        "eval_run_dir": str(eval_run_dir),
        "canary_count": str(len(canary_ids)),
        "variant_count": str(len(variant_ids)),
        "manifest_index_path": str(manifest_index_path),
    }


def parse_agent_eval_manifests(
    canary_manifest: str,
    variant_manifest: str,
    output_dir: str,
) -> dict[str, str]:
    """Parse agent-eval manifests, resolve prompt vars, and create eval run directory structure."""
    from datetime import datetime

    from autoskillit.core import atomic_write  # noqa: PLC0415

    if not Path(output_dir).is_absolute():
        raise ValueError(f"output_dir must be absolute, got {output_dir!r}")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    eval_run_dir = Path(output_dir) / "runs" / timestamp
    eval_run_dir.mkdir(parents=True, exist_ok=True)

    try:
        canaries = _load_json(canary_manifest)
        variants = _load_json(variant_manifest)
    except (OSError, json.JSONDecodeError) as exc:
        return {"success": "false", "error": f"Failed to read manifest: {exc}"}

    canary_ids = []
    variant_ids = []

    for c in canaries:
        cid = c.get("id")
        if not cid:
            return {"success": "false", "error": "Canary missing 'id' field"}
        canary_ids.append(cid)

    for v in variants:
        vid = v.get("id")
        if not vid:
            return {"success": "false", "error": "Variant missing 'id' field"}
        variant_ids.append(vid)

    variant_labels = {v["id"]: v.get("label", v["id"]) for v in variants}

    for canary in canaries:
        canary_id = canary["id"]

        if "agent_name" not in canary:
            return {"success": "false", "error": f"Canary {canary_id} missing agent_name"}

        if "prompt_template" not in canary:
            return {"success": "false", "error": f"Canary {canary_id} missing prompt_template"}

        criteria = canary.get("detection_criteria", [])
        if not criteria:
            return {"success": "false", "error": f"Canary {canary_id} has no detection_criteria"}

        for i, c in enumerate(criteria):
            if not isinstance(c, dict) or not REQUIRED_CRITERION_KEYS <= c.keys():
                return {
                    "success": "false",
                    "error": (
                        f"Canary {canary_id} criterion {i} must have 'text' and 'type' fields"
                    ),
                }
            if c["type"] not in VALID_CRITERION_TYPES:
                return {
                    "success": "false",
                    "error": (f"Canary {canary_id} criterion {i} has invalid type '{c['type']}'"),
                }

        types_present = {c["type"] for c in criteria}
        if "recall" not in types_present:
            return {
                "success": "false",
                "error": (
                    f"Canary {canary_id} has no recall criterion — "
                    "add at least one type: 'recall' criterion"
                ),
            }

        canary_dir = eval_run_dir / canary_id
        canary_dir.mkdir(parents=True, exist_ok=True)

        resolved_vars = {}
        for key, value in canary.get("prompt_vars", {}).items():
            if key.endswith("_file"):
                bare_key = key[:-5]
                if bare_key in resolved_vars:
                    return {
                        "success": "false",
                        "error": (
                            f"prompt_vars collision: '{bare_key}' defined both "
                            "directly and via _file indirection"
                        ),
                    }
                try:
                    resolved_vars[bare_key] = Path(value).read_text()
                except OSError as exc:
                    return {"success": "false", "error": f"Failed to read {key} file: {exc}"}
            else:
                if key in resolved_vars:
                    return {
                        "success": "false",
                        "error": (
                            f"prompt_vars collision: '{key}' defined both "
                            "directly and via _file indirection"
                        ),
                    }
                resolved_vars[key] = value

        try:
            resolved_prompt = str(canary["prompt_template"]).format_map(resolved_vars)
        except KeyError as exc:
            return {"success": "false", "error": f"Template variable not resolved: {exc}"}

        atomic_write(canary_dir / "resolved_prompt.txt", resolved_prompt)

        variants_dict = {}
        for v in variants:
            vid = v["id"]
            variants_dict[vid] = {
                "label": v.get("label", vid),
                "agent_file": v.get("agent_file"),
            }
            (canary_dir / vid).mkdir(parents=True, exist_ok=True)

        resolved = dict(canary)
        resolved["resolved_prompt"] = resolved_prompt
        resolved["variants"] = variants_dict

        atomic_write(canary_dir / "resolved.json", json.dumps(resolved, indent=2))

    manifest_index: dict[str, object] = {
        "canary_ids": canary_ids,
        "variant_ids": variant_ids,
        "variant_labels": variant_labels,
    }
    for cid in canary_ids:
        manifest_index[f"path_{cid}"] = str(eval_run_dir / cid)

    manifest_index_path = eval_run_dir / "manifest_index.json"
    atomic_write(manifest_index_path, json.dumps(manifest_index, indent=2))

    return {
        "success": "true",
        "eval_run_dir": str(eval_run_dir),
        "canary_count": str(len(canary_ids)),
        "variant_count": str(len(variant_ids)),
        "manifest_index_path": str(manifest_index_path),
    }


def _build_eval_context_common(
    canary_id: str,
    paths: dict,
    eval_run_dir: str,
    resolved: dict,
    *,
    subject: str,
    reference_label: str,
    default_artifact_type: str,
) -> dict[str, str]:
    """Shared implementation for build_eval_context and build_agent_eval_context."""
    from autoskillit.core import atomic_write  # noqa: PLC0415

    reference_path_raw = resolved.get("reference_path")
    if not reference_path_raw:
        return {
            "success": "false",
            "error": f"Canary {canary_id} resolved.json missing reference_path",
        }
    reference_path = Path(reference_path_raw).resolve()

    candidates = []
    for variant_id, path in paths.items():
        variant_meta = resolved.get("variants", {}).get(variant_id, {})
        label = variant_meta.get("label", variant_id)
        if path is not None:
            candidates.append(
                {
                    "id": variant_id,
                    "path": str(Path(path).resolve()),
                    "label": label,
                    "status": "completed",
                }
            )
        else:
            candidates.append(
                {
                    "id": variant_id,
                    "path": None,
                    "label": label,
                    "status": "failed",
                }
            )

    codebase_root = ""
    eval_run_path = Path(eval_run_dir)
    for parent in eval_run_path.parents:
        if (parent / ".git").exists():
            codebase_root = str(parent)
            break

    eval_context = {
        "eval_id": resolved.get("id", canary_id),
        "subject": subject,
        "gap_description": resolved.get("gap_description", ""),
        "detection_criteria": resolved.get("detection_criteria", []),
        "reference": {
            "path": str(reference_path),
            "label": reference_label,
            "artifact_type": resolved.get("reference_type", default_artifact_type),
        },
        "candidates": candidates,
        "codebase_root": codebase_root,
        "eval_run_dir": str(eval_run_path.resolve()),
    }

    out_path = eval_run_path / canary_id / "eval_context.json"
    atomic_write(out_path, json.dumps(eval_context, indent=2))
    return {"success": "true", "eval_context_path": str(out_path)}


def build_eval_context(
    canary_id: str,
    plan_paths_json: str,
    eval_run_dir: str,
) -> dict[str, str]:
    """Assemble eval_context.json from resolved manifest and plan paths."""
    resolved_path = Path(eval_run_dir) / canary_id / "resolved.json"
    try:
        resolved = json.loads(resolved_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return {"success": "false", "error": f"Failed to read resolved.json: {exc}"}

    try:
        paths = json.loads(plan_paths_json)
    except json.JSONDecodeError as exc:
        return {"success": "false", "error": f"Failed to parse plan_paths_json: {exc}"}

    skill_name = resolved.get("skill", "")
    if skill_name.startswith("/"):
        skill_name = skill_name.lstrip("/")
    if skill_name.startswith("autoskillit:"):
        skill_name = skill_name[len("autoskillit:") :]

    return _build_eval_context_common(
        canary_id,
        paths,
        eval_run_dir,
        resolved,
        subject=skill_name,
        reference_label="Original plan (introduced the bug)",
        default_artifact_type="plan",
    )


def build_agent_eval_context(
    canary_id: str,
    output_paths_json: str,
    eval_run_dir: str,
) -> dict[str, str]:
    """Assemble eval_context.json from resolved manifest and variant output paths."""
    resolved_path = Path(eval_run_dir) / canary_id / "resolved.json"
    try:
        resolved = json.loads(resolved_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return {"success": "false", "error": f"Failed to read resolved.json: {exc}"}

    try:
        paths = json.loads(output_paths_json)
    except json.JSONDecodeError as exc:
        return {"success": "false", "error": f"Failed to parse output_paths_json: {exc}"}

    subject = resolved.get("agent_name", "")

    return _build_eval_context_common(
        canary_id,
        paths,
        eval_run_dir,
        resolved,
        subject=subject,
        reference_label="Input context for agent evaluation",
        default_artifact_type="patch",
    )


_VACUOUS_SIGNAL_RE = re.compile(
    r"\b(?:empty|no\s+findings|vacuously|no\s+output|trivially\s+satisfied)\b",
    re.IGNORECASE,
)


def _is_vacuous_pass(verdict_entry: dict) -> bool:
    if verdict_entry.get("overall") != "PASS":
        return False
    for c in verdict_entry.get("criteria", []):
        if c.get("result") == "PASS":
            evidence = c.get("evidence") or ""
            if _VACUOUS_SIGNAL_RE.search(evidence):
                return True
    return False


def compile_eval_scorecard(
    eval_run_dir: str,
    canary_manifest: str,
    variant_manifest: str,
) -> dict[str, str]:
    """Walk verdict.json files and produce scorecard.json + scorecard.md.

    Reads canary and variant manifests to determine expected combinations,
    counts PASS/FAIL verdicts, and writes both machine-readable and
    human-readable scorecard outputs.
    """
    from autoskillit.core import atomic_write  # noqa: PLC0415

    eval_run_path = Path(eval_run_dir)

    try:
        canaries = _load_json(canary_manifest)
        variants = _load_json(variant_manifest)
    except (OSError, json.JSONDecodeError) as exc:
        return {"success": "false", "error": f"Failed to read manifest: {exc}"}

    try:
        canary_ids = [c["id"] for c in canaries]
        variant_ids = [v["id"] for v in variants]
    except (KeyError, TypeError) as exc:
        return {"success": "false", "error": f"Invalid manifest schema: {exc}"}
    if not canary_ids or not variant_ids:
        return {"success": "false", "error": "Empty canary or variant manifest"}
    total_runs = len(canary_ids) * len(variant_ids)
    passed_runs = 0
    vacuous_passes = 0

    canary_results: dict[str, dict[str, str]] = {}
    variant_summary: dict[str, dict[str, int]] = {
        v["id"]: {"pass": 0, "fail": 0} for v in variants
    }

    for canary in canaries:
        cid = canary["id"]
        canary_results[cid] = {}
        verdict_path = eval_run_path / cid / "verdict.json"
        verdict_data: dict | None = None
        try:
            verdict_data = json.loads(verdict_path.read_text())
        except (OSError, json.JSONDecodeError):
            pass

        for variant in variants:
            vid = variant["id"]
            if verdict_data and verdict_data.get("verdicts", {}).get(vid) is not None:
                verdict_entry = verdict_data["verdicts"][vid]
                overall = verdict_entry.get("overall", "FAIL")
            else:
                verdict_entry = {}
                overall = "FAIL"
            canary_results[cid][vid] = overall
            if overall == "PASS":
                passed_runs += 1
                variant_summary[vid]["pass"] += 1
                if _is_vacuous_pass(verdict_entry):
                    vacuous_passes += 1
            else:
                variant_summary[vid]["fail"] += 1

    pass_rate = passed_runs / total_runs if total_runs > 0 else 0.0
    effective_pass_rate = (passed_runs - vacuous_passes) / total_runs if total_runs > 0 else 0.0

    scorecard = {
        "pass_rate": pass_rate,
        "total_runs": total_runs,
        "passed_runs": passed_runs,
        "vacuous_passes": vacuous_passes,
        "effective_pass_rate": effective_pass_rate,
        "canary_results": canary_results,
        "variant_summary": {
            vid: {"pass": s["pass"], "fail": s["fail"]} for vid, s in variant_summary.items()
        },
    }

    scorecard_json_path = eval_run_path / "scorecard.json"
    atomic_write(scorecard_json_path, json.dumps(scorecard, indent=2))

    verdict_cache: dict[str, dict | None] = {}
    for canary in canaries:
        verdict_cache[canary["id"]] = try_load_json(eval_run_path / canary["id"] / "verdict.json")

    rows = []
    for canary in canaries:
        cid = canary["id"]
        for variant in variants:
            vid = variant["id"]
            overall = canary_results[cid].get(vid, "FAIL")
            criteria = ""
            if overall == "PASS":
                if vd := verdict_cache.get(cid):
                    verdicts_for_variant = vd.get("verdicts", {}).get(vid, {})
                    pass_count = sum(
                        1
                        for c in verdicts_for_variant.get("criteria", [])
                        if c.get("result") == "PASS"
                    )
                    total_count = len(verdicts_for_variant.get("criteria", []))
                    criteria = f"{pass_count}/{total_count}"
            rows.append(f"| {cid} | {vid} | {overall} | {criteria} |")

    md_lines = ["# Skill Eval Scorecard", "", "| Canary | Variant | Overall | Criteria Passed |"]
    md_lines.append("|--------|---------|---------|-----------------|")
    md_lines.extend(rows)
    md_lines.append("")
    md_lines.append(f"**Pass Rate:** {passed_runs}/{total_runs} ({pass_rate * 100:.1f}%)")

    scorecard_md_path = eval_run_path / "scorecard.md"
    atomic_write(scorecard_md_path, "\n".join(md_lines))

    return {
        "success": "true",
        "scorecard_path": str(scorecard_json_path),
        "pass_rate": str(pass_rate),
        "total_runs": str(total_runs),
        "passed_runs": str(passed_runs),
        "vacuous_passes": str(vacuous_passes),
        "effective_pass_rate": str(effective_pass_rate),
    }
