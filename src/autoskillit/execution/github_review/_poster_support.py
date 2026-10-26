"""Value objects and pure helpers for the review publication state machine."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from autoskillit.core import (
    GitHubReviewFindingDisposition,
    GitHubReviewPostResult,
    GitHubReviewReceipt,
    GitHubReviewRequest,
    ReviewFindingDispositionKind,
    ReviewOperationState,
    ReviewReconciliationResult,
    ReviewResponseClass,
)

from .canonical import AdmittedFinding, CanonicalFinding
from .gateway import GatewayResult

OPERATION_MARKER = "<!-- autoskillit-review-operation:{key} -->"
FINDING_MARKER = "<!-- autoskillit-review-finding:{digest} -->"


@dataclass(frozen=True, slots=True)
class Reconciliation:
    result: ReviewReconciliationResult
    review_id: int | None = None
    comment_ids: tuple[tuple[int, int], ...] = ()
    error: str | None = None


@dataclass(frozen=True, slots=True)
class RemoteFindingScan:
    certain: bool
    ids_by_digest: Mapping[str, int]
    error: str | None = None


_AUTHOR_EVENT_DISCLOSURES = {
    "APPROVE": (
        "Requested review event APPROVE was published as COMMENT because GitHub does not allow "
        "a pull request's author to approve it; this is not a GitHub approval."
    ),
    "REQUEST_CHANGES": (
        "Requested review event REQUEST_CHANGES was published as COMMENT because GitHub does "
        "not allow a pull request's author to request changes on it; this review does not "
        "block merging."
    ),
}


def author_effective_event(
    requested_event: str, *, authenticated_login: str, pr_author_login: str
) -> str:
    """Choose the event for GitHub's known own-author restriction."""
    if (
        requested_event in _AUTHOR_EVENT_DISCLOSURES
        and authenticated_login.casefold() == pr_author_login.casefold()
    ):
        return "COMMENT"
    return requested_event


def _disclose_author_event(body: str, *, requested_event: str, event: str) -> str:
    if event == requested_event:
        return body
    if event != "COMMENT" or requested_event not in _AUTHOR_EVENT_DISCLOSURES:
        raise ValueError(
            f"unsupported review event transformation: {requested_event!r} -> {event!r}"
        )
    return append_marker(body, _AUTHOR_EVENT_DISCLOSURES[requested_event])


def payload(
    *,
    request: GitHubReviewRequest,
    operation_key: str,
    findings: tuple[AdmittedFinding, ...],
    omitted: tuple[GitHubReviewFindingDisposition, ...],
    event: str,
) -> dict[str, Any]:
    comments: list[dict[str, Any]] = []
    for finding in findings:
        if not isinstance(finding, AdmittedFinding):
            raise TypeError("review payload requires admitted findings")
        item = {
            key: value
            for key, value in finding.wire.items()
            if value is not None and key != "start_side"
        }
        if finding.wire["start_line"] is not None:
            item["start_side"] = finding.wire["start_side"]
        item["body"] = append_marker(
            str(finding.wire["body"]),
            FINDING_MARKER.format(digest=finding.digest),
        )
        comments.append(item)
    body = normalize_text(request.body)
    entries = []
    for disposition in omitted:
        if disposition.kind is ReviewFindingDispositionKind.OMITTED_INVALID:
            if not 0 <= disposition.original_index < len(request.comments):
                raise ValueError(
                    f"omitted disposition original_index {disposition.original_index} "
                    f"out of range for request with {len(request.comments)} comments"
                )
            comment = request.comments[disposition.original_index]
            entries.append(
                f"- `{comment.path}:{comment.line}` ({comment.side}): "
                f"{normalize_text(comment.body)}\n  Reason: {disposition.reason}"
            )
    if entries:
        body += "\n\n## Outside Diff Range\n\n" + "\n\n".join(entries)
    body = _disclose_author_event(body, requested_event=request.event, event=event)
    if len(body) > 60000:
        raise ValueError("review body including omitted findings exceeds 60000 characters")
    return {
        "body": append_marker(
            body,
            OPERATION_MARKER.format(key=operation_key),
        ),
        "commit_id": request.head_sha,
        "event": event,
        "comments": comments,
    }


def remote_comment_matches(
    remote: Mapping[str, Any],
    expected: Mapping[str, Any],
    finding_digest: str,
    *,
    reviewed_head_sha: str,
) -> bool:
    if remote.get("body") != expected.get("body"):
        return False
    if finding_marker_digest(str(remote.get("body", ""))) != finding_digest:
        return False
    if (
        remote.get("path") != expected["path"]
        or remote.get("original_commit_id") != reviewed_head_sha
        or positive_int(remote.get("original_line")) != expected["line"]
        or "original_start_line" not in remote
        or remote.get("side") != expected["side"]
        or remote.get("start_side") != expected.get("start_side")
    ):
        return False
    if expected.get("start_line") is None:
        return remote["original_start_line"] is None
    return positive_int(remote["original_start_line"]) == expected["start_line"]


