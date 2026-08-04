"""Contracts for authoritative GitHub pull-request review publication."""

from __future__ import annotations

import ast
import dataclasses
import inspect
import re
import sys
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from typing import get_type_hints

import pytest

from autoskillit.core import (
    GitHubReviewComment,
    GitHubReviewFindingDisposition,
    GitHubReviewPosterProtocol,
    GitHubReviewPostResult,
    GitHubReviewReceipt,
    GitHubReviewRequest,
    ReviewFindingDispositionKind,
    ReviewOperationState,
    ReviewReconciliationResult,
    ReviewResponseClass,
    is_final_github_review_state,
    is_valid_github_review_head_sha,
    is_valid_github_review_logical_iteration,
    is_valid_github_review_operation_key,
    is_valid_github_review_repository,
    review_receipt_validation_error,
)
from autoskillit.core.types import _type_github_review

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


_CONTRACT_TYPES = (
    GitHubReviewComment,
    GitHubReviewRequest,
    GitHubReviewPostResult,
    GitHubReviewReceipt,
    GitHubReviewFindingDisposition,
)

_PUBLIC_NAMES = {
    "GitHubReviewComment",
    "GitHubReviewRequest",
    "GitHubReviewPostResult",
    "GitHubReviewReceipt",
    "GitHubReviewFindingDisposition",
    "ReviewOperationState",
    "ReviewResponseClass",
    "ReviewReconciliationResult",
    "ReviewFindingDispositionKind",
    "is_final_github_review_state",
    "is_valid_github_review_head_sha",
    "is_valid_github_review_logical_iteration",
    "is_valid_github_review_operation_key",
    "is_valid_github_review_repository",
    "review_receipt_validation_error",
}


def _comment(body: str = "Use the normalized value.") -> GitHubReviewComment:
    return GitHubReviewComment(path="src/example.py", line=17, body=body)


def _request(**overrides: object) -> GitHubReviewRequest:
    values: dict[str, object] = {
        "repository": "octo/example",
        "pr_number": 42,
        "head_sha": "a" * 40,
        "logical_iteration": "review-pr:3",
        "event": "COMMENT",
        "body": "Automated review",
        "comments": (_comment(),),
    }
    values.update(overrides)
    return GitHubReviewRequest(**values)


@pytest.mark.parametrize(
    ("validator", "valid", "invalid"),
    [
        (is_valid_github_review_head_sha, "a" * 40, "A" * 40),
        (is_valid_github_review_operation_key, "f" * 64, "review-v1:approved"),
        (is_valid_github_review_repository, "octo/example", "Octo/example"),
        (is_valid_github_review_logical_iteration, "review-pr:2", "review_pr:2"),
    ],
)
def test_review_identity_validators_share_canonical_wire_rules(
    validator: Callable[[object], bool],
    valid: str,
    invalid: str,
) -> None:
    assert validator(valid) is True
    assert validator(invalid) is False
    assert validator(None) is False


def test_receipt_effect_validation_owns_final_state_and_finding_partition() -> None:
    payload: dict[str, object] = {
        "schema_version": 1,
        "operation_key": "f" * 64,
        "repository": "octo/example",
        "pr_number": 42,
        "head_sha": "a" * 40,
        "logical_iteration": "review-pr:3",
        "state": "SUCCEEDED",
        "review_id": 700,
        "comment_ids": [900],
        "canonical_finding_count": 1,
        "finding_dispositions": [
            {"original_index": 0, "kind": "POSTED", "remote_comment_id": 900}
        ],
        "reconciliation_result": "NOT_NEEDED",
    }

    def validate() -> str | None:
        return review_receipt_validation_error(
            payload,
            operation_key="f" * 64,
            repository="octo/example",
            pr_number=42,
            head_sha="a" * 40,
            logical_iteration="review-pr:3",
            post_state="SUCCEEDED",
        )

    assert is_final_github_review_state("SUCCEEDED") is True
    assert validate() is None

    payload["finding_dispositions"] = []
    assert validate() == "incomplete_finding_accounting"


def test_contract_module_exports_exact_public_surface() -> None:
    assert set(_type_github_review.__all__) == _PUBLIC_NAMES
    for name in _PUBLIC_NAMES:
        assert getattr(_type_github_review, name) is getattr(sys.modules["autoskillit.core"], name)


