"""Structural guards proving the pagination test split is mechanically complete.

These tests assert that every test from the pre-split `test_recipe_section_pagination.py`
has been moved to one of the three new focused files, that no pre-split name is lost or
duplicated, and that the new files carry the project-required layer and size markers.
The tests themselves do not exercise production code; they guard the reorganization
contract.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

_PRE_SPLIT_PAGINATION_NAMES: frozenset[str] = frozenset(
    {
        "test_extract_recipe_step_bodies_preserves_requested_order",
        "test_request_state_rejects_non_integer_and_below_floor_bounds",
        "test_page_descriptor_rejects_unknown_incomplete_and_mixed_range_families",
        "test_page_descriptor_rejects_malformed_raw_range_values",
        "test_page_descriptor_rejects_malformed_fragment_values",
        "test_scalar_planning_never_serializes_the_whole_oversized_remainder",
        "test_terminal_initialization_page_carries_progress_and_completion_receipt",
        "test_char_ceiling_accepts_a_page_within_it",
        "test_char_ceiling_rejects_a_page_that_exceeds_it",
        "test_select_recipe_section_loads_only_recognized_dynamic_content",
        "test_select_recipe_section_rejects_empty_dynamic_content",
        "test_failure_floor_is_derived_from_the_registered_renderer",
        "test_raw_pages_preserve_text_and_exact_utf8_bounds",
        "test_json_scalar_pages_are_independently_valid_and_reconstruct_markdown",
        "test_ordered_array_pages_are_complete_json_documents",
        "test_oversized_array_elements_fragment_in_first_middle_and_final_positions",
        "test_array_plan_can_interleave_ordinary_and_fragment_pages",
        "test_raw_recipe_and_named_step_yaml_use_unchanged_raw_reconstruction",
        "test_exact_fit_succeeds_and_one_byte_under_replans_without_oversize",
        "test_production_like_ten_thousand_byte_bound_is_honored",
        "test_candidate_sizing_uses_binary_search_scale_oracle_calls",
        "test_convergence_ceiling_is_derived_from_artifact_policy",
        "test_final_digest_injection_revalidates_descriptor_boundaries",
        "test_final_verifier_rejects_fragment_descriptor_and_content_corruption",
        "test_page_and_fragment_indices_cross_two_digit_boundaries",
        "test_plan_manifest_is_complete_and_plan_digest_is_non_self_referential",
        "test_string_scalar_strategy_rejects_non_string_values",
        "test_repeat_builds_and_fresh_cache_are_deterministic",
        "test_cached_plans_are_reused_and_cache_clear_forces_a_rebuild",
        "test_concurrent_same_key_requests_share_one_page_plan_build",
        "test_retirement_during_build_prevents_stale_cache_admission",
        "test_every_cache_key_dimension_prevents_aliasing",
        "test_cache_entry_limit_evicts_oldest_plan",
        "test_cache_rejects_a_single_plan_over_its_byte_limit",
        "test_cache_byte_limit_evicts_oldest_plan",
        "test_cache_replacement_subtracts_the_previous_plan_weight",
        "test_cache_kitchen_eviction_subtracts_every_matching_plan_weight",
        "test_cross_process_plan_and_rendering_are_deterministic",
    }
)

_PAGINATION_SPLIT_TARGETS: tuple[tuple[str, tuple[str, ...]], ...] = (
    # planning
    (
        "tests.server.test_recipe_section_pagination_planning",
        (
            "test_request_state_rejects_non_integer_and_below_floor_bounds",
            "test_page_descriptor_rejects_unknown_incomplete_and_mixed_range_families",
            "test_page_descriptor_rejects_malformed_raw_range_values",
            "test_page_descriptor_rejects_malformed_fragment_values",
            "test_scalar_planning_never_serializes_the_whole_oversized_remainder",
            "test_terminal_initialization_page_carries_progress_and_completion_receipt",
            "test_char_ceiling_accepts_a_page_within_it",
            "test_char_ceiling_rejects_a_page_that_exceeds_it",
            "test_select_recipe_section_loads_only_recognized_dynamic_content",
            "test_select_recipe_section_rejects_empty_dynamic_content",
            "test_failure_floor_is_derived_from_the_registered_renderer",
            "test_string_scalar_strategy_rejects_non_string_values",
            "test_convergence_ceiling_is_derived_from_artifact_policy",
        ),
    ),
    # reconstruction
    (
        "tests.server.test_recipe_section_pagination_reconstruction",
        (
            "test_extract_recipe_step_bodies_preserves_requested_order",
            "test_raw_pages_preserve_text_and_exact_utf8_bounds",
            "test_json_scalar_pages_are_independently_valid_and_reconstruct_markdown",
            "test_ordered_array_pages_are_complete_json_documents",
            "test_oversized_array_elements_fragment_in_first_middle_and_final_positions",
            "test_array_plan_can_interleave_ordinary_and_fragment_pages",
            "test_raw_recipe_and_named_step_yaml_use_unchanged_raw_reconstruction",
            "test_exact_fit_succeeds_and_one_byte_under_replans_without_oversize",
            "test_production_like_ten_thousand_byte_bound_is_honored",
            "test_page_and_fragment_indices_cross_two_digit_boundaries",
        ),
    ),
    # cache (PagePlanCache mechanics + concurrent build races)
    (
        "tests.server.test_recipe_section_pagination_cache",
        (
            "test_cached_plans_are_reused_and_cache_clear_forces_a_rebuild",
            "test_concurrent_same_key_requests_share_one_page_plan_build",
            "test_retirement_during_build_prevents_stale_cache_admission",
            "test_every_cache_key_dimension_prevents_aliasing",
            "test_cache_entry_limit_evicts_oldest_plan",
            "test_cache_rejects_a_single_plan_over_its_byte_limit",
            "test_cache_byte_limit_evicts_oldest_plan",
            "test_cache_replacement_subtracts_the_previous_plan_weight",
            "test_cache_kitchen_eviction_subtracts_every_matching_plan_weight",
        ),
    ),
    # invariants (sizing, descriptor integrity, manifest, determinism)
    (
        "tests.server.test_recipe_section_pagination_invariants",
        (
            "test_candidate_sizing_uses_binary_search_scale_oracle_calls",
            "test_final_digest_injection_revalidates_descriptor_boundaries",
            "test_final_verifier_rejects_fragment_descriptor_and_content_corruption",
            "test_plan_manifest_is_complete_and_plan_digest_is_non_self_referential",
            "test_repeat_builds_and_fresh_cache_are_deterministic",
            "test_cross_process_plan_and_rendering_are_deterministic",
        ),
    ),
)

_TEST_TO_MODULE: dict[str, str] = {
    test_name: target_module
    for target_module, test_names in _PAGINATION_SPLIT_TARGETS
    for test_name in test_names
}

_TEST_TO_MODULE_KEYS = frozenset(_TEST_TO_MODULE.keys())

_NEW_PAGINATION_TEST_FILES = (
    "tests/server/test_recipe_section_pagination_planning.py",
    "tests/server/test_recipe_section_pagination_reconstruction.py",
    "tests/server/test_recipe_section_pagination_cache.py",
    "tests/server/test_recipe_section_pagination_invariants.py",
)

_ALL_NEW_PAGINATION_FILES = (
    "tests/server/_recipe_section_pagination_test_helpers.py",
    *_NEW_PAGINATION_TEST_FILES,
    "tests/server/test_recipe_section_pagination_split_completeness.py",
)

# Helper-module names that the new test files import. Same private names
# as the source used — preserving these is the contract that makes the
# "verbatim" move claim in the Tests section true.
_EXPECTED_HELPER_EXPORTS = frozenset(
    {
        "_ALL_RANGE_FIELDS",
        "_PAGE_TEST_BOUND",
        "_RANGE_FIELDS_BY_FORMAT",
        "_build",
        "_clear_page_plan_cache",
        "_decoded_pages",
        "_generation",
        "_payload",
        "_reconstruct",
        "_rendered_pages",
    }
)


def test_pre_split_pagination_inventory_is_frozen() -> None:
    """The pre-split inventory is well-formed (no duplicates, no leading dots)."""
    # Size check derives from the module map; if a pre-split test is added or
    # dropped without updating both sides, this size check fires first with a
    # clear "drift" signal before the equality check below can run.
    assert len(_PRE_SPLIT_PAGINATION_NAMES) == len(_TEST_TO_MODULE_KEYS)
    for name in _PRE_SPLIT_PAGINATION_NAMES:
        assert not name.startswith("."), name
    assert _TEST_TO_MODULE_KEYS == _PRE_SPLIT_PAGINATION_NAMES


def test_no_pre_split_pagination_file_exists() -> None:
    """The pre-split `test_recipe_section_pagination.py` must be deleted post-split."""
    assert not Path("tests/server/test_recipe_section_pagination.py").exists(), (
        "Pre-split pagination file must be deleted; the completeness guard is now live"
    )


@pytest.mark.parametrize("path", _ALL_NEW_PAGINATION_FILES)
def test_every_split_target_file_exists(path: str) -> None:
    assert Path(path).is_file(), f"Split target {path} does not exist"


@pytest.mark.parametrize(
    ("test_name", "module_name"),
    sorted(_TEST_TO_MODULE.items()),
)
def test_pre_split_test_name_resolves_to_its_target_file(test_name: str, module_name: str) -> None:
    """Every pre-split test must resolve to its declared target module."""
    module = importlib.import_module(module_name)
    assert hasattr(module, test_name), f"{test_name} missing from {module_name}"


def test_every_pre_split_test_name_appears_in_exactly_one_new_file() -> None:
    """Each pre-split name appears in exactly one of the three new test files (no dup, no loss)."""
    counts: dict[str, int] = {}
    for path_str in _NEW_PAGINATION_TEST_FILES:
        path = Path(path_str)
        text = path.read_text(encoding="utf-8")
        for name in _PRE_SPLIT_PAGINATION_NAMES:
            if f"def {name}(" in text:
                counts[name] = counts.get(name, 0) + 1
    missing = _PRE_SPLIT_PAGINATION_NAMES - counts.keys()
    duplicates = {name: c for name, c in counts.items() if c > 1}
    assert not missing, f"Pre-split tests not found in any new file: {missing}"
    assert not duplicates, f"Pre-split tests duplicated across new files: {duplicates}"


def test_no_unintended_new_pagination_test_files_under_tests_server() -> None:
    """Only the four expected pagination files match the test_recipe_section_pagination pattern."""
    import re

    server_dir = Path("tests/server")
    pattern = re.compile(r"^test_recipe_section_pagination.*\.py$")
    found = sorted(p.name for p in server_dir.iterdir() if pattern.match(p.name))
    # Filter helper files (private modules start with `_`) — the prior `if "test_" in p`
    # heuristic broke once helpers were renamed to `_recipe_section_pagination_test_helpers.py`.
    expected = sorted(
        p.split("/")[-1] for p in _ALL_NEW_PAGINATION_FILES if not p.split("/")[-1].startswith("_")
    )
    assert found == expected, (
        f"Unexpected new pagination test files: {set(found) - set(expected)}; "
        f"missing: {set(expected) - set(found)}"
    )


def test_helper_module_exports_match_source_helper_names() -> None:
    """The helper module exposes exactly the same private names the source used — this is the
    contract that makes verbatim test-body moves work without call-site rewrites."""
    import ast

    from tests.server import _recipe_section_pagination_test_helpers as helpers

    # Walk only the module body (not function bodies) to collect names defined at module
    # level: functions, classes, and module-level assignments. dir()/vars() would surface
    # imports; ast.walk() would descend into function bodies and pick up locals.
    assert helpers.__file__ is not None
    tree = ast.parse(Path(helpers.__file__).read_text(encoding="utf-8"))
    actual: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            actual.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    actual.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            actual.add(node.target.id)
    actual = {name for name in actual if not name.startswith("__")}
    missing = _EXPECTED_HELPER_EXPORTS - actual
    unexpected = actual - _EXPECTED_HELPER_EXPORTS
    assert not missing, f"Helper module is missing expected names: {missing}"
    assert not unexpected, f"Helper module has unexpected names: {unexpected}"


@pytest.mark.parametrize("path", _NEW_PAGINATION_TEST_FILES)
def test_every_split_file_has_layer_server_marker(path: str) -> None:
    text = Path(path).read_text(encoding="utf-8")
    assert 'pytest.mark.layer("server")' in text, f"{path} must carry the server layer marker"


@pytest.mark.parametrize("path", _NEW_PAGINATION_TEST_FILES)
def test_every_split_file_has_medium_size_marker(path: str) -> None:
    text = Path(path).read_text(encoding="utf-8")
    assert "pytest.mark.medium" in text, (
        f"{path} must carry the medium size marker (matches source file)"
    )


pytestmark = [pytest.mark.layer("server"), pytest.mark.small]
