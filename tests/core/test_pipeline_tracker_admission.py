"""Admission snapshots keep tracker dependency edges tied to executable steps."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

import pytest

from autoskillit.core.pipeline.pipeline_tracker import (
    TrackerDependencyAdmissionError,
    TrackerInitialization,
    initialize_kitchen_tracker,
    initialize_manual_tracker,
    is_tracker_enforced_step,
    mutate_tracker,
    read_tracker_authority,
    retain_tracker_lease,
)
from autoskillit.core.types import SKILL_TOOLS, FinalizedRecipeStep
from tests.core.test_pipeline_tracker import _key, _target, _tracker

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def _steps() -> dict[str, FinalizedRecipeStep]:
    return {
        "plan": FinalizedRecipeStep(name="plan", tool="run_skill"),
        "verify": FinalizedRecipeStep(name="verify", tool="run_skill"),
        "rectify": FinalizedRecipeStep(name="rectify", tool="run_skill"),
        "review_approach": FinalizedRecipeStep(name="review_approach", tool="run_skill"),
        "clone": FinalizedRecipeStep(name="clone", tool="bootstrap_clone"),
        "done": FinalizedRecipeStep(name="done", action="stop"),
    }


def _skill_steps(*names: str) -> dict[str, FinalizedRecipeStep]:
    return {name: FinalizedRecipeStep(name=name, tool="run_skill") for name in names}


def _initialization(
    *,
    kitchen_id: str = "kitchen-1",
    recipe_steps: Mapping[str, FinalizedRecipeStep] | None = None,
    dependencies: Mapping[str, Sequence[str]] | None = None,
    skipped: frozenset[str] | set[str] = frozenset(),
) -> TrackerInitialization:
    return TrackerInitialization(
        kitchen_id=kitchen_id,
        recipe_steps=_steps() if recipe_steps is None else recipe_steps,
        dependencies={} if dependencies is None else dependencies,
        skipped=skipped,
    )


@pytest.mark.parametrize(
    ("dependencies", "reason", "detail_parts"),
    [
        (
            {"plan": ["clone"]},
            "not_run_skill",
            ("prerequisite 'clone'", "bootstrap_clone"),
        ),
        (
            {"clone": ["plan"]},
            "not_run_skill",
            ("dependent 'clone'", "bootstrap_clone"),
        ),
        ({"plan": ["done"]}, "not_run_skill", ("prerequisite 'done'", "stop")),
        ({"plan": ["ghost"]}, "unknown_step", ("ghost",)),
        ({"ghost": ["plan"]}, "unknown_step", ("ghost",)),
        ({"plan": ["plan"]}, "self_dependency", ("plan",)),
    ],
)
def test_admission_rejects_invalid_dependency_edges(dependencies, reason, detail_parts):
    with pytest.raises(TrackerDependencyAdmissionError) as raised:
        _initialization(dependencies=dependencies)

    violations = raised.value.violations
    assert any(violation.reason == reason for violation in violations)
    assert any(
        violation.reason == reason and all(part in violation.detail for part in detail_parts)
        for violation in violations
    )
    if dependencies == {"plan": ["clone"]}:
        assert len(violations) == 1


def test_admission_reports_cycle_edges_without_marking_a_connecting_edge():
    cycle_edges = {
        ("a", "b"),
        ("b", "a"),
        ("c", "d"),
        ("d", "c"),
    }
    with pytest.raises(TrackerDependencyAdmissionError) as raised:
        _initialization(
            recipe_steps=_skill_steps("a", "b", "c", "d"),
            dependencies={"a": ["b"], "b": ["a", "c"], "c": ["d"], "d": ["c"]},
        )

    reported = {
        (violation.dependent, violation.prerequisite)
        for violation in raised.value.violations
        if violation.reason == "cycle"
    }
    assert reported
    assert reported <= cycle_edges
    assert ("b", "c") not in reported


@pytest.mark.parametrize(
    ("dependencies", "cycle_edges"),
    [
        (
            {"plan": ["verify"], "verify": ["plan"]},
            {("plan", "verify"), ("verify", "plan")},
        ),
        (
            {"plan": ["verify"], "verify": ["rectify"], "rectify": ["plan"]},
            {("plan", "verify"), ("verify", "rectify"), ("rectify", "plan")},
        ),
    ],
)
def test_admission_rejects_cycles_and_names_a_ring_edge(dependencies, cycle_edges):
    with pytest.raises(TrackerDependencyAdmissionError) as raised:
        _initialization(dependencies=dependencies)

    reported = {
        (violation.dependent, violation.prerequisite)
        for violation in raised.value.violations
        if violation.reason == "cycle"
    }
    assert reported & cycle_edges


def test_admission_rejects_edges_with_untyped_step_values():
    with pytest.raises(TrackerDependencyAdmissionError) as raised:
        _initialization(
            recipe_steps={"left": {}, "right": {}},
            dependencies={"left": ["right"]},
        )

    assert any(
        violation.reason == "not_run_skill" and "untyped" in violation.detail
        for violation in raised.value.violations
    )


def test_admission_normalizes_empty_and_duplicate_prerequisites():
    empty = _initialization(dependencies={"verify": []})
    duplicate = _initialization(dependencies={"verify": ["plan", "plan"]})

    assert dict(empty.dependencies) == {}
    assert dict(duplicate.dependencies) == {"verify": ("plan",)}


def test_admission_allows_valid_and_untyped_steps_without_edges():
    accepted = _initialization(dependencies={"verify": ["plan"]})
    untyped = _initialization(recipe_steps={"opaque": {}})

    assert dict(accepted.dependencies) == {"verify": ("plan",)}
    assert dict(untyped.dependencies) == {}


def test_tracker_data_and_admitted_inputs_are_snapshots():
    recipe_steps = _steps()
    prerequisites = ["plan"]
    dependencies = {"verify": prerequisites}
    skipped = {"plan"}
    initialization = _initialization(
        recipe_steps=recipe_steps,
        dependencies=dependencies,
        skipped=skipped,
    )

    recipe_steps["later"] = FinalizedRecipeStep(name="later", tool="run_skill")
    del recipe_steps["plan"]
    prerequisites[0] = "rectify"
    dependencies["rectify"] = ["review_approach"]
    skipped.clear()
    skipped.add("verify")
    data = initialization.tracker_data(pipeline_id="p")

    assert isinstance(data["tracker_incarnation_id"], str)
    assert data["tracker_incarnation_id"]
    assert data["pipeline_id"] == "p"
    assert data["kitchen_id"] == "kitchen-1"
    assert data["initialized_at"]
    assert data["steps"] == {
        name: {"status": "skipped" if name == "plan" else "pending"} for name in _steps()
    }
    assert data["dependencies"] == {"verify": ["plan"]}


@pytest.mark.parametrize(
    ("initializer", "owner_kind"),
    [(initialize_manual_tracker, "manual"), (initialize_kitchen_tracker, "kitchen")],
)
def test_tracker_writers_require_admitted_initialization(tmp_path, initializer, owner_kind):
    target = _target(tmp_path, "p", expected=False)
    lease = retain_tracker_lease({}, _key(target, tmp_path, owner_kind))
    try:
        with pytest.raises(TypeError):
            initializer(target, lease, _tracker())
        assert not target.path.exists()
    finally:
        lease.close()


def test_kitchen_reinitialization_preserves_identity_and_progress(tmp_path):
    target = _target(tmp_path, "p", expected=False)
    lease = retain_tracker_lease({}, _key(target, tmp_path, "kitchen"))
    first = _initialization(kitchen_id="p", dependencies={"verify": ["plan"]})
    second = _initialization(kitchen_id="p", dependencies={"rectify": ["verify"]})
    try:
        created = initialize_kitchen_tracker(target, lease, first)

        def complete_plan(data):
            data["steps"]["plan"]["status"] = "complete"
            return data

        mutate_tracker(target, lease, complete_plan)
        refreshed = initialize_kitchen_tracker(target, lease, second)
    finally:
        lease.close()

    assert created.data is not None
    assert refreshed.data is not None
    assert refreshed.data["tracker_incarnation_id"] == created.data["tracker_incarnation_id"]
    assert refreshed.data["steps"]["plan"] == {"status": "complete"}
    assert refreshed.data["dependencies"] == {"rectify": ["verify"]}


def test_kitchen_reinitialization_adds_identity_to_legacy_tracker(tmp_path):
    target = _target(tmp_path, "p", expected=False)
    lease = retain_tracker_lease({}, _key(target, tmp_path, "kitchen"))
    legacy = _tracker()
    legacy["pipeline_id"] = "p"
    legacy["kitchen_id"] = "p"
    target.path.parent.mkdir(parents=True, exist_ok=True)
    target.path.write_text(json.dumps(legacy))
    initialization = _initialization(
        kitchen_id="p",
        recipe_steps={"prepare": FinalizedRecipeStep(name="prepare", tool="run_skill")},
    )
    try:
        result = initialize_kitchen_tracker(target, lease, initialization)
    finally:
        lease.close()

    assert result.data is not None
    assert isinstance(result.data.get("tracker_incarnation_id"), str)
    assert result.data["tracker_incarnation_id"]


def test_kitchen_initialization_rejects_a_different_target_id(tmp_path):
    target = _target(tmp_path, "p", expected=False)
    lease = retain_tracker_lease({}, _key(target, tmp_path, "kitchen"))
    try:
        with pytest.raises(ValueError):
            initialize_kitchen_tracker(
                target,
                lease,
                _initialization(kitchen_id="other-kitchen"),
            )
        assert not target.path.exists()
    finally:
        lease.close()


def test_mutation_cannot_change_dependencies(tmp_path):
    target = _target(tmp_path, "p", expected=False)
    lease = retain_tracker_lease({}, _key(target, tmp_path, "kitchen"))
    initialization = _initialization(kitchen_id="p", dependencies={"verify": ["plan"]})
    try:
        initialize_kitchen_tracker(target, lease, initialization)
        original = target.path.read_bytes()

        def change_dependencies(data):
            data["dependencies"] = {"rectify": ["verify"]}
            return data

        with pytest.raises(ValueError):
            mutate_tracker(target, lease, change_dependencies)
        assert target.path.read_bytes() == original
    finally:
        lease.close()


def test_expected_absent_tracker_error_guides_initialization(tmp_path):
    target = _target(tmp_path, "missing-pipeline", expected=True)
    lease = retain_tracker_lease({}, _key(target, tmp_path))
    try:
        result = read_tracker_authority(target, lease)
    finally:
        lease.close()

    assert result.error is not None
    assert "record_pipeline_step(op='init'" in result.error
    assert "missing-pipeline" in result.error


def test_tracker_enforced_steps_are_skill_tools_only():
    assert SKILL_TOOLS
    for tool in SKILL_TOOLS:
        assert is_tracker_enforced_step(FinalizedRecipeStep(name="skill", tool=tool))
    assert not is_tracker_enforced_step(FinalizedRecipeStep(name="clone", tool="bootstrap_clone"))
    assert not is_tracker_enforced_step(FinalizedRecipeStep(name="python", tool="run_python"))
    assert not is_tracker_enforced_step(FinalizedRecipeStep(name="done", action="stop"))
    assert not is_tracker_enforced_step({"tool": "run_skill"})
    assert not is_tracker_enforced_step(None)
