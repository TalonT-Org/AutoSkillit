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
from typing import cast

import regex as re

from autoskillit.core import (
    ContainmentError,
    GitHubReviewReceipt,
    fsync_directory,
    fsync_file,
    github_review_receipt_filename,
    is_final_github_review_state,
    read_stable_contained_bytes,
)
from autoskillit.smoke_utils._review_contracts import (
    _is_non_empty_string,
    review_handoff_pair_error,
)
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
    "github_review_binding": "github_review_binding_{pr_number}.json",
}
_GENERATION_IDENTITY_KEYS = (
    "_head_sha",
    "_base_sha",
    "_merge_base_sha",
    "annotation_generation_id",
    "review_generation_id",
)


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


def render_unpostable_review_section(
    unpostable: Sequence[Mapping[str, object]],
) -> str:
    """Render body-only findings with their explicit admission reasons."""
    if not unpostable:
        return ""
    lines = ["## Outside Diff Range"]
    for finding in unpostable:
        path = _bounded_utf8(str(finding.get("file", "")), _MAX_REVIEW_PATH_BYTES)
        line = finding.get("line", "")
        reason = _bounded_utf8(str(finding.get("admission_reason", "")), 256)
        lines.append(f"- `{path}:{line}` ({reason}) — {render_review_finding_body(finding)}")
    return _bounded_utf8("\n".join(lines), _MAX_GITHUB_REVIEW_BODY_BYTES)


def normalize_local_review_finding(finding: Mapping[str, object]) -> dict[str, object]:
    """Copy a local review finding and add the canonical path/body aliases."""
    normalized = dict(finding)
    if "file" in normalized:
        normalized["path"] = normalized["file"]
    normalized["body"] = render_review_finding_body(normalized)
    return normalized


