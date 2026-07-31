"""Pure validation and deterministic identity for review operations."""

from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath
from typing import Any

import regex as re

from autoskillit.core import GitHubReviewComment, GitHubReviewRequest, normalize_owner_repo

_SHA_RE = re.compile(r"[0-9a-f]{40}")
_EVENTS = frozenset({"APPROVE", "COMMENT", "REQUEST_CHANGES"})
_SIDES = frozenset({"LEFT", "RIGHT"})


def _normalize_text(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def _comment_wire(comment: GitHubReviewComment) -> dict[str, Any]:
    path = PurePosixPath(comment.path)
    if (
        not comment.path
        or path.is_absolute()
        or "\\" in comment.path
        or ".." in path.parts
        or "." in path.parts
    ):
        raise ValueError("review comment path must be repository-relative POSIX")
    if isinstance(comment.line, bool) or comment.line <= 0:
        raise ValueError("review comment line must be positive")
    if comment.side not in _SIDES:
        raise ValueError("review comment side must be LEFT or RIGHT")
    if (comment.start_line is None) != (comment.start_side is None):
        raise ValueError("start_line and start_side must be supplied together")
    if comment.start_line is not None:
        if (
            isinstance(comment.start_line, bool)
            or comment.start_line <= 0
            or comment.start_line > comment.line
        ):
            raise ValueError("review comment start_line is outside the valid range")
        if comment.start_side not in _SIDES:
            raise ValueError("review comment start_side must be LEFT or RIGHT")
    body = _normalize_text(comment.body)
    if not body:
        raise ValueError("review comment body must not be empty")
    return {
        "body": body,
        "line": comment.line,
        "path": comment.path,
        "side": comment.side,
        "start_line": comment.start_line,
        "start_side": comment.start_side,
    }


def canonical_comment_records(
    request: GitHubReviewRequest,
) -> tuple[tuple[int, dict[str, Any]], ...]:
    records = tuple(
        (index, _comment_wire(comment)) for index, comment in enumerate(request.comments)
    )
    fingerprints: set[bytes] = set()
    for _, record in records:
        fingerprint = json.dumps(
            record, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
        if fingerprint in fingerprints:
            raise ValueError("duplicate normalized review finding")
        fingerprints.add(fingerprint)
    return tuple(
        sorted(
            records,
            key=lambda item: (
                item[1]["path"],
                item[1]["line"],
                item[1]["start_line"] or 0,
                item[1]["side"],
                item[1]["body"],
            ),
        )
    )


def _validated_identity_wire(request: GitHubReviewRequest) -> dict[str, Any]:
    repository = normalize_owner_repo(request.repository)
    if isinstance(request.pr_number, bool) or request.pr_number <= 0:
        raise ValueError("pr_number must be positive")
    if not _SHA_RE.fullmatch(request.head_sha):
        raise ValueError("head_sha must be a full lowercase hexadecimal commit SHA")
    if (
        not isinstance(request.logical_iteration, str)
        or ":" not in request.logical_iteration
        or any(not part for part in request.logical_iteration.split(":", 1))
    ):
        raise ValueError("logical_iteration must be a namespaced string")
    return {
        "comments": [record for _, record in canonical_comment_records(request)],
        "head_sha": request.head_sha,
        "logical_iteration": request.logical_iteration,
        "pr_number": request.pr_number,
        "repository": repository,
        "schema_version": 1,
    }


def _canonical_wire(request: GitHubReviewRequest) -> dict[str, Any]:
    identity = _validated_identity_wire(request)
    if request.event not in _EVENTS:
        raise ValueError("event must be APPROVE, COMMENT, or REQUEST_CHANGES")
    body = _normalize_text(request.body)
    if request.event != "APPROVE" and not body:
        raise ValueError("review body must not be empty")
    return {
        **identity,
        "body": body,
        "event": request.event,
    }


def canonicalize_review_request(request: GitHubReviewRequest) -> bytes:
    return json.dumps(
        _canonical_wire(request),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def compute_review_operation_key(request: GitHubReviewRequest) -> str:
    canonical = json.dumps(
        _validated_identity_wire(request),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(b"autoskillit:github-review-operation:v1\0" + canonical).hexdigest()
