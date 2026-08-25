"""Smoke-utils tests relocated from the former monolith."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit.smoke_utils import (
    check_review_loop,
    init_counter,
)

pytestmark = [pytest.mark.medium]


def test_crl_next_iteration_increments() -> None:
    """next_iteration increments from current_iteration: "" → "1", "1" → "2", "2" → "3"."""
    r1 = check_review_loop("1", current_iteration="")
    assert r1["next_iteration"] == "1"

    r2 = check_review_loop("1", current_iteration="1")
    assert r2["next_iteration"] == "2"

    r3 = check_review_loop("1", current_iteration="2")
    assert r3["next_iteration"] == "3"


def test_crl_max_exceeded_when_next_iteration_ge_max() -> None:
    """max_exceeded=true when next_iteration >= max_iterations."""
    result = check_review_loop("1", current_iteration="2", max_iterations="3")
    assert result["max_exceeded"] == "true"
    assert result["next_iteration"] == "3"


def test_crl_max_not_exceeded_when_below_max() -> None:
    """max_exceeded=false when next_iteration < max_iterations."""
    result = check_review_loop("1", current_iteration="1", max_iterations="3")
    assert result["max_exceeded"] == "false"


def test_check_review_loop_always_continues_when_iterations_remain() -> None:
    """After a resolve cycle, check_review_loop must indicate continuation
    when max_iterations is not exceeded — regardless of GitHub thread state.

    The function is a pure iteration guard: if next_iteration < max_iterations,
    it must return max_exceeded=false so the recipe routes back to review_pr.
    """
    result = check_review_loop(
        pr_number="42",
        current_iteration="0",
        max_iterations="3",
    )
    assert result["max_exceeded"] == "false"
    assert result["next_iteration"] == "1"


def test_check_review_loop_stops_at_max_iterations() -> None:
    """When current_iteration reaches max_iterations, max_exceeded must be true."""
    result = check_review_loop(
        pr_number="42",
        current_iteration="2",
        max_iterations="3",
    )
    assert result["max_exceeded"] == "true"
    assert result["next_iteration"] == "3"


def test_check_review_loop_returns_expected_fields() -> None:
    """check_review_loop returns next/prev iteration, max_exceeded, had_blocking."""
    result = check_review_loop(pr_number="42")
    assert set(result.keys()) == {
        "next_iteration",
        "prev_iteration",
        "max_exceeded",
        "had_blocking",
    }


def test_check_review_loop_has_no_subprocess_calls() -> None:
    """The simplified check_review_loop must not use subprocess at all."""
    import ast

    src = Path("src/autoskillit/smoke_utils/_review_design.py").read_text()
    tree = ast.parse(src)

    # Find the check_review_loop function node
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "check_review_loop":
            for child in ast.walk(node):
                if isinstance(child, ast.Attribute) and child.attr == "run":
                    if isinstance(child.value, ast.Name) and child.value.id == "subprocess":
                        raise AssertionError(
                            "check_review_loop should not use subprocess.run() — "
                            "it is a pure iteration guard"
                        )
            break


def test_crl_had_blocking_true_when_changes_requested() -> None:
    """had_blocking=true when previous_verdict is changes_requested."""
    result = check_review_loop("42", previous_verdict="changes_requested")
    assert result["had_blocking"] == "true"


def test_crl_had_blocking_false_when_approved_with_comments() -> None:
    """had_blocking=false when previous_verdict is approved_with_comments."""
    result = check_review_loop("42", previous_verdict="approved_with_comments")
    assert result["had_blocking"] == "false"


def test_crl_had_blocking_false_when_empty_verdict() -> None:
    """had_blocking=false when previous_verdict is absent (first-pass guard)."""
    result = check_review_loop("42")
    assert result["had_blocking"] == "false"


def test_crl_local_rounds_not_exhausted_approved_is_blocking() -> None:
    """When local_review_rounds > 0 and iteration < local_rounds, approved is blocking.

    The approved verdict must trigger re-review until local_review_rounds are
    exhausted, so review_loop_count advances on every local round.
    """
    result = check_review_loop(
        pr_number="42",
        current_iteration="0",
        max_iterations="6",
        previous_verdict="approved",
        local_review_rounds="3",
    )
    assert result["had_blocking"] == "true"
    assert result["next_iteration"] == "1"
    assert result["max_exceeded"] == "false"


def test_crl_local_rounds_exhausted_approved_is_non_blocking() -> None:
    """When iteration >= local_review_rounds, approved is non-blocking.

    Once local rounds are exhausted, approved exits to CI immediately.
    """
    result = check_review_loop(
        pr_number="42",
        current_iteration="3",
        max_iterations="6",
        previous_verdict="approved",
        local_review_rounds="3",
    )
    assert result["had_blocking"] == "false"
    assert result["next_iteration"] == "4"


def test_crl_changes_requested_always_blocking_regardless_of_local_rounds() -> None:
    """changes_requested is always blocking, even after local rounds exhausted."""
    result = check_review_loop(
        pr_number="42",
        current_iteration="5",
        max_iterations="6",
        previous_verdict="changes_requested",
        local_review_rounds="3",
    )
    assert result["had_blocking"] == "true"


def test_crl_no_local_rounds_approved_is_non_blocking() -> None:
    """When local_review_rounds is absent or zero, approved is non-blocking as before.

    This preserves backward compatibility: without local_review_rounds configured,
    the only blocking verdict is changes_requested.
    """
    result = check_review_loop(
        pr_number="42",
        current_iteration="0",
        max_iterations="3",
        previous_verdict="approved",
        local_review_rounds="",
    )
    assert result["had_blocking"] == "false"


def test_crl_local_rounds_zero_approved_is_non_blocking() -> None:
    """local_review_rounds="0" means no local rounds, approved is non-blocking."""
    result = check_review_loop(
        pr_number="42",
        current_iteration="0",
        max_iterations="3",
        previous_verdict="approved",
        local_review_rounds="0",
    )
    assert result["had_blocking"] == "false"


def test_crl_needs_human_non_blocking_when_local_rounds_exhausted() -> None:
    """needs_human is non-blocking when local rounds are exhausted."""
    result = check_review_loop(
        pr_number="42",
        current_iteration="3",
        max_iterations="6",
        previous_verdict="needs_human",
        local_review_rounds="3",
    )
    assert result["had_blocking"] == "false"


def test_crl_approved_with_comments_non_blocking_when_local_rounds_active() -> None:
    """approved_with_comments must NOT trigger re-review even when local_rounds are not exhausted.

    The resolve_review pass for approved_with_comments is one-shot. Re-reviewing
    after resolved warnings adds no value and wastes time budget.
    """
    result = check_review_loop(
        pr_number="42",
        current_iteration="1",
        max_iterations="6",
        previous_verdict="approved_with_comments",
        local_review_rounds="2",
    )
    assert result["had_blocking"] == "false"
    assert result["next_iteration"] == "2"


def test_crl_approved_with_comments_non_blocking_at_first_local_round() -> None:
    """approved_with_comments at iteration 0 with local_review_rounds > 0 is still non-blocking."""
    result = check_review_loop(
        pr_number="42",
        current_iteration="0",
        max_iterations="6",
        previous_verdict="approved_with_comments",
        local_review_rounds="3",
    )
    assert result["had_blocking"] == "false"


def test_local_round_exempt_verdicts_constant_exists() -> None:
    """LOCAL_ROUND_EXEMPT_VERDICTS must exist and contain approved_with_comments."""
    from autoskillit.smoke_utils import LOCAL_ROUND_EXEMPT_VERDICTS

    assert "approved_with_comments" in LOCAL_ROUND_EXEMPT_VERDICTS
    assert "changes_requested" not in LOCAL_ROUND_EXEMPT_VERDICTS
    assert "approved" not in LOCAL_ROUND_EXEMPT_VERDICTS


def test_needs_human_exempt_from_local_rounds() -> None:
    """needs_human must be exempt from local_review_rounds re-review."""
    result = check_review_loop(
        pr_number="42",
        current_iteration="0",
        max_iterations="6",
        previous_verdict="needs_human",
        local_review_rounds="2",
    )
    assert result["had_blocking"] == "false", (
        "needs_human must yield had_blocking=false regardless of local_review_rounds "
        "because it indicates review was skipped (graceful degradation) and "
        "re-review would be pointless."
    )


def test_check_review_loop_none_current() -> None:
    result = check_review_loop(pr_number="1", current_iteration=None)  # type: ignore[arg-type]
    assert result["next_iteration"] == "1"


def test_check_review_loop_none_verdict() -> None:
    result = check_review_loop(pr_number="1", previous_verdict=None)  # type: ignore[arg-type]
    assert result["had_blocking"] == "false"


def test_init_counter_with_empty_string() -> None:
    """init_counter returns value='0' when counter_value is empty."""

    assert init_counter(counter_value="") == {"value": "0"}


def test_init_counter_with_whitespace_only() -> None:
    """init_counter returns value='0' when counter_value is whitespace."""

    assert init_counter(counter_value="  ") == {"value": "0"}


def test_init_counter_with_numeric_value() -> None:
    """init_counter passes through a numeric string unchanged."""

    assert init_counter(counter_value="2") == {"value": "2"}


def test_pre_iteration_cleanup_removes_files(tmp_path: Path) -> None:
    """pre_iteration_cleanup removes all files in output_dir, preserving patterns."""
    from autoskillit.smoke_utils import pre_iteration_cleanup  # noqa: PLC0415

    out = tmp_path / "iter_0"
    out.mkdir()
    (out / "prior_threads_123.json").write_text("{}")
    (out / "diff_context_123.json").write_text("{}")
    (out / "deferred_obs_123.json").write_text("[]")

    result = pre_iteration_cleanup(
        output_dir=str(out),
        preserve_patterns="deferred_obs*.json",
    )
    assert result["cleaned"] == "true"
    assert result["removed_count"] == "2"
    assert not (out / "prior_threads_123.json").exists()
    assert not (out / "diff_context_123.json").exists()
    assert (out / "deferred_obs_123.json").exists()


def test_pre_iteration_cleanup_noop_when_dir_missing(tmp_path: Path) -> None:
    """pre_iteration_cleanup is a no-op when output_dir does not exist."""
    from autoskillit.smoke_utils import pre_iteration_cleanup  # noqa: PLC0415

    result = pre_iteration_cleanup(output_dir=str(tmp_path / "nonexistent"))
    assert result["cleaned"] == "false"
    assert result["reason"] == "not_found"


def test_pre_iteration_cleanup_noop_when_dir_empty(tmp_path: Path) -> None:
    """pre_iteration_cleanup returns cleaned=true with removed_count=0 when dir is empty."""
    from autoskillit.smoke_utils import pre_iteration_cleanup  # noqa: PLC0415

    out = tmp_path / "empty_iter"
    out.mkdir()
    result = pre_iteration_cleanup(output_dir=str(out))
    assert result["cleaned"] == "true"
    assert result["removed_count"] == "0"


def test_select_review_dimensions_happy_path(tmp_path: Path) -> None:
    """Registry-grounded benchmark type returns 8 non-S lenses in H→M→L order."""
    from autoskillit.recipe import get_experiment_type_by_name
    from autoskillit.smoke_utils import select_review_dimensions

    spec = get_experiment_type_by_name("benchmark")
    assert spec is not None
    expected_dims = {d for d, w in spec.dimension_weights.items() if w != "S"}

    result = select_review_dimensions(
        experiment_type="benchmark",
        output_dir=str(tmp_path),
    )
    lenses = result["selected_lenses"].split(",")
    assert len(lenses) == len(expected_dims)
    assert set(lenses) == expected_dims
    manifest = json.loads(Path(result["dimensions_manifest_path"]).read_text())
    tiers = list(manifest.values())
    expected_order = sorted(tiers, key=lambda t: {"H": 0, "M": 1, "L": 2}.get(t, 3))
    assert tiers == expected_order


def test_select_review_dimensions_creates_output_dir(tmp_path: Path) -> None:
    """Benchmark experiment type creates missing output_dir and writes manifest."""
    from autoskillit.smoke_utils import select_review_dimensions

    out = tmp_path / "nested" / "output"
    assert not out.exists()
    result = select_review_dimensions(
        experiment_type="benchmark",
        output_dir=str(out),
    )
    assert out.exists()
    assert Path(result["dimensions_manifest_path"]).exists()


def test_select_review_dimensions_qualitative_type_returns_active_dims(
    tmp_path: Path,
) -> None:
    """Qualitative-interpretive type returns only its 2 active L-tier dimensions."""
    from autoskillit.recipe import get_experiment_type_by_name
    from autoskillit.smoke_utils import select_review_dimensions

    spec = get_experiment_type_by_name("qualitative_interpretive")
    assert spec is not None
    expected_dims = {d for d, w in spec.dimension_weights.items() if w != "S"}

    result = select_review_dimensions(
        experiment_type="qualitative_interpretive",
        output_dir=str(tmp_path),
    )
    lenses = result["selected_lenses"].split(",")
    assert len(lenses) == len(expected_dims)
    assert set(lenses) == expected_dims
    assert "data_acquisition" in lenses
    assert "agent_implementability" in lenses
    assert "causal_structure" not in lenses
    assert Path(result["dimensions_manifest_path"]).exists()


def test_select_review_dimensions_registry_happy_path(tmp_path: Path) -> None:
    """Registry lookup returns non-empty lenses for a known experiment type."""
    from autoskillit.smoke_utils import select_review_dimensions

    result = select_review_dimensions(
        experiment_type="causal_inference",
        output_dir=str(tmp_path),
    )
    assert result["selected_lenses"] != ""
    assert result["dimensions_manifest_path"] != ""
    manifest_path = Path(result["dimensions_manifest_path"])
    assert manifest_path.is_absolute()
    assert manifest_path.exists()


def test_select_review_dimensions_unknown_type_returns_empty(tmp_path: Path) -> None:
    """Unknown experiment type returns _EMPTY and writes no files."""
    from autoskillit.smoke_utils import select_review_dimensions

    result = select_review_dimensions(
        experiment_type="nonexistent_type",
        output_dir=str(tmp_path),
    )
    assert result == {
        "selected_lenses": "",
        "lens_context_paths": "",
        "dimensions_manifest_path": "",
    }
    assert not list(tmp_path.iterdir())


def test_select_review_dimensions_empty_type_returns_empty(tmp_path: Path) -> None:
    """Empty experiment_type returns _EMPTY and writes no files."""
    from autoskillit.smoke_utils import select_review_dimensions

    result = select_review_dimensions(
        experiment_type="",
        output_dir=str(tmp_path),
    )
    assert result == {
        "selected_lenses": "",
        "lens_context_paths": "",
        "dimensions_manifest_path": "",
    }
    assert not list(tmp_path.iterdir())
