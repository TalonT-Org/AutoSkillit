"""Cross-recipe contracts for review publication identity and receipts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import pytest

from autoskillit.core.io import load_yaml
from autoskillit.recipe.io import builtin_recipes_dir

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


@dataclass(frozen=True)
class ReviewConsumer:
    recipe_name: str
    work_dir_ref: str
    pr_ref: str
    review_step: str
    failure_route: str
    logical_iteration: str
    logical_iteration_pattern: str = ""


_CONSUMERS = (
    ReviewConsumer(
        recipe_name="implementation",
        work_dir_ref="${{ context.work_dir }}",
        pr_ref="${{ context.pr_number }}",
        review_step="review_pr",
        failure_route="release_issue_failure",
        logical_iteration="review-pr:${{ context.review_loop_count }}",
    ),
    ReviewConsumer(
        recipe_name="implementation-groups",
        work_dir_ref="${{ context.work_dir }}",
        pr_ref="${{ context.pr_number }}",
        review_step="review_pr",
        failure_route="release_issue_failure",
        logical_iteration="review-pr:${{ context.review_loop_count }}",
    ),
    ReviewConsumer(
        recipe_name="merge-prs",
        work_dir_ref="${{ context.work_dir }}",
        pr_ref="${{ context.review_pr_number }}",
        review_step="review_pr_integration",
        failure_route="register_clone_failure",
        logical_iteration="",
        logical_iteration_pattern=r"merge-prs:[a-z0-9][a-z0-9-]*",
    ),
    ReviewConsumer(
        recipe_name="remediation",
        work_dir_ref="${{ context.work_dir }}",
        pr_ref="${{ context.pr_number }}",
        review_step="review_pr",
        failure_route="release_issue_failure",
        logical_iteration="review-pr:${{ context.review_loop_count }}",
    ),
    ReviewConsumer(
        recipe_name="research",
        work_dir_ref="${{ context.worktree_path }}",
        pr_ref="${{ context.pr_number }}",
        review_step="review_research_pr",
        failure_route="escalate_stop",
        logical_iteration="",
        logical_iteration_pattern=r"review-research-pr:[a-z0-9][a-z0-9-]*",
    ),
    ReviewConsumer(
        recipe_name="research-review",
        work_dir_ref="${{ inputs.worktree_path }}",
        pr_ref="${{ context.pr_number }}",
        review_step="review_research_pr",
        failure_route="escalate_stop",
        logical_iteration="",
        logical_iteration_pattern=r"review-research-pr:[a-z0-9][a-z0-9-]*",
    ),
)


def _load_steps(consumer: ReviewConsumer) -> dict[str, dict[str, Any]]:
    path = builtin_recipes_dir() / f"{consumer.recipe_name}.yaml"
    data = load_yaml(path)
    return data["steps"]


def _find_capture_producer(
    steps: dict[str, dict[str, Any]],
    capture_name: str,
) -> tuple[str, dict[str, Any]]:
    producers = [
        (name, step) for name, step in steps.items() if capture_name in (step.get("capture") or {})
    ]
    assert len(producers) == 1, (
        f"expected exactly one producer of context.{capture_name}, found "
        f"{[name for name, _ in producers]}"
    )
    return producers[0]


@pytest.mark.parametrize("consumer", _CONSUMERS, ids=lambda case: case.recipe_name)
def test_review_consumer_has_explicit_canonical_repository_producer(
    consumer: ReviewConsumer,
) -> None:
    """Every publisher receives one fail-closed nameWithOwner identity."""
    steps = _load_steps(consumer)
    producer_name, producer = _find_capture_producer(steps, "review_repository")
    with_args = producer["with"]

    assert producer["tool"] == "run_cmd", producer_name
    assert with_args["cmd"] == (
        "gh repo view --json nameWithOwner -q '.nameWithOwner | ascii_downcase'"
    )
    assert with_args["cwd"] == consumer.work_dir_ref
    assert producer["capture"]["review_repository"] == "${{ result.stdout | trim }}"
    assert producer["on_failure"] == consumer.failure_route
    assert list(steps).index(producer_name) < list(steps).index("annotate_pr_diff")


@pytest.mark.parametrize("consumer", _CONSUMERS, ids=lambda case: case.recipe_name)
def test_review_consumer_uses_the_correct_captured_pr_identity(
    consumer: ReviewConsumer,
) -> None:
    """The recipe-specific PR variable is captured and reused without substitution."""
    steps = _load_steps(consumer)
    capture_name = consumer.pr_ref.removeprefix("${{ context.").removesuffix(" }}")
    producer_name, producer = _find_capture_producer(steps, capture_name)
    annotate = steps["annotate_pr_diff"]
    review = steps[consumer.review_step]

    assert producer.get("on_failure") == consumer.failure_route, producer_name
    assert annotate["with"]["pr_number"] == consumer.pr_ref
    assert review["with"]["skill_inputs"]["pr_number"] == consumer.pr_ref


@pytest.mark.parametrize("consumer", _CONSUMERS, ids=lambda case: case.recipe_name)
def test_review_annotation_requires_a_stable_live_head_and_shared_namespace(
    consumer: ReviewConsumer,
) -> None:
    """Annotation is mandatory, fail-closed, and owns the review artifact namespace."""
    steps = _load_steps(consumer)
    annotate = steps["annotate_pr_diff"]
    review = steps[consumer.review_step]

    assert annotate["tool"] == "run_python"
    assert annotate["with"]["callable"] == "autoskillit.smoke_utils.annotate_pr_diff"
    assert annotate["with"]["cwd"] == consumer.work_dir_ref
    assert annotate["with"]["work_dir"] == consumer.work_dir_ref
    assert "args" not in annotate["with"]
    assert annotate["capture"]["pr_head_sha"] == "${{ result.pr_head_sha }}"
    assert annotate["on_failure"] == consumer.failure_route
    assert annotate["with"]["output_dir"] == review["with"]["output_dir"]
    assert annotate["with"]["output_dir"].startswith("{{AUTOSKILLIT_TEMP}}/")


@pytest.mark.parametrize("consumer", _CONSUMERS, ids=lambda case: case.recipe_name)
def test_review_publisher_captures_receipt_identity(
    consumer: ReviewConsumer,
) -> None:
    """The publication server result is captured under one collision-free namespace."""
    steps = _load_steps(consumer)
    review = steps[consumer.review_step]
    skill_inputs = review["with"]["skill_inputs"]
    capture = review["capture"]

    assert skill_inputs["repository"] == "${{ context.review_repository }}"
    assert skill_inputs["pr_number"] == consumer.pr_ref
    assert skill_inputs["pr_head_sha"] == "${{ context.pr_head_sha }}"
    assert skill_inputs["receipt_path"].endswith(f"/batch_review_response_{consumer.pr_ref}.json")
    assert capture["review_operation_key"] == "${{ result.review_operation_key }}"
    assert capture["review_head_sha"] == "${{ result.review_head_sha }}"
    assert capture["review_post_state"] == "${{ result.review_post_state }}"
    assert capture["review_receipt_path"] == "${{ result.review_receipt_path }}"

    logical_iteration = skill_inputs["logical_iteration"]
    if consumer.logical_iteration:
        assert logical_iteration == consumer.logical_iteration
    else:
        assert re.fullmatch(consumer.logical_iteration_pattern, logical_iteration)


@pytest.mark.parametrize("consumer", _CONSUMERS, ids=lambda case: case.recipe_name)
def test_check_review_posted_receives_the_exact_publication_identity(
    consumer: ReviewConsumer,
) -> None:
    """The verifier receives only captured server identity, never a recomputed approximation."""
    steps = _load_steps(consumer)
    review = steps[consumer.review_step]
    check = steps["check_review_posted"]
    with_block = check["with"]
    with_args = with_block["args"]
    expected_logical_iteration = review["with"]["skill_inputs"]["logical_iteration"]

    assert check["tool"] == "run_python"
    assert with_block["callable"] == "autoskillit.smoke_utils.check_review_posted"
    assert with_block["work_dir"] == consumer.work_dir_ref
    assert with_args["cwd"] == consumer.work_dir_ref
    assert with_args["receipt_path"] == "${{ context.review_receipt_path }}"
    assert with_args["repository"] == "${{ context.review_repository }}"
    assert with_args["pr_number"] == consumer.pr_ref
    assert with_args["head_sha"] == "${{ context.review_head_sha }}"
    assert with_args["logical_iteration"] == expected_logical_iteration
    assert with_args["operation_key"] == "${{ context.review_operation_key }}"
    assert with_args["post_state"] == "${{ context.review_post_state }}"
    assert "output_dir" not in with_block
    assert check["on_failure"] == consumer.failure_route
    assert check["on_result"][0] == {
        "when": "${{ result.reviews_posted }} == 'false'",
        "route": consumer.failure_route,
    }


@pytest.mark.parametrize(
    (
        "recipe_name",
        "resolve_step",
        "pr_ref",
        "logical_iteration",
        "success_route",
        "failure_route",
    ),
    (
        (
            "implementation",
            "resolve_review",
            "${{ context.pr_number }}",
            "resolve-review:${{ context.review_loop_count }}",
            "pre_review_rebase",
            "release_issue_failure",
        ),
        (
            "implementation-groups",
            "resolve_review",
            "${{ context.pr_number }}",
            "resolve-review:${{ context.review_loop_count }}",
            "pre_review_rebase",
            "release_issue_failure",
        ),
        (
            "remediation",
            "resolve_review",
            "${{ context.pr_number }}",
            "resolve-review:${{ context.review_loop_count }}",
            "pre_review_rebase",
            "release_issue_failure",
        ),
        (
            "merge-prs",
            "resolve_review_integration",
            "${{ context.review_pr_number }}",
            "resolve-review:integration",
            "pre_review_rebase_integration",
            "register_clone_failure",
        ),
    ),
)
def test_resolve_review_publication_is_effect_verified(
    recipe_name: str,
    resolve_step: str,
    pr_ref: str,
    logical_iteration: str,
    success_route: str,
    failure_route: str,
) -> None:
    """Conditional resolve-review publication must cross the same exact receipt gate."""
    steps = load_yaml(builtin_recipes_dir() / f"{recipe_name}.yaml")["steps"]
    resolve = steps[resolve_step]
    check = steps["check_resolve_review_posted"]
    args = check["with"]["args"]

    assert (
        resolve["capture"]
        | {
            "resolve_review_operation_key": "${{ result.review_operation_key }}",
            "resolve_review_head_sha": "${{ result.review_head_sha }}",
            "resolve_review_post_state": "${{ result.review_post_state }}",
            "resolve_review_receipt_path": "${{ result.review_receipt_path }}",
        }
        == resolve["capture"]
    )
    assert {
        "when": "${{ result.review_post_state }} == 'SUCCEEDED'",
        "route": "check_resolve_review_posted",
    } in resolve["on_result"]
    assert {
        "when": "${{ result.review_post_state }} == 'RECONCILED'",
        "route": "check_resolve_review_posted",
    } in resolve["on_result"]
    assert check["with"]["callable"] == "autoskillit.smoke_utils.check_review_posted"
    assert args == {
        "cwd": "${{ context.work_dir }}",
        "receipt_path": "${{ context.resolve_review_receipt_path }}",
        "mode": "github",
        "repository": "${{ context.review_repository }}",
        "pr_number": pr_ref,
        "head_sha": "${{ context.resolve_review_head_sha }}",
        "logical_iteration": logical_iteration,
        "operation_key": "${{ context.resolve_review_operation_key }}",
        "post_state": "${{ context.resolve_review_post_state }}",
    }
    assert check["on_result"][0] == {
        "when": "${{ result.reviews_posted }} == 'true'",
        "route": success_route,
    }
    assert check["on_failure"] == failure_route