def _normalize_handoff_finding(finding: Mapping[str, object]) -> dict[str, object]:
    if "anchor_digest" in finding:
        raise ValueError("anchor_digest is produced only by diff-context enrichment")
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
    unpostable: Sequence[Mapping[str, object]] = (),
    review_level_findings: Sequence[Mapping[str, object]] = (),
) -> dict[str, object]:
    """Build one immutable publication generation or suppress stale effects."""
    if mode not in ("github", "local"):
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
    normalized_unpostable = json.loads(
        json.dumps(
            [_normalize_handoff_finding(finding) for finding in unpostable],
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    normalized_review_level_findings = json.loads(
        json.dumps(
            [_normalize_handoff_finding(finding) for finding in review_level_findings],
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    effective_survivors = normalized_survivors if snapshot_is_fresh else []
    effective_unpostable = normalized_unpostable if snapshot_is_fresh else []
    effective_review_level_findings = normalized_review_level_findings if snapshot_is_fresh else []
    metadata = json.loads(
        json.dumps(dict(handoff_metadata or {}), sort_keys=True, separators=(",", ":"))
    )
    requested_schema_version = metadata.pop("schema_version", None)
    review_level_candidate_ids = {
        finding.get("candidate_id") for finding in effective_review_level_findings
    }
    context_entries = [
        finding
        for finding in effective_survivors
        if finding.get("candidate_id") not in review_level_candidate_ids
    ]
    schema_version = 2 if requested_schema_version == 2 and not context_entries else 1
    generation_input = json.dumps(
        {
            "annotation_generation_id": annotation_generation_id,
            "handoff_metadata": metadata,
            "mode": mode,
            "raw_ledger": json.loads(canonical_ledger),
            "snapshot": dict(snapshot),
            "snapshot_is_fresh": snapshot_is_fresh,
            "survivors": effective_survivors,
            "unpostable": effective_unpostable,
            "review_level_findings": effective_review_level_findings,
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
        "unpostable": effective_unpostable,
        "outside_diff_range": render_unpostable_review_section(effective_unpostable),
    }
    if not snapshot_is_fresh:
        return {
            "mode": mode,
            "state": "stale_snapshot",
            "artifact_order": ["raw_findings"],
            "artifacts": {"raw_findings": raw_findings},
        }

    diff_context = {
        **metadata,
        **identity,
        "schema_version": schema_version,
        "context_entries": context_entries,
        "unpostable": normalized_unpostable,
        "review_level_findings": effective_review_level_findings,
    }
    artifacts: dict[str, object] = {
        "raw_findings": raw_findings,
        "diff_context": diff_context,
    }
    artifact_order = ["raw_findings", "diff_context"]
    if mode == "local":
        local_findings = {
            **metadata,
            **identity,
            "findings": normalized_survivors,
            "unpostable": normalized_unpostable,
        }
        artifacts["local_findings"] = local_findings
        artifact_order.append("local_findings")
    return {
        "mode": mode,
        "state": "complete",
        "artifact_order": artifact_order,
        "artifacts": artifacts,
    }


def _load_github_review_receipt(
    root: Path,
    pr_number: str,
    *,
    head_sha: str,
) -> GitHubReviewReceipt:
    if re.fullmatch(r"[1-9][0-9]*", pr_number) is None:
        raise ValueError("pr_number must be canonical decimal")
    try:
        identifier = int(pr_number)
    except ValueError as exc:
        raise ValueError("pr_number must be canonical decimal") from exc
    try:
        _resolved, data = read_stable_contained_bytes(
            root / github_review_receipt_filename(identifier),
            root,
            max_size_bytes=1_000_000,
        )
    except (ContainmentError, OSError) as exc:
        raise ValueError(f"receipt unreadable: {exc}") from exc
    try:
        receipt = GitHubReviewReceipt.from_wire(json.loads(data))
    except (
        KeyError,
        TypeError,
        ValueError,
        OverflowError,
        RecursionError,
    ) as exc:
        raise ValueError(f"receipt malformed: {exc}") from exc
    if (
        receipt.pr_number != identifier
        or not is_final_github_review_state(receipt.state.value)
        or type(receipt.review_id) is not int
        or receipt.review_id <= 0
    ):
        raise ValueError("receipt is not a final successful review")
    if receipt.head_sha != head_sha:
        raise ValueError("receipt head does not match the review generation")
    return receipt


def _publication_order(
    publication: Mapping[str, object],
    root: Path,
    identifier: str,
) -> tuple[list[str], dict[str, object]]:
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
    if "github_review_binding" in order and order[-1] != "github_review_binding":
        raise ValueError("github_review_binding must be the final publication marker")
    mode = publication.get("mode")
    if mode not in {"github", "local"}:
        raise ValueError("publication mode must be 'github' or 'local'")

    artifacts: dict[str, object] = dict(raw_artifacts)
    if publication.get("state") == "complete" and mode == "github":
        if order != ["raw_findings", "diff_context"]:
            raise ValueError("complete github publication requires raw findings and diff context")
        diff_context = cast(Mapping[str, object], artifacts["diff_context"])
        receipt = _load_github_review_receipt(
            root,
            identifier,
            head_sha=cast(str, diff_context["_head_sha"]),
        )
        artifacts["github_review_binding"] = {
            **{key: diff_context[key] for key in _GENERATION_IDENTITY_KEYS if key in diff_context},
            "receipt_operation_key": receipt.operation_key,
        }
        order.append("github_review_binding")
    return order, artifacts


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
        fsync_file(temporary_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    return temporary_path


def _restore_publication_files(
    root: Path,
    final_paths: Mapping[str, Path],
    prior_bytes: Mapping[str, bytes | None],
    changed: Sequence[str],
    marker_names: tuple[str, ...],
) -> OSError | None:
    try:
        for name in marker_names:
            if final_paths[name].exists():
                final_paths[name].unlink()
        fsync_directory(root)
    except OSError as error:
        return error

    rollback_error: OSError | None = None
    payload_names = [name for name in dict.fromkeys(changed) if name not in marker_names]
    for name in payload_names:
        try:
            previous = prior_bytes[name]
            if previous is None:
                final_paths[name].unlink(missing_ok=True)
            else:
                rollback_path = _write_temp_bytes(root, final_paths[name].name, previous)
                try:
                    os.replace(rollback_path, final_paths[name])
                finally:
                    rollback_path.unlink(missing_ok=True)
            fsync_directory(root)
        except OSError as error:
            rollback_error = error

    marker_names_to_restore = (
        [name for name in marker_names if prior_bytes[name] is not None]
        if rollback_error is None
        else []
    )
    for name in marker_names_to_restore:
        try:
            rollback_path = _write_temp_bytes(
                root, final_paths[name].name, prior_bytes[name] or b""
            )
            try:
                os.replace(rollback_path, final_paths[name])
            finally:
                rollback_path.unlink(missing_ok=True)
            fsync_directory(root)
        except OSError as error:
            rollback_error = error
    return rollback_error


def publish_experimental_review_artifacts(
    *,
    publication: Mapping[str, object],
    output_dir: str,
    pr_number: str,
) -> dict[str, object]:
    """Publish artifacts using a marker derived at commit time from the sibling server receipt.

    The publisher never writes, replaces, or deletes the server receipt.
    """
    root = Path(output_dir)
    if not root.is_absolute():
        raise ValueError(f"output_dir must be absolute, got {output_dir!r}")
    identifier = str(pr_number)
    if not identifier or Path(identifier).name != identifier or identifier in {".", ".."}:
        raise ValueError(f"pr_number must be a path-safe identifier, got {pr_number!r}")

    order, artifacts = _publication_order(publication, root, identifier)

    root.mkdir(parents=True, exist_ok=True)
    final_paths = {
        name: root / filename.format(pr_number=identifier)
        for name, filename in _PUBLICATION_FILENAMES.items()
    }
    marker_names = ("local_findings", "github_review_binding")
    retired_names = [
        name for name in final_paths if name not in order and name not in marker_names
    ]
    prior_bytes = {
        name: path.read_bytes() if path.exists() else None for name, path in final_paths.items()
    }
    documents = {
        name: (json.dumps(artifacts[name], sort_keys=True, separators=(",", ":")) + "\n").encode()
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

        for name in marker_names:
            if final_paths[name].exists():
                final_paths[name].unlink()
                changed.append(name)
        fsync_directory(root)

        marker_name = order[-1] if order[-1] in marker_names else None
        payload_order = order[:-1] if marker_name is not None else order
        for name in payload_order:
            os.replace(staged[name], final_paths[name])
            staged.pop(name)
            changed.append(name)
            fsync_directory(root)
        for name in retired_names:
            if final_paths[name].exists():
                final_paths[name].unlink()
                changed.append(name)
                fsync_directory(root)
        if marker_name is not None:
            os.replace(staged[marker_name], final_paths[marker_name])
            staged.pop(marker_name)
            changed.append(marker_name)
            fsync_directory(root)
    except Exception as publication_error:
        for temporary_path in staged.values():
            temporary_path.unlink(missing_ok=True)
        rollback_error = (
            _restore_publication_files(
                root,
                final_paths,
                prior_bytes,
                changed,
                marker_names,
            )
            if changed
            else None
        )
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


def github_review_handoff_error(*, output_dir: str, pr_number: str) -> str | None:
    """Return a stable reason when the GitHub binding cannot be paired to its receipt."""
    root = Path(output_dir)
    if not root.is_absolute():
        return f"output_dir must be absolute, got {output_dir!r}"

    documents: dict[str, object] = {}
    for name in ("diff_context", "github_review_binding"):
        path = root / _PUBLICATION_FILENAMES[name].format(pr_number=pr_number)
        try:
            _resolved, data = read_stable_contained_bytes(path, root)
            documents[name] = json.loads(data)
        except (ContainmentError, OSError, ValueError, RecursionError) as exc:
            return f"{name} unreadable: {exc}"

    pair_error = review_handoff_pair_error(
        documents["diff_context"],
        documents["github_review_binding"],
    )
    if pair_error is not None:
        return pair_error

    binding = cast(Mapping[str, object], documents["github_review_binding"])
    try:
        receipt = _load_github_review_receipt(
            root,
            pr_number,
            head_sha=cast(str, binding["_head_sha"]),
        )
    except ValueError as exc:
        return str(exc)
    if binding.get("receipt_operation_key") != receipt.operation_key:
        return "receipt operation key does not match the github review binding"
    return None
