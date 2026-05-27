"""TypedDict conformance: required-key sets, SKILL.md alignment, factory validation."""

from __future__ import annotations

import pytest

from autoskillit.planner.schema import (
    ASSIGNMENT_REQUIRED_KEYS,
    DELIVERABLE_BOUNDS,
    PHASE_REQUIRED_KEYS,
    WP_REQUIRED_KEYS,
    AssignmentElaborated,
    AssignmentResult,
    PhaseResult,
    PhaseShort,
    WPElaborated,
    WPResult,
    parse_planner_id,
    validate_assignment_result,
    validate_phase_result,
    validate_wp_result,
)

pytestmark = [pytest.mark.layer("planner"), pytest.mark.small, pytest.mark.feature("planner")]


def test_phase_required_keys_subset_of_typed_dict_fields() -> None:
    assert PHASE_REQUIRED_KEYS <= PhaseResult.__required_keys__


def test_assignment_required_keys_subset_of_typed_dict_fields() -> None:
    assert ASSIGNMENT_REQUIRED_KEYS <= AssignmentResult.__required_keys__


def test_wp_required_keys_subset_of_typed_dict_fields() -> None:
    assert WP_REQUIRED_KEYS <= WPResult.__required_keys__


def test_validate_phase_result_rejects_missing_required_keys() -> None:
    with pytest.raises(ValueError, match="missing required keys"):
        validate_phase_result({"name": "Only Name"})


def test_validate_assignment_result_rejects_missing_required_keys() -> None:
    with pytest.raises(ValueError, match="missing required keys"):
        validate_assignment_result({"name": "Only Name"})


def test_validate_wp_result_rejects_missing_required_keys() -> None:
    with pytest.raises(ValueError, match="missing required keys"):
        validate_wp_result({"name": "Only Name"})


def test_factory_phase_result_contains_all_backend_keys() -> None:
    from tests.planner.conftest import make_phase_result

    result = make_phase_result(3, name="Third Phase")

    assert result["phase_number"] == 3
    assert result["name_slug"] == "third-phase"
    assert isinstance(result["assignments"], list)


def test_factory_assignment_result_contains_all_backend_keys() -> None:
    from tests.planner.conftest import make_assignment_result

    result = make_assignment_result(2, 4)

    assert result["phase_number"] == 2
    assert result["assignment_number"] == 4
    assert result["id"] == "P2-A4"


def test_factory_wp_result_contains_required_keys() -> None:
    from tests.planner.conftest import make_wp_result

    result = make_wp_result("P1-A1-WP2")

    assert result["id"] == "P1-A1-WP2"
    assert isinstance(result["deliverables"], list)
    assert len(result["deliverables"]) >= 1


@pytest.mark.parametrize(
    "assignments_preview,expected_count",
    [
        ([], 0),
        (["Schema design"], 1),
        (["Task A", "Task B", "Task C"], 3),
    ],
)
def test_validate_phase_result_derives_assignments_from_preview(
    assignments_preview: list[str], expected_count: int
) -> None:
    result = validate_phase_result(
        {
            "id": "P1",
            "name": "Phase One",
            "ordering": 1,
            "assignments_preview": assignments_preview,
        }
    )
    assert len(result["assignments"]) == expected_count
    for item in result["assignments"]:
        assert "name" in item
        assert "metadata" in item


@pytest.mark.parametrize(
    "assign_id,expected_pn,expected_an",
    [
        ("P1-A1", 1, 1),
        ("P3-A7", 3, 7),
        ("P10-A2", 10, 2),
    ],
)
def test_validate_assignment_result_parses_id(
    assign_id: str, expected_pn: int, expected_an: int
) -> None:
    result = validate_assignment_result(
        {
            "id": assign_id,
            "name": "Test",
            "proposed_work_packages": [],
        }
    )
    assert result["phase_number"] == expected_pn
    assert result["assignment_number"] == expected_an


