"""Frozen measurements and retained, interpreted instances of the observability deck."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from autoskillit.core import (
    ArtifactLease,
    TokenMeasure,
    require_aware_timestamp,
    validate_rollout_marker,
)
from autoskillit.core import atomic_write as _atomic_write

from .deck._html import render_deck_html
from .deck._measure_helpers import TOKEN_FIELDS
from .deck._payload import build_deck_payload
from .deck._trend import _trend_delta, _trend_group_key, _trend_measure_summary

_LIMITATION = "Coverage includes retained report-index sources, not complete historical coverage."


def _json_bytes(value: Any) -> bytes:
    """Compact sorted-key UTF-8 JSON, retaining finite measured floats."""
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _canonical_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    copied = [deepcopy(dict(row)) for row in rows]
    return sorted(copied, key=lambda row: (str(row.get("key", "")), _json_bytes(row)))


def _coverage(populations: Mapping[str, list[dict[str, Any]]]) -> dict[str, Any]:
    result: dict[str, Any] = {"limitation": _LIMITATION, "populations": {}}
    for name, rows in populations.items():
        result["populations"][name] = {
            "rows": len(rows),
            "untimed_rows": sum(
                not isinstance(row.get("time_ms"), int) or isinstance(row.get("time_ms"), bool)
                for row in rows
            ),
        }
        if name == "tools":
            continue
        measures = [row.get("token_usage", {}) if name == "subagents" else row for row in rows]
        result["populations"][name]["measure_states"] = {
            field: dict(
                sorted(
                    Counter(
                        TokenMeasure.measure_from_raw(
                            row.get(field) if isinstance(row, Mapping) else None
                        ).state.value
                        for row in measures
                    ).items()
                )
            )
            for field in TOKEN_FIELDS
        }
    return result


def _selected_runs(
    sessions: list[dict[str, Any]], change_id: str
) -> tuple[dict[str, Any], list[tuple[dict[str, Any], datetime]]]:
    selected: list[tuple[dict[str, Any], datetime]] = []
    declaration: dict[str, Any] | None = None
    for row in sessions:
        raw = row.get("rollout")
        if raw is None:
            continue
        if not isinstance(raw, Mapping):
            raise ValueError("rollout evidence must be an object or null when cohorting")
        if raw.get("change_id") != change_id:
            continue
        marker = validate_rollout_marker(raw, field_name="rollout")
        if declaration is not None and marker != declaration:
            raise ValueError(f"conflicting rollout declarations for {change_id}")
        declaration = dict(marker)
        try:
            instant = require_aware_timestamp(
                row.get("timestamp"), field_name="selected run timestamp"
            )
        except ValueError as exc:
            raise ValueError("selected run timestamp must be an aware ISO-8601 timestamp") from exc
        selected.append((row, instant))
    if declaration is None:
        raise ValueError(f"no retained runs match rollout change_id {change_id}")
    return declaration, selected


def _cohort(sessions: list[dict[str, Any]], change_id: str) -> dict[str, Any]:
    if not change_id:
        return {"change_id": None, "reason": "No rollout cohort applied.", "comparisons": []}
    declaration, selected = _selected_runs(sessions, change_id)
    cutover = datetime.fromisoformat(declaration["effective_at"])
    groups: dict[tuple[Any, ...], dict[str, list[dict[str, Any]]]] = defaultdict(
        lambda: {"before": [], "after": []}
    )
    before_count = 0
    for row, instant in selected:
        side = "before" if instant < cutover else "after"
        before_count += side == "before"
        groups[_trend_group_key(row)][side].append(row)
    comparisons: list[dict[str, Any]] = []
    for group, sides in sorted(groups.items(), key=lambda item: _json_bytes(item[0])):
        before, after = (_trend_measure_summary(sides[side]) for side in ("before", "after"))
        for metric in (*TOKEN_FIELDS, "failure_share"):
            comparisons.append(
                {
                    "group": list(group),
                    "metric": metric,
                    "before": {**before["failure_share"], "unit": "ratio"}
                    if metric == "failure_share"
                    else {**before["measures"][metric], "unit": "tokens/session"},
                    "after": {**after["failure_share"], "unit": "ratio"}
                    if metric == "failure_share"
                    else {**after["measures"][metric], "unit": "tokens/session"},
                    "delta": _trend_delta(
                        before,
                        after,
                        metric,
                        unit="percentage_points"
                        if metric == "failure_share"
                        else "tokens/session",
                    ),
                }
            )
    return {
        **declaration,
        "before_count": before_count,
        "after_count": len(selected) - before_count,
        "excluded_count": len(sessions) - len(selected),
        "marked_pre_cutover_observation": before_count > 0,
        "preregistration": (
            "Marked pre-cutover observations provide a baseline; logical start time does not "
            "independently prove when the prediction was registered."
            if before_count
            else "After-only declarations do not establish preregistration; "
            "no marked pre-cutover baseline was retained."
        ),
        "comparisons": comparisons,
    }


def _measured_nodes(value: Any, path: str) -> Iterable[tuple[str, dict[str, Any]]]:
    if isinstance(value, dict):
        if "value" in value and "state" in value:
            yield path, value
        else:
            for key in sorted(value):
                yield from _measured_nodes(value[key], path + "/" + key)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _measured_nodes(child, path + "/" + str(index))


def _evidence(payload: dict[str, Any], cohort: dict[str, Any]) -> dict[str, Any]:
    evidence: dict[str, Any] = {}
    sources = {
        "cohort": "sessions",
        "spend": "skills",
        "efficiency": "skills",
        "skill": "skills",
        "role": "roles",
    }
    for view in payload["views"]:
        if view["status"] != "built":
            continue
        view_id = view["id"]
        table_name = sources.get(view_id, view_id)
        table = payload["tables"].get(table_name)
        if table:
            for index, row in enumerate(table["rows"]):
                measurement = dict(zip(table["columns"], row, strict=True))
                identity = f"{view_id}/table/{index}"
                evidence[identity] = {
                    "view_id": view_id,
                    "source": identity,
                    "measurement": measurement,
                }
        prepared_name = sources.get(view_id, view_id)
        for path, measurement in _measured_nodes(
            payload["prepared"].get(prepared_name), prepared_name
        ):
            identity = view_id + "/prepared/" + path
            evidence[identity] = {"view_id": view_id, "source": path, "measurement": measurement}
    for comparison in cohort["comparisons"]:
        identity = "trend/rollout/" + _digest([comparison["group"], comparison["metric"]])
        evidence[identity] = {"view_id": "trend", "source": "rollout", "measurement": comparison}
    return evidence


def prepare_run_analysis(
    session_rows: Iterable[Mapping[str, Any]],
    *,
    request_rows: Iterable[Mapping[str, Any]] = (),
    tool_rows: Iterable[Mapping[str, Any]] = (),
    subagent_rows: Iterable[Mapping[str, Any]] = (),
    turn_rows: Iterable[Mapping[str, Any]] = (),
    index_schema_version: int,
    change_id: str = "",
    render_bundle: dict[str, Any],
) -> dict[str, Any]:
    """Freeze one canonical all-population snapshot and its prepared measurements."""
    populations = {
        name: _canonical_rows(rows)
        for name, rows in (
            ("sessions", session_rows),
            ("requests", request_rows),
            ("tools", tool_rows),
            ("subagents", subagent_rows),
            ("turns", turn_rows),
        )
    }
    anchor_ms = max(
        (
            row["time_ms"]
            for rows in populations.values()
            for row in rows
            if isinstance(row.get("time_ms"), int) and not isinstance(row.get("time_ms"), bool)
        ),
        default=0,
    )
    payload = build_deck_payload(
        populations["sessions"],
        request_rows=populations["requests"],
        tool_rows=populations["tools"],
        subagent_rows=populations["subagents"],
        turn_rows=populations["turns"],
        generated_at=datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=anchor_ms),
        index_schema_version=index_schema_version,
    )
    cohort = _cohort(populations["sessions"], change_id)
    packet: dict[str, Any] = {
        "populations": populations,
        "payload": payload,
        "coverage": _coverage(populations),
        "cohort": cohort,
        "evidence": _evidence(payload, cohort),
        "render_bundle": deepcopy(render_bundle),
    }
    packet["input_fingerprint"] = _digest(packet)
    return packet


def _check_packet(packet: dict[str, Any], content: bytes) -> str:
    if not isinstance(packet, dict):
        raise ValueError("frozen measurements must be an object")
    identity_inputs = {key: value for key, value in packet.items() if key != "input_fingerprint"}
    if _json_bytes(packet) != content or packet.get("input_fingerprint") != _digest(
        identity_inputs
    ):
        raise ValueError("corrupt frozen measurements or input fingerprint")
    return hashlib.sha256(content).hexdigest()


def _validate_findings(
    candidate: Any, packet: dict[str, Any], snapshot_digest: str
) -> dict[str, Any]:
    if not isinstance(candidate, dict) or set(candidate) != {
        "snapshot_digest",
        "interpreter",
        "findings",
    }:
        raise ValueError("findings must contain exactly snapshot_digest, interpreter, findings")
    if candidate["snapshot_digest"] != snapshot_digest:
        raise ValueError("findings snapshot_digest does not match frozen measurements")
    interpreter = candidate["interpreter"]
    if not isinstance(interpreter, dict) or set(interpreter) != {"role", "model", "prompt"}:
        raise ValueError("interpreter must contain exactly role, model, prompt identities")
    if not isinstance(interpreter["role"], str) or not interpreter["role"].strip():
        raise ValueError("interpreter role must be nonblank text")
    if any(value is not None and not isinstance(value, str) for value in interpreter.values()):
        raise ValueError("interpreter identities must be text or null when unknown")
    findings = candidate["findings"]
    if not isinstance(findings, list) or not findings:
        raise ValueError("interpretation findings are required")
    built_views = {view["id"] for view in packet["payload"]["views"] if view["status"] == "built"}
    empty = not any(packet["populations"].values())
    for finding in findings:
        _validate_finding(finding, built_views, packet["evidence"], empty=empty)
    return candidate


def _validate_finding(
    finding: Any, built_views: set[str], evidence: dict[str, Any], *, empty: bool
) -> None:
    if not isinstance(finding, dict) or set(finding) != {"view_id", "text", "evidence_ids"}:
        raise ValueError("each finding must contain exactly view_id, text, evidence_ids")
    if not isinstance(finding["view_id"], str) or finding["view_id"] not in built_views:
        raise ValueError("finding view_id must name a built view")
    text = finding["text"]
    if not isinstance(text, str) or not text.strip():
        raise ValueError("finding text must be nonblank plain text")
    references = finding["evidence_ids"]
    if not isinstance(references, list):
        raise ValueError("evidence_ids must be a list")
    if not references and not (empty and "no observations" in text.lower()):
        raise ValueError(
            "findings require evidence, or an explicit empty-corpus no observations finding"
        )
    for reference in references:
        if not isinstance(reference, str) or reference not in evidence:
            raise ValueError("finding references unknown evidence")
        if evidence[reference]["view_id"] != finding["view_id"]:
            raise ValueError("finding references cross-view evidence")


def _analysis_payload(
    packet: dict[str, Any], findings: dict[str, Any], generated_at_ms: int
) -> dict[str, Any]:
    payload = deepcopy(packet["payload"])
    payload["analysis"] = {
        "input_fingerprint": packet["input_fingerprint"],
        "generated_at_ms": generated_at_ms,
        "coverage": packet["coverage"],
        "cohort": packet["cohort"],
        "evidence": packet["evidence"],
        "interpreter": findings["interpreter"],
        "findings": findings["findings"],
    }
    return payload


def _manifest(
    packet: dict[str, Any], findings: dict[str, Any], snapshot_digest: str, generated_at_ms: int
) -> dict[str, Any]:
    return {
        "input_fingerprint": packet["input_fingerprint"],
        "snapshot_digest": snapshot_digest,
        "findings_digest": _digest(findings),
        "generated_at_ms": generated_at_ms,
        "anchor_ms": packet["payload"]["generated_at_ms"],
        "coverage": packet["coverage"],
        "cohort": packet["cohort"],
        "interpreter": findings["interpreter"],
    }


def _verify_completed(output_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    measurements = (output_dir / "measurements.json").read_bytes()
    packet = json.loads(measurements)
    snapshot_digest = _check_packet(packet, measurements)
    content = (output_dir / "findings.json").read_bytes()
    findings = _validate_findings(json.loads(content), packet, snapshot_digest)
    if content != _json_bytes(findings):
        raise ValueError("corrupt canonical findings")
    generated_at_ms = manifest.get("generated_at_ms")
    if not isinstance(generated_at_ms, int) or isinstance(generated_at_ms, bool):
        raise ValueError("invalid completed analysis timestamp")
    if manifest != _manifest(packet, findings, snapshot_digest, generated_at_ms):
        raise ValueError("corrupt completion manifest or findings digest")
    expected_html = render_deck_html(
        _analysis_payload(packet, findings, generated_at_ms), render_bundle=packet["render_bundle"]
    ).encode("utf-8")
    if (output_dir / "report.html").read_bytes() != expected_html:
        raise ValueError("corrupt completed report HTML")
    return findings


def _completed_findings(root: Path, packet: dict[str, Any]) -> dict[str, Any] | None:
    winner = None
    for path in sorted(root.glob("*/analysis.json")):
        read_error: OSError | None = None
        try:
            manifest_bytes = path.read_bytes()
        except (FileNotFoundError, NotADirectoryError, PermissionError) as exc:
            read_error = exc
        if read_error is not None:
            raise ValueError(
                f"retained completion manifest is unavailable: {path}"
            ) from read_error
        manifest = json.loads(manifest_bytes)
        if manifest.get("input_fingerprint") != packet["input_fingerprint"]:
            continue
        if _json_bytes(manifest) != manifest_bytes:
            raise ValueError("corrupt canonical completion manifest")
        findings = _verify_completed(path.parent, manifest)
        if winner is not None and findings != winner:
            raise ValueError(
                "inconsistent completed interpretations for the same input fingerprint"
            )
        winner = findings
    return winner


def write_run_analysis_packet(output_dir: Path, packet: dict[str, Any]) -> dict[str, Any]:
    """Create a dated instance, supplying verified prior interpretation when available."""
    if output_dir.exists():
        raise FileExistsError(f"analysis output directory already exists: {output_dir}")
    try:
        datetime.strptime(output_dir.name[:17], "%Y-%m-%d_%H%M%S")
    except ValueError as exc:
        raise ValueError(
            "analysis directory must start with a UTC YYYY-MM-DD_HHMMSS date"
        ) from exc
    content = _json_bytes(packet)
    snapshot_digest = _check_packet(packet, content)
    winner = _completed_findings(output_dir.parent, packet)
    output_dir.mkdir(parents=True, exist_ok=False)
    _atomic_write(output_dir / "measurements.json", content, exclusive=True)
    if winner is not None:
        _atomic_write(output_dir / ".candidate-findings.json", _json_bytes(winner), exclusive=True)
    return {
        "output_dir": str(output_dir),
        "input_fingerprint": packet["input_fingerprint"],
        "snapshot_digest": snapshot_digest,
        "interpretation_required": winner is None,
        "candidate_path": str(output_dir / ".candidate-findings.json"),
    }


def finish_run_analysis(output_dir: Path, *, analysis_generated_at: datetime) -> dict[str, Any]:
    """Publish from frozen inputs under a finite publication lease; completion is last."""
    if analysis_generated_at.utcoffset() is None:
        raise ValueError("analysis_generated_at must be timezone-aware")
    if (output_dir / "analysis.json").exists():
        raise ValueError("analysis is already completed")
    measurements = (output_dir / "measurements.json").read_bytes()
    packet = json.loads(measurements)
    snapshot_digest = _check_packet(packet, measurements)
    candidate = _validate_findings(
        json.loads((output_dir / ".candidate-findings.json").read_bytes()), packet, snapshot_digest
    )
    generated_at_ms = (analysis_generated_at - datetime(1970, 1, 1, tzinfo=UTC)) // timedelta(
        milliseconds=1
    )
    with ArtifactLease.acquire_exclusive(output_dir.parent / ".publication.lock", timeout=5.0):
        if (output_dir / "analysis.json").exists():
            raise ValueError("analysis is already completed")
        findings = _completed_findings(output_dir.parent, packet) or candidate
        findings_bytes = _json_bytes(findings)
        html = render_deck_html(
            _analysis_payload(packet, json.loads(findings_bytes), generated_at_ms),
            render_bundle=packet["render_bundle"],
        )
        _atomic_write(output_dir / "findings.json", findings_bytes, exclusive=True)
        _atomic_write(output_dir / "report.html", html, exclusive=True)
        (output_dir / ".candidate-findings.json").unlink()
        _atomic_write(
            output_dir / "analysis.json",
            _json_bytes(_manifest(packet, findings, snapshot_digest, generated_at_ms)),
            exclusive=True,
        )
    return {"report_path": str(output_dir / "report.html"), "completed": True}