def comments_for_review_ids(
    comments: list[Any], review_ids: set[int]
) -> list[Mapping[str, Any]] | None:
    selected: list[Mapping[str, Any]] = []
    prefix = FINDING_MARKER.split("{digest}", 1)[0]
    for comment in comments:
        if not isinstance(comment, Mapping):
            continue
        review_id = positive_int(comment.get("pull_request_review_id"))
        if review_id in review_ids:
            selected.append(comment)
        elif review_id is None and prefix in str(comment.get("body", "")):
            return None
    return selected


def finding_marker_digest(body: str) -> str | None:
    prefix, suffix = FINDING_MARKER.split("{digest}", 1)
    start = body.find(prefix)
    if start < 0:
        return None
    start += len(prefix)
    end = body.find(suffix, start)
    if end < 0:
        return None
    digest = body[start:end]
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        return None
    return digest


def structured_invalid_comment_index(
    response: GatewayResult,
    comment_count: int,
) -> int | None:
    if response.status_code != 422:
        return None
    data = response.data if isinstance(response.data, Mapping) else {}
    errors = data.get("errors")
    if not isinstance(errors, list):
        return None
    anchor_errors = [
        item
        for item in errors
        if isinstance(item, Mapping) and str(item.get("field", "")).startswith("comments[")
    ]
    if len(anchor_errors) != 1:
        return None
    item = anchor_errors[0]
    if not isinstance(item, Mapping) or item.get("code") != "invalid":
        return None
    message = str(item.get("message", "")).casefold()
    if not any(term in message for term in ("diff", "line", "anchor")):
        return None
    field = str(item.get("field", ""))
    if not field.startswith("comments["):
        return None
    index_text, separator, suffix = field.removeprefix("comments[").partition("]")
    if separator != "]" or (suffix and not (suffix.startswith(".") and suffix[1:])):
        return None
    try:
        index = int(index_text)
    except ValueError:
        return None
    return index if 0 <= index < comment_count else None


def structured_error_message(response: GatewayResult) -> str | None:
    if not isinstance(response.data, Mapping):
        return None
    errors = response.data.get("errors")
    if not isinstance(errors, list) or len(errors) != 1 or not isinstance(errors[0], Mapping):
        return None
    value = errors[0].get("message")
    return value if isinstance(value, str) and value else None


def nonfinal_result(
    request: GitHubReviewRequest,
    state: ReviewOperationState,
    response_class: ReviewResponseClass,
    error: str,
    *,
    operation_key: str = "",
    reconciliation: ReviewReconciliationResult = ReviewReconciliationResult.NOT_NEEDED,
    executed_mutations: int = 0,
) -> GitHubReviewPostResult:
    return GitHubReviewPostResult(
        operation_key=operation_key,
        head_sha=request.head_sha,
        state=state,
        response_class=response_class,
        reconciliation_result=reconciliation,
        planned_mutation_count=1,
        planned_comment_count=len(request.comments),
        executed_mutation_count=executed_mutations,
        error=error,
    )


def result_from_receipt(
    receipt: GitHubReviewReceipt,
    *,
    replayed: bool,
) -> GitHubReviewPostResult:
    posted = sum(
        item.kind is ReviewFindingDispositionKind.POSTED for item in receipt.finding_dispositions
    )
    return GitHubReviewPostResult(
        operation_key=receipt.operation_key,
        head_sha=receipt.head_sha,
        state=receipt.state,
        response_class=receipt.response_class,
        reconciliation_result=receipt.reconciliation_result,
        review_id=receipt.review_id,
        comment_ids=receipt.comment_ids,
        planned_comment_count=receipt.canonical_finding_count,
        executed_mutation_count=0,
        executed_comment_count=posted,
        receipt=receipt,
        replayed=replayed,
    )


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()


def normalize_text(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def text_digest(value: str) -> str:
    return hashlib.sha256(normalize_text(value).encode()).hexdigest()


def effective_body_digest(
    request: GitHubReviewRequest,
    operation_key: str,
    findings: tuple[AdmittedFinding, ...],
    event: str,
    omitted: tuple[GitHubReviewFindingDisposition, ...],
) -> str:
    """Digest the exact top-level body sent to GitHub for one attempt."""
    return text_digest(
        str(
            payload(
                request=request,
                operation_key=operation_key,
                findings=findings,
                omitted=omitted,
                event=event,
            )["body"]
        )
    )


def finding_set_digest(findings: tuple[CanonicalFinding, ...]) -> str:
    return hashlib.sha256(canonical_json([dict(finding.wire) for finding in findings])).hexdigest()


def append_marker(body: str, marker: str) -> str:
    return f"{body}\n\n{marker}" if body else marker


def positive_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def nested_string(data: Any, outer: str, inner: str) -> str | None:
    if not isinstance(data, Mapping):
        return None
    nested = data.get(outer)
    if not isinstance(nested, Mapping):
        return None
    value = nested.get(inner)
    return value if isinstance(value, str) and value else None


def login(data: Any) -> str | None:
    if not isinstance(data, Mapping):
        return None
    value = data.get("login")
    return value if isinstance(value, str) and value else None
