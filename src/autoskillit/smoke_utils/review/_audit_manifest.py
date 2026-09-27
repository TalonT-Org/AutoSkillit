"""Build and verify immutable authority for one review-audit run."""

from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

import regex as re

from autoskillit.core import DiffAnchorAuthority
from autoskillit.core.io import atomic_write
from autoskillit.smoke_utils._review_contracts import select_experimental_review_dispatch
from autoskillit.smoke_utils.review._constants import _STANDARD_REVIEW_DIMENSIONS
from autoskillit.smoke_utils.review._validation import deletion_regression_is_eligible

REVIEW_AUDIT_MANIFEST_SCHEMA_VERSION = 1
REVIEW_AUDIT_MAX_ATTEMPTS = 3
REVIEW_AUDIT_SLOT_FIELD = "autoskillit-review-audit-slot"

_REPOSITORY_RE = re.compile(r"^[^/\s]+/[^/\s]+$")
_HEAD_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class ReviewAuditInputError(ValueError):
    """Raised when review-audit authority or caller input is invalid."""


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _content_digest(value: Mapping[str, object]) -> str:
    body = {key: item for key, item in value.items() if key != "content_sha256"}
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _read_json(path: Path, *, tolerate_error: bool = False) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        if tolerate_error:
            return None
        raise


def _standard_dimensions(metrics_marker: object) -> list[str]:
    agents = metrics_marker.get("dispatch_agents") if isinstance(metrics_marker, Mapping) else None
    if (
        not isinstance(agents, list)
        or not agents
        or not all(
            isinstance(agent, str) and agent in _STANDARD_REVIEW_DIMENSIONS for agent in agents
        )
    ):
        return list(_STANDARD_REVIEW_DIMENSIONS)
    selected = set(agents)
    return [dimension for dimension in _STANDARD_REVIEW_DIMENSIONS if dimension in selected]


def plan_review_audit_slots(
    *,
    gate_state: str,
    metrics_marker: object,
    annotated_diff: str,
    valid_diff_lines: object,
    deletion_merge_base: str,
    audit_run_id: str,
) -> dict[str, object]:
    """Derive the required auditor slots from retained gate evidence."""
    dimensions = _standard_dimensions(metrics_marker)
    experimental = select_experimental_review_dispatch(
        gate_state=gate_state,
        annotated_diff=annotated_diff,
        valid_diff_lines=valid_diff_lines,
        standard_agent_names=list(_STANDARD_REVIEW_DIMENSIONS),
    )
    definitions: list[tuple[str, str, str]] = [
        (dimension, "standard", dimension) for dimension in dimensions
    ]
    if deletion_regression_is_eligible({"merge_base": deletion_merge_base}):
        definitions.append(("deletion_regression", "deletion", "deletion_regression"))
    dispatch = experimental.get("dispatch_agents", [])
    if isinstance(dispatch, list):
        definitions.extend(
            (str(item["dimension"]), "experimental", str(item["agent_name"]))
            for item in dispatch
            if isinstance(item, Mapping)
            and isinstance(item.get("dimension"), str)
            and isinstance(item.get("agent_name"), str)
        )
    slots = [
        {
            "slot_id": slot_id,
            "kind": kind,
            "dimension": dimension,
            "producer": producer,
            "slot_token": f"rva{audit_run_id}{ordinal:02d}",
            "marker_line": f"{REVIEW_AUDIT_SLOT_FIELD}: rva{audit_run_id}{ordinal:02d}",
        }
        for ordinal, (slot_id, kind, producer) in enumerate(definitions)
        for dimension in (slot_id,)
    ]
    return {
        "slots": slots,
        "experimental_audit_state": experimental["audit_state"],
        "experimental_reason": experimental["reason"],
    }


def _validate_anchor_identity(
    authority: DiffAnchorAuthority, repository: str, pr_number: int, head_sha: str
) -> DiffAnchorAuthority:
    if (
        authority.repository.casefold() != repository.casefold()
        or authority.pr_number != pr_number
        or authority.head_sha != head_sha
    ):
        raise ReviewAuditInputError("anchor authority identity does not match review manifest")
    return authority


def _read_gate_authority(
    authority_path: str, review_output_dir: str
) -> tuple[Path, Mapping[str, object]]:
    output = Path(review_output_dir)
    authority_file = Path(authority_path)
    if not output.is_absolute() or not output.is_dir():
        raise ReviewAuditInputError("review_output_dir must be an existing absolute directory")
    output = output.resolve()
    if not authority_file.is_absolute() or not authority_file.resolve().is_relative_to(output):
        raise ReviewAuditInputError("authority_path must resolve inside review_output_dir")
    authority_file = authority_file.resolve()
    try:
        authority = _read_json(authority_file)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReviewAuditInputError("gate authority is unreadable JSON") from exc
    if not isinstance(authority, Mapping):
        raise ReviewAuditInputError("gate authority must be a JSON object")
    return authority_file, authority