def test_contract_module_has_only_stdlib_imports() -> None:
    """The IL-0 wire contracts remain usable without importing another layer."""
    source_path = Path(inspect.getsourcefile(_type_github_review) or "")
    tree = ast.parse(source_path.read_text())
    non_stdlib: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots = {alias.name.partition(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                non_stdlib.add(f"relative:{node.module or ''}")
                continue
            roots = {(node.module or "").partition(".")[0]}
        else:
            continue
        non_stdlib.update(root for root in roots if root not in sys.stdlib_module_names)
    assert not non_stdlib, f"non-stdlib imports in GitHub review contracts: {non_stdlib}"


@pytest.mark.parametrize("contract_type", _CONTRACT_TYPES)
def test_contract_records_are_frozen_slotted_dataclasses(contract_type: type[object]) -> None:
    assert dataclasses.is_dataclass(contract_type)
    assert contract_type.__dataclass_params__.frozen is True
    assert "__slots__" in contract_type.__dict__


def test_comment_defaults_and_range_fields_are_immutable() -> None:
    comment = GitHubReviewComment(
        path="src/example.py",
        line=17,
        body="Comment",
        start_line=14,
        start_side="RIGHT",
    )
    assert dataclasses.asdict(comment) == {
        "path": "src/example.py",
        "line": 17,
        "body": "Comment",
        "side": "RIGHT",
        "start_line": 14,
        "start_side": "RIGHT",
    }
    with pytest.raises(dataclasses.FrozenInstanceError):
        comment.line = 18  # type: ignore[misc]


@pytest.mark.parametrize("field", ["path", "body", "side", "start_side"])
def test_comment_from_wire_rejects_non_string_scalars(field: str) -> None:
    payload: dict[str, object] = {
        "path": "src/example.py",
        "line": 17,
        "body": "Comment",
        "side": "RIGHT",
        "start_line": 14,
        "start_side": "RIGHT",
    }
    payload[field] = ["not", "a", "string"]

    with pytest.raises(TypeError, match=rf"{field} must be a string"):
        GitHubReviewComment.from_wire(payload)


def test_request_uses_tuple_comments_and_preserves_explicit_identity(tmp_path: Path) -> None:
    receipt_path = tmp_path / "receipt.json"
    request = _request(receipt_path=receipt_path, dry_run=True)
    assert request.repository == "octo/example"
    assert request.pr_number == 42
    assert request.head_sha == "a" * 40
    assert request.logical_iteration == "review-pr:3"
    assert request.event == "COMMENT"
    assert request.comments == (_comment(),)
    assert request.receipt_path == receipt_path
    assert request.dry_run is True
    with pytest.raises(dataclasses.FrozenInstanceError):
        request.event = "APPROVE"  # type: ignore[misc]


def test_operation_state_vocabulary_is_closed() -> None:
    assert issubclass(ReviewOperationState, StrEnum)
    assert {member.name for member in ReviewOperationState} == {
        "DRY_RUN",
        "PREPARED",
        "RETRY_PENDING",
        "POSTING",
        "COMMITTED_PENDING_VERIFICATION",
        "SUCCEEDED",
        "RECONCILED",
        "AMBIGUOUS",
        "THROTTLED",
        "TERMINAL",
    }
    assert all(member.value == member.name for member in ReviewOperationState)


@pytest.mark.parametrize(
    "enum_type",
    (
        ReviewResponseClass,
        ReviewReconciliationResult,
        ReviewFindingDispositionKind,
    ),
)
def test_classification_vocabularies_are_nonempty_unique_str_enums(
    enum_type: type[StrEnum],
) -> None:
    assert issubclass(enum_type, StrEnum)
    values = [member.value for member in enum_type]
    assert values
    assert len(values) == len(set(values))
    assert all(isinstance(value, str) and value for value in values)


def test_result_receipt_and_disposition_expose_identity_fields() -> None:
    expected_fields = {
        GitHubReviewPostResult: {
            "operation_key",
            "head_sha",
            "state",
            "response_class",
            "reconciliation_result",
            "review_id",
            "comment_ids",
            "planned_mutation_count",
            "planned_comment_count",
            "executed_mutation_count",
            "executed_comment_count",
            "receipt_path",
            "receipt",
            "replayed",
            "error",
        },
        GitHubReviewReceipt: {
            "schema_version",
            "operation_key",
            "repository",
            "pr_number",
            "head_sha",
            "logical_iteration",
            "requested_event",
            "effective_event",
            "requested_body_digest",
            "effective_body_digest",
            "canonical_finding_digest",
            "state",
            "response_class",
            "review_id",
            "comment_ids",
            "canonical_finding_count",
            "reconciliation_result",
            "finding_dispositions",
            "created_at",
            "updated_at",
        },
        GitHubReviewFindingDisposition: {
            "original_index",
            "kind",
            "remote_comment_id",
            "reason",
        },
    }
    for contract_type, required in expected_fields.items():
        actual = {field.name for field in dataclasses.fields(contract_type)}
        assert required <= actual, f"{contract_type.__name__} missing {required - actual}"
    assert callable(GitHubReviewPostResult.to_dict)


def test_poster_protocol_is_runtime_checkable_and_has_exact_async_contract() -> None:
    class _Poster:
        async def post(self, request: GitHubReviewRequest) -> GitHubReviewPostResult:
            raise NotImplementedError

    assert isinstance(_Poster(), GitHubReviewPosterProtocol)
    assert inspect.iscoroutinefunction(GitHubReviewPosterProtocol.post)
    signature = inspect.signature(GitHubReviewPosterProtocol.post)
    assert tuple(signature.parameters) == ("self", "request")
    assert get_type_hints(GitHubReviewPosterProtocol.post) == {
        "request": GitHubReviewRequest,
        "return": GitHubReviewPostResult,
    }


def test_operation_keys_are_documented_as_lowercase_sha256() -> None:
    """The public result and receipt key annotations stay scalar and serializable."""
    for contract_type in (GitHubReviewPostResult, GitHubReviewReceipt):
        annotation = get_type_hints(contract_type)["operation_key"]
        assert annotation is str
    assert re.fullmatch(r"[0-9a-f]{64}", "0" * 64)
