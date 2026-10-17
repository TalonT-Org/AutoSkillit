"""Binding a declared child write to the canonical invocation input."""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
import structlog.testing

from autoskillit.core import WriteBehaviorSpec
from autoskillit.recipe.contracts import get_skill_contract, load_bundled_manifest
from autoskillit.server.tools._execution_helpers import (
    deserialize_skill_contract,
    resolve_skill_dispatch_metadata,
    serialize_skill_contract,
)
from autoskillit.server.tools.tools_execution._run_skill_session import (
    _bind_delegated_target,
)

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def _binding_state(cwd: Path, bound_inputs: tuple[tuple[str, object], ...]) -> SimpleNamespace:
    return SimpleNamespace(
        skill_command="/autoskillit:dry-walkthrough",
        _skill_contract=SimpleNamespace(delegated_write_input="plan_path"),
        _stored_contract=None,
        write_spec=WriteBehaviorSpec(mode="always"),
        _bound_recipe_inputs=bound_inputs,
        cwd=str(cwd),
    )


@pytest.mark.parametrize("path_form", ["relative", "absolute", "ancestor_symlink"])
def test_binder_captures_in_scope_plan_digest(tmp_path: Path, path_form: str) -> None:
    root = tmp_path / "root"
    nested = root / "nested"
    nested.mkdir(parents=True)
    plan = nested / "plan.md"
    plan.write_text("plan before dispatch", encoding="utf-8")
    (root / "alias").symlink_to(nested, target_is_directory=True)
    input_path = {
        "relative": "nested/plan.md",
        "absolute": str(plan),
        "ancestor_symlink": "alias/plan.md",
    }[path_form]

    state = _binding_state(root, (("plan_path", input_path),))
    _bind_delegated_target(state)

    target = state.write_spec.delegated_target
    assert target.path == str(plan.resolve())
    assert target.baseline_digest == hashlib.sha256(plan.read_bytes()).hexdigest()


@pytest.mark.parametrize(
    "case",
    ["absent", "empty", "non_string", "outside", "null_byte", "final_symlink"],
)
def test_invalid_target_stays_unbound_and_logs_reason(tmp_path: Path, case: str) -> None:
    root = tmp_path / "root"
    root.mkdir()
    plan = root / "plan.md"
    plan.write_text("plan", encoding="utf-8")
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    (root / "link.md").symlink_to(plan)
    inputs: dict[str, tuple[tuple[str, object], ...]] = {
        "absent": (),
        "empty": (("plan_path", ""),),
        "non_string": (("plan_path", 42),),
        "outside": (("plan_path", str(outside)),),
        "null_byte": (("plan_path", "bad\x00path"),),
        "final_symlink": (("plan_path", "link.md"),),
    }
    state = _binding_state(root, inputs[case])

    with structlog.testing.capture_logs() as events:
        _bind_delegated_target(state)

    assert state.write_spec.delegated_target is None
    assert any(event.get("event") == "delegated_write_target_unbound" for event in events)


def test_contract_without_declaration_leaves_write_spec_unchanged(tmp_path: Path) -> None:
    state = _binding_state(tmp_path, (("plan_path", "plan.md"),))
    state._skill_contract = SimpleNamespace(delegated_write_input=None)
    original_spec = state.write_spec

    _bind_delegated_target(state)

    assert state.write_spec is original_spec


def _restored_state(
    cwd: Path,
    skill_contract: object,
    write_spec: WriteBehaviorSpec,
) -> SimpleNamespace:
    stored_contract = SimpleNamespace(
        expected_output_patterns=[],
        write_behavior=write_spec,
        skill_contract_json=serialize_skill_contract(skill_contract),
    )
    _, restored_spec, restored_contract = resolve_skill_dispatch_metadata(
        None, "/autoskillit:dry-walkthrough", stored_contract
    )
    return SimpleNamespace(
        _skill_contract=restored_contract,
        _stored_contract=stored_contract,
        write_spec=restored_spec,
        _bound_recipe_inputs=(("plan_path", "plan.md"),),
        cwd=str(cwd),
    )


def test_resume_keeps_original_target_baseline_without_rebinding(tmp_path: Path) -> None:
    from autoskillit.core import DelegatedWriteTarget

    plan = tmp_path / "plan.md"
    plan.write_text("original", encoding="utf-8")
    original_target = DelegatedWriteTarget.bind(str(plan))
    plan.write_text("changed after binding", encoding="utf-8")
    contract = get_skill_contract("dry-walkthrough", load_bundled_manifest())
    assert contract is not None
    state = _restored_state(
        tmp_path,
        contract,
        WriteBehaviorSpec(mode="always", delegated_target=original_target),
    )

    _bind_delegated_target(state)

    restored_target = state.write_spec.delegated_target
    assert restored_target.baseline_digest == original_target.baseline_digest
    assert restored_target.changed()


def test_resume_without_target_stays_unbound_without_warning(tmp_path: Path) -> None:
    contract = get_skill_contract("dry-walkthrough", load_bundled_manifest())
    assert contract is not None
    state = _restored_state(tmp_path, contract, WriteBehaviorSpec(mode="always"))

    with structlog.testing.capture_logs() as events:
        _bind_delegated_target(state)

    assert state.write_spec.delegated_target is None
    assert not any(event.get("event") == "delegated_write_target_unbound" for event in events)


@pytest.mark.anyio
@pytest.mark.parametrize("structured", [True, False])
async def test_run_skill_binds_structured_and_positional_plan_inputs(
    tool_ctx_kitchen_open,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    structured: bool,
) -> None:
    from autoskillit.server.tools.tools_execution import run_skill
    from tests.fakes import InMemoryHeadlessExecutor

    plan = tmp_path / "plan.md"
    plan.write_text("plan", encoding="utf-8")
    executor = InMemoryHeadlessExecutor()
    monkeypatch.setattr(tool_ctx_kitchen_open, "executor", executor)
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)
    skill_command = (
        "/autoskillit:dry-walkthrough" if structured else f"/autoskillit:dry-walkthrough {plan}"
    )
    skill_inputs = {"plan_path": str(plan)} if structured else None

    await run_skill(skill_command, str(tmp_path), skill_inputs=skill_inputs)

    assert len(executor.calls) == 1
    target = executor.calls[0].write_behavior.delegated_target
    assert target.path == str(plan.resolve())


def test_serialized_skill_contract_preserves_delegated_write_input() -> None:
    contract = get_skill_contract("dry-walkthrough", load_bundled_manifest())
    assert contract is not None

    restored = deserialize_skill_contract(serialize_skill_contract(contract))

    assert restored is not None
    assert restored.delegated_write_input == "plan_path"