def _validate_gate_state(authority: Mapping[str, object]) -> str:
    gate_state = authority.get("state")
    if not isinstance(gate_state, str) or gate_state not in {
        "valid_true",
        "valid_false",
        "degraded",
    }:
        raise ReviewAuditInputError("gate authority has an invalid state")
    required_text_fields = (
        "reason_code",
        "experimental_audit_state",
        "annotation_generation_id",
        "authority_path",
        "snapshot_dir",
        "hunk_ranges_snapshot_path",
        "mode",
        "diff_metrics_path",
        "annotated_diff_path",
        "hunk_ranges_path",
        "valid_lines_path",
    )
    if any(not isinstance(authority.get(field), str) for field in required_text_fields):
        raise ReviewAuditInputError("gate authority is missing required string fields")
    return gate_state


def _validate_gate_identity(
    authority: Mapping[str, object],
) -> tuple[Path, int, Mapping[str, object]]:
    root_value = authority.get("checkout_root")
    if (
        not isinstance(root_value, str)
        or not Path(root_value).is_absolute()
        or not Path(root_value).is_dir()
    ):
        raise ReviewAuditInputError("gate authority checkout_root must be an absolute directory")
    pr_value = authority.get("pr_number")
    if not isinstance(pr_value, str) or re.fullmatch(r"[1-9][0-9]*", pr_value) is None:
        raise ReviewAuditInputError("gate authority pr_number must be a positive integer string")
    snapshot = authority.get("snapshot")
    if not isinstance(snapshot, Mapping):
        raise ReviewAuditInputError("gate authority snapshot must be an object")
    if any(
        not isinstance(snapshot.get(field), str)
        for field in (
            "head_sha",
            "base_sha",
            "merge_base_sha",
            "base_repo_full_name",
            "diff_sha256",
            "profile_id",
        )
    ):
        raise ReviewAuditInputError("gate authority snapshot is missing required string fields")
    return Path(root_value).resolve(), int(pr_value), snapshot


def _retained_snapshot_paths(authority: Mapping[str, object]) -> tuple[Path, Path, Path]:
    names = (
        "metrics_marker_snapshot_path",
        "annotated_diff_snapshot_path",
        "valid_lines_snapshot_path",
    )
    values = [authority.get(name) for name in names]
    if any(not isinstance(value, str) for value in values):
        raise ReviewAuditInputError("gate authority snapshot paths must be strings")
    return Path(str(values[0])), Path(str(values[1])), Path(str(values[2]))


def _load_retained_snapshots(
    paths: tuple[Path, Path, Path], gate_state: str
) -> tuple[object, str, object]:
    metrics_path, diff_path, lines_path = paths
    if gate_state != "degraded" and not all(
        path.is_file() for path in (metrics_path, diff_path, lines_path)
    ):
        raise ReviewAuditInputError("non-degraded gate authority has a missing snapshot file")
    metrics_marker = _read_json(metrics_path, tolerate_error=True)
    valid_diff_lines = _read_json(lines_path, tolerate_error=True)
    try:
        annotated = diff_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        annotated = ""
    annotated_diff = annotated.split("\n", 1)[1] if "\n" in annotated else ""
    return metrics_marker, annotated_diff, valid_diff_lines


def _bind_anchor_identity(
    anchor_authority_path: str,
    repository: str,
    pr_number: int,
    snapshot: Mapping[str, object],
) -> tuple[str, str, str]:
    if anchor_authority_path:
        anchor_path = Path(anchor_authority_path)
        if not anchor_path.is_absolute() or not anchor_path.is_file():
            raise ReviewAuditInputError("anchor_authority_path must name an absolute file")
        try:
            anchor = DiffAnchorAuthority.from_wire(_read_json(anchor_path))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ReviewAuditInputError("anchor authority is invalid") from exc
        manifest_repository = (repository or anchor.repository).casefold()
        if not _REPOSITORY_RE.fullmatch(manifest_repository):
            raise ReviewAuditInputError("repository must be owner/repository")
        _validate_anchor_identity(
            anchor, manifest_repository, pr_number, str(snapshot.get("head_sha", ""))
        )
        return manifest_repository, str(anchor_path.resolve()), _sha256_file(anchor_path)

    if not _REPOSITORY_RE.fullmatch(repository):
        raise ReviewAuditInputError(
            "repository must be owner/repository when anchors are unavailable"
        )
    head_sha = snapshot.get("head_sha")
    if not isinstance(head_sha, str) or not _HEAD_SHA_RE.fullmatch(head_sha):
        raise ReviewAuditInputError(
            "gate authority snapshot head_sha must be a full lowercase SHA"
        )
    return repository.casefold(), "", ""


def _write_review_audit_manifest(output: Path, body: dict[str, object]) -> Path:
    manifest = {**body, "content_sha256": _content_digest(body)}
    manifest_path = output / f"review_audit_manifest_{body['audit_run_id']}.json"
    atomic_write(
        manifest_path,
        json.dumps(manifest, sort_keys=True, indent=2) + "\n",
        exclusive=True,
        strict_durability=True,
    )
    return manifest_path


