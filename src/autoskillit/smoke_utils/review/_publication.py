"""Rendering, publication preparation, and atomic artifact writes for the
experimental review pipeline.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

from autoskillit.smoke_utils._review_contracts import _is_non_empty_string
from autoskillit.smoke_utils.review._constants import _bounded_utf8

_MAX_GITHUB_REVIEW_BODY_BYTES = 60 * 1024
_MAX_REVIEW_MESSAGE_BYTES = 32 * 1024
_MAX_REVIEW_PATH_BYTES = 2048
_MAX_REVIEW_ROLE_BYTES = 256
_MAX_REVIEW_CLAIM_BYTES = 4096
_MAX_REVIEW_PROVENANCE_ID_BYTES = 1024
_PUBLICATION_FILENAMES = {
    "raw_findings": "raw_findings_{pr_number}.json",
    "diff_context": "diff_context_{pr_number}.json",
    "local_findings": "local_findings_{pr_number}.json",
    "review_receipt": "batch_review_response_{pr_number}.json",
}


def render_review_finding_body(finding: Mapping[str, object]) -> str:
    """Render one finding identically for primary and fallback GitHub effects."""
    severity = _bounded_utf8(str(finding.get("severity", "")), 128)
    dimension = _bounded_utf8(str(finding.get("dimension", "")), 256)
    message = _bounded_utf8(str(finding.get("message", "")), _MAX_REVIEW_MESSAGE_BYTES)
    body = f"[{severity}] {dimension}: {message}"
    evidence = finding.get("evidence")
    if isinstance(evidence, list):
        rendered_evidence = [
            (
                f"{_bounded_utf8(str(item.get('path')), _MAX_REVIEW_PATH_BYTES)}:"
                f"{item.get('line')} "
                f"[{_bounded_utf8(str(item.get('role')), _MAX_REVIEW_ROLE_BYTES)}] "
                f"{_bounded_utf8(str(item.get('claim')), _MAX_REVIEW_CLAIM_BYTES)}"
            )
            for item in evidence
            if isinstance(item, Mapping)
        ]
        if rendered_evidence:
            body += "\nEvidence: " + "; ".join(rendered_evidence)
    candidate_id = finding.get("candidate_id")
    disposition_id = finding.get("disposition_id")
    provenance = ""
    if _is_non_empty_string(candidate_id) and _is_non_empty_string(disposition_id):
        bounded_candidate_id = _bounded_utf8(
            str(candidate_id),
            _MAX_REVIEW_PROVENANCE_ID_BYTES,
        )
        bounded_disposition_id = _bounded_utf8(
            str(disposition_id),
            _MAX_REVIEW_PROVENANCE_ID_BYTES,
        )
        provenance = (
            f"\nProvenance: candidate_id={bounded_candidate_id} "
            f"disposition_id={bounded_disposition_id}"
        )
    body_budget = _MAX_GITHUB_REVIEW_BODY_BYTES - len(provenance.encode("utf-8"))
    return _bounded_utf8(body, body_budget) + provenance


def normalize_local_review_finding(finding: Mapping[str, object]) -> dict[str, object]:
    """Copy a local review finding and add the canonical path/body aliases."""
    normalized = dict(finding)
    if "file" in normalized:
        normalized["path"] = normalized["file"]
    normalized["body"] = render_review_finding_body(normalized)
    return normalized


def _normalize_handoff_finding(finding: Mapping[str, object]) -> dict[str, object]:
    normalized = dict(finding)
    normalized.setdefault("path", normalized.get("file", ""))
    normalized.setdefault("body", render_review_finding_body(normalized))
    normalized.setdefault("side", "RIGHT")
    normalized.setdefault("code_region", "")
    return normalized


def prepare_experimental_review_publication(
    *,
    raw_ledger: Mapping[str, object],
    survivors: Sequence[Mapping[str, object]],
    snapshot: Mapping[str, str],
    annotation_generation_id: str,
    mode: str,
    snapshot_is_fresh: bool,
    handoff_metadata: Mapping[str, object] | None = None,
    receipt: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build one immutable publication generation or suppress stale effects."""
    if mode not in {"github", "local"}:
        raise ValueError(f"mode must be 'github' or 'local', got {mode!r}")
    head_sha = snapshot.get("head_sha", snapshot.get("_head_sha", ""))
    base_sha = snapshot.get("base_sha", snapshot.get("_base_sha", ""))
    if not head_sha or not base_sha or not annotation_generation_id:
        raise ValueError("snapshot head/base and annotation generation must be non-empty")

    canonical_ledger = json.dumps(raw_ledger, sort_keys=True, separators=(",", ":"))
    normalized_survivors = [_normalize_handoff_finding(finding) for finding in survivors]
    normalized_survivors = json.loads(
        json.dumps(normalized_survivors, sort_keys=True, separators=(",", ":"))
    )
    effective_survivors = normalized_survivors if snapshot_is_fresh else []
    metadata = json.loads(
        json.dumps(dict(handoff_metadata or {}), sort_keys=True, separators=(",", ":"))
    )
    generation_input = json.dumps(
        {
            "annotation_generation_id": annotation_generation_id,
            "handoff_metadata": metadata,
            "mode": mode,
            "raw_ledger": json.loads(canonical_ledger),
            "snapshot": dict(snapshot),
            "snapshot_is_fresh": snapshot_is_fresh,
            "survivors": effective_survivors,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    identity: dict[str, object] = {
        "_head_sha": head_sha,
        "_base_sha": base_sha,
        "annotation_generation_id": annotation_generation_id,
        "review_generation_id": hashlib.sha256(generation_input.encode()).hexdigest(),
    }
    merge_base_sha = snapshot.get("merge_base_sha", snapshot.get("_merge_base_sha", ""))
    if merge_base_sha:
        identity["_merge_base_sha"] = merge_base_sha

    normalized_ledger = json.loads(canonical_ledger)
    raw_findings = {
        **normalized_ledger,
        **identity,
        "state": "complete" if snapshot_is_fresh else "stale_snapshot",
        "survivors": effective_survivors,
    }
    if not snapshot_is_fresh:
        return {
            "state": "stale_snapshot",
            "artifact_order": ["raw_findings"],
            "artifacts": {"raw_findings": raw_findings},
        }

    diff_context = {**metadata, **identity, "context_entries": normalized_survivors}
    artifacts: dict[str, object] = {
        "raw_findings": raw_findings,
        "diff_context": diff_context,
    }
    artifact_order = ["raw_findings", "diff_context"]
    if mode == "local":
        local_findings = {**metadata, **identity, "findings": normalized_survivors}
        artifacts["local_findings"] = local_findings
        artifact_order.append("local_findings")
    elif receipt is not None:
        normalized_receipt = json.loads(
            json.dumps(dict(receipt), sort_keys=True, separators=(",", ":"))
        )
        review_receipt = {**normalized_receipt, **identity}
        artifacts["review_receipt"] = review_receipt
        artifact_order.append("review_receipt")
    return {
        "state": "complete",
        "artifact_order": artifact_order,
        "artifacts": artifacts,
    }


def _write_temp_bytes(directory: Path, final_name: str, content: bytes) -> Path:
    fd, temporary_name = tempfile.mkstemp(
        dir=directory,
        prefix=f".{final_name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        stream = os.fdopen(fd, "wb")
    except Exception:
        os.close(fd)
        temporary_path.unlink(missing_ok=True)
        raise
    try:
        with stream:
            stream.write(content)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    return temporary_path


def publish_experimental_review_artifacts(
    *,
    publication: Mapping[str, object],
    output_dir: str,
    pr_number: str,
) -> dict[str, object]:
    """Atomically publish a prepared generation and roll back partial renames."""
    root = Path(output_dir)
    if not root.is_absolute():
        raise ValueError(f"output_dir must be absolute, got {output_dir!r}")
    identifier = str(pr_number)
    if not identifier or Path(identifier).name != identifier or identifier in {".", ".."}:
        raise ValueError(f"pr_number must be a path-safe identifier, got {pr_number!r}")

    raw_order = publication.get("artifact_order")
    raw_artifacts = publication.get("artifacts")
    if not isinstance(raw_order, list) or not isinstance(raw_artifacts, Mapping):
        raise ValueError("publication must contain artifact_order and artifacts")
    order = [str(name) for name in raw_order]
    if len(order) != len(set(order)) or set(order) != set(raw_artifacts):
        raise ValueError("artifact_order must name every artifact exactly once")
    if any(name not in _PUBLICATION_FILENAMES for name in order):
        raise ValueError("publication contains an unknown artifact")
    if not order or order[0] != "raw_findings":
        raise ValueError("raw_findings must be the first publication")
    if "local_findings" in order and order[-1] != "local_findings":
        raise ValueError("local_findings must be the final publication marker")
    if "review_receipt" in order and order[-1] != "review_receipt":
        raise ValueError("review_receipt must be published after raw findings and handoffs")

    root.mkdir(parents=True, exist_ok=True)
    final_paths = {
        name: root / filename.format(pr_number=identifier)
        for name, filename in _PUBLICATION_FILENAMES.items()
    }
    retired_names = [name for name in final_paths if name not in order]
    prior_bytes = {
        name: path.read_bytes() if path.exists() else None for name, path in final_paths.items()
    }
    documents = {
        name: (
            json.dumps(raw_artifacts[name], sort_keys=True, separators=(",", ":")) + "\n"
        ).encode()
        for name in order
    }
    staged: dict[str, Path] = {}
    changed: list[str] = []
    try:
        for name in order:
            staged[name] = _write_temp_bytes(
                root,
                final_paths[name].name,
                documents[name],
            )
        for name in order[:-1]:
            os.replace(staged[name], final_paths[name])
            staged.pop(name)
            changed.append(name)
        for name in retired_names:
            if final_paths[name].exists():
                final_paths[name].unlink()
                changed.append(name)
        marker_name = order[-1]
        os.replace(staged[marker_name], final_paths[marker_name])
        staged.pop(marker_name)
        changed.append(marker_name)
    except Exception as publication_error:
        rollback_error: Exception | None = None
        for temporary_path in staged.values():
            temporary_path.unlink(missing_ok=True)
        for name in reversed(changed):
            try:
                previous = prior_bytes[name]
                if previous is None:
                    final_paths[name].unlink(missing_ok=True)
                else:
                    rollback_path = _write_temp_bytes(
                        root,
                        final_paths[name].name,
                        previous,
                    )
                    os.replace(rollback_path, final_paths[name])
            except OSError as error:  # pragma: no cover - exceptional filesystem failure
                rollback_error = error
        if rollback_error is not None:
            raise ExceptionGroup(
                "publication failed and rollback was incomplete",
                [publication_error, rollback_error],
            )
        raise RuntimeError(
            "experimental review artifact publication failed"
        ) from publication_error

    publication_records = [
        {
            "artifact": name,
            "byte_length": len(documents[name]),
            "path": str(final_paths[name]),
            "sha256": hashlib.sha256(documents[name]).hexdigest(),
        }
        for name in order
    ]
    return {
        "state": publication.get("state"),
        "published_paths": {name: str(final_paths[name]) for name in order},
        "publication_records": publication_records,
    }