@pytest.mark.parametrize(
    "name,expected_slug",
    [
        ("Phase One", "phase-one"),
        ("Foundation", "foundation"),
        ("Schema & Validation", "schema-validation"),
        ("Phase 1", "phase-1"),
    ],
)
def test_validate_phase_result_slugifies_name(name: str, expected_slug: str) -> None:
    result = validate_phase_result({"id": "P1", "name": name, "ordering": 1})
    assert result["name_slug"] == expected_slug


def test_phase_short_includes_ordering() -> None:
    import typing

    hints = typing.get_type_hints(PhaseShort)
    assert "ordering" in hints, "PhaseShort must include ordering per Issue 02 spec"
    assert hints["ordering"] is int


def test_validate_wp_result_rejects_empty_deliverables() -> None:
    with pytest.raises(ValueError, match="has 0 deliverables"):
        validate_wp_result({"id": "P1-A1-WP1", "name": "WP", "deliverables": []})


def test_validate_wp_result_accepts_empty_deliverables_with_allow_stub() -> None:
    result = validate_wp_result(
        {"id": "P1-A1-WP1", "name": "WP", "deliverables": []}, allow_stub=True
    )
    assert result["deliverables"] == []


def test_validate_wp_result_warns_too_many_deliverables() -> None:
    _, hi = DELIVERABLE_BOUNDS
    with pytest.warns(UserWarning, match=f"has {hi + 1} deliverables"):
        validate_wp_result(
            {
                "id": "P1-A1-WP1",
                "name": "WP",
                "deliverables": [f"f{i}.py" for i in range(hi + 1)],
            }
        )


def test_validate_wp_result_skips_upper_bound_when_requested() -> None:
    _, hi = DELIVERABLE_BOUNDS
    result = validate_wp_result(
        {
            "id": "P1-A1-WP1",
            "name": "WP",
            "deliverables": [f"f{i}.py" for i in range(hi + 2)],
        },
        skip_upper_bound=True,
    )
    assert len(result["deliverables"]) == hi + 2


def test_validate_wp_result_enforces_lower_bound_with_skip_upper_bound() -> None:
    with pytest.raises(ValueError, match="has 0 deliverables"):
        validate_wp_result(
            {"id": "P1-A1-WP1", "name": "WP", "deliverables": []},
            skip_upper_bound=True,
        )


def test_validate_wp_result_injects_phase_id_and_assignment_id() -> None:
    result = validate_wp_result({"id": "P1-A2-WP3", "name": "WP", "deliverables": ["f.py"]})
    assert result["phase_id"] == "P1"
    assert result["assignment_id"] == "P1-A2"


def test_validate_wp_result_output_covers_wp_elaborated_required_keys() -> None:
    result = validate_wp_result({"id": "P1-A1-WP1", "name": "WP", "deliverables": ["f.py"]})
    missing = WPElaborated.__required_keys__ - result.keys()
    assert not missing, f"validate_wp_result output missing WPElaborated keys: {missing}"


def test_validate_assignment_result_output_covers_assignment_elaborated_required_keys() -> None:
    result = validate_assignment_result({"id": "P1-A1", "name": "A", "proposed_work_packages": []})
    missing = AssignmentElaborated.__required_keys__ - result.keys()
    assert not missing, (
        f"validate_assignment_result output missing AssignmentElaborated keys: {missing}"
    )


def test_make_wp_result_fixture_includes_ancestry_fields() -> None:
    from tests.planner.conftest import make_wp_result

    result = make_wp_result("P1-A1-WP1")
    assert "phase_id" in result
    assert "assignment_id" in result


def test_parse_planner_id_phase() -> None:
    assert parse_planner_id("P1") == (1,)


def test_parse_planner_id_assignment() -> None:
    assert parse_planner_id("P3-A7") == (3, 7)


def test_parse_planner_id_wp() -> None:
    assert parse_planner_id("P10-A2-WP5") == (10, 2, 5)


def test_parse_planner_id_rejects_malformed() -> None:
    with pytest.raises(ValueError):
        parse_planner_id("bad")