def plan_review_audit(
    *,
    authority_path: str,
    review_output_dir: str,
    deletion_merge_base: str = "",
    anchor_authority_path: str = "",
    repository: str = "",
) -> dict[str, object]:
    """Snapshot retained gate authority and derive one immutable audit manifest."""
    authority_file, authority = _read_gate_authority(authority_path, review_output_dir)
    output = Path(review_output_dir).resolve()
    gate_state = _validate_gate_state(authority)
    review_root, pr_number, snapshot = _validate_gate_identity(authority)
    evidence = _load_retained_snapshots(_retained_snapshot_paths(authority), gate_state)
    metrics_marker, annotated_diff, valid_diff_lines = evidence
    manifest_repository, anchor_path_value, anchor_digest = _bind_anchor_identity(
        anchor_authority_path,
        repository,
        pr_number,
        snapshot,
    )

    audit_run_id = secrets.token_hex(8)
    planned = plan_review_audit_slots(
        gate_state=gate_state,
        metrics_marker=metrics_marker,
        annotated_diff=annotated_diff,
        valid_diff_lines=valid_diff_lines,
        deletion_merge_base=deletion_merge_base,
        audit_run_id=audit_run_id,
    )
    body: dict[str, object] = {
        "schema_version": REVIEW_AUDIT_MANIFEST_SCHEMA_VERSION,
        "audit_run_id": audit_run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "authority_path": str(authority_file),
        "authority_sha256": _sha256_file(authority_file),
        "gate_state": gate_state,
        "experimental_audit_state": planned["experimental_audit_state"],
        "experimental_reason": planned["experimental_reason"],
        "snapshot": dict(snapshot),
        "review_root": str(review_root),
        "pr_number": pr_number,
        "mode": authority.get("mode", ""),
        "anchor_authority_path": anchor_path_value,
        "anchor_authority_sha256": anchor_digest,
        "repository": manifest_repository,
        "slots": planned["slots"],
        "max_attempts": REVIEW_AUDIT_MAX_ATTEMPTS,
    }
    manifest_path = _write_review_audit_manifest(output, body)
    return {
        "manifest_path": str(manifest_path),
        "audit_run_id": audit_run_id,
        "gate_state": gate_state,
        "experimental_audit_state": planned["experimental_audit_state"],
        "slots": planned["slots"],
        "max_attempts": REVIEW_AUDIT_MAX_ATTEMPTS,
    }


def load_review_audit_manifest(manifest_path: str) -> dict[str, object]:
    """Load a manifest only while each retained authority file is unchanged."""
    try:
        value = _read_json(Path(manifest_path))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReviewAuditInputError("review-audit manifest is unreadable") from exc
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != REVIEW_AUDIT_MANIFEST_SCHEMA_VERSION
    ):
        raise ReviewAuditInputError("review-audit manifest schema is invalid")
    if value.get("content_sha256") != _content_digest(value):
        raise ReviewAuditInputError("review-audit manifest digest does not match")
    for path_key, digest_key in (
        ("authority_path", "authority_sha256"),
        ("anchor_authority_path", "anchor_authority_sha256"),
    ):
        path_value = value.get(path_key)
        digest_value = value.get(digest_key)
        if not isinstance(path_value, str) or not isinstance(digest_value, str):
            raise ReviewAuditInputError(f"manifest {path_key} fields are invalid")
        if not path_value:
            if digest_value:
                raise ReviewAuditInputError(f"manifest {digest_key} has no matching path")
            continue
        try:
            actual = _sha256_file(Path(path_value))
        except OSError as exc:
            raise ReviewAuditInputError(
                f"manifest authority file is unreadable: {path_key}"
            ) from exc
        if actual != digest_value:
            raise ReviewAuditInputError(f"manifest authority digest changed: {path_key}")
    return value


def load_review_audit_anchor_authority(manifest: Mapping[str, object]) -> DiffAnchorAuthority:
    """Load an anchor artifact and bind its repository, PR, and head identity."""
    path = manifest.get("anchor_authority_path")
    try:
        if isinstance(path, str) and path:
            authority = DiffAnchorAuthority.from_wire(_read_json(Path(path)))
        else:
            snapshot = manifest.get("snapshot")
            if not isinstance(snapshot, Mapping):
                raise ReviewAuditInputError("manifest snapshot is invalid")
            authority = DiffAnchorAuthority.unavailable(
                repository=str(manifest["repository"]),
                pr_number=int(str(manifest["pr_number"])),
                head_sha=str(snapshot["head_sha"]),
            )
        snapshot = manifest.get("snapshot")
        if not isinstance(snapshot, Mapping) or (
            authority.repository.casefold() != str(manifest.get("repository", "")).casefold()
            or authority.pr_number != manifest.get("pr_number")
            or authority.head_sha != snapshot.get("head_sha")
        ):
            raise ReviewAuditInputError("anchor authority identity does not match manifest")
        return authority
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ) as exc:
        if isinstance(exc, ReviewAuditInputError):
            raise
        raise ReviewAuditInputError("anchor authority does not match manifest identity") from exc
