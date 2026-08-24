"""Generated parameter-forwarding rules, not hand-restated (#4707, S10).

``build_parameter_forwarding_rules`` (core/tool_registry.py) is the single
source for which ``run_skill`` parameters may be forwarded from a step's
``with:`` block — wired into ``_build_orchestration_rules``
(recipe/_api.py) so every orchestrator-instruction delivery path shares one
generator instead of a hand-copied sentence per surface, the #4707 failure
mode (a tool docstring and a bootstrap skill both hand-maintained the same
rule and drifted).
"""

from __future__ import annotations

import pytest

from autoskillit.core import (
    EXECUTION_TUNING_EXTERNALLY_RESOLVED,
    EXECUTION_TUNING_STEP_FIELDS,
    ToolParamRole,
    build_parameter_forwarding_rules,
    get_tool_def,
)
from tests._helpers import (
    execution_tuning_param_names,
    find_execution_tuning_forwarding_violations,
)

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _execution_tuning_field_map() -> dict[str, str]:
    return {**EXECUTION_TUNING_STEP_FIELDS, **EXECUTION_TUNING_EXTERNALLY_RESOLVED}


def _live_tuning_param_names() -> list[str]:
    tool_def = get_tool_def("run_skill")
    assert tool_def is not None, "run_skill must be a registered ToolDef"
    return [
        param.name for param in tool_def.params if param.role is ToolParamRole.EXECUTION_TUNING
    ]


def test_rules_name_every_execution_tuning_param_and_its_step_field() -> None:
    """Every EXECUTION_TUNING param and its mapped RecipeStep field appear in
    the generated text — derived from the live role registry, not a literal
    list. The mapping is not identity: `step_provider` maps to the `provider`
    field, and the generator must name that field, not the parameter name."""
    tuning_params = _live_tuning_param_names()
    assert tuning_params, (
        "no EXECUTION_TUNING-role run_skill params found — has the role registry drifted?"
    )
    field_by_param = _execution_tuning_field_map()

    rules = build_parameter_forwarding_rules()
    for param_name in tuning_params:
        assert f"`{param_name}`" in rules, f"{param_name!r} missing from generated rules"
        field_name = field_by_param[param_name]
        assert f"`{field_name}:`" in rules, f"{field_name}: missing from generated rules"

    # The sharpest wrong-name trap: the field name for step_provider must be
    # `provider`, never `step_provider` — the union table's value, not its key.
    assert "`provider:`" in rules
    assert "`step_provider:`" not in rules


def test_rules_instruct_not_forwarding_them() -> None:
    rules = build_parameter_forwarding_rules()
    for param_name, field_name in _execution_tuning_field_map().items():
        instruction = (
            f"A step's `{field_name}:` field is resolved server-side; never include "
            f"`{param_name}` in a `run_skill` call for that step."
        )
        assert instruction in rules


def test_generated_rules_survive_the_prose_forwarding_sweep() -> None:
    """The generator must not emit text its own sweep would flag — without
    this, T1 and S10 can contradict each other."""
    names = execution_tuning_param_names()
    rules = build_parameter_forwarding_rules()
    # Prefix with "run_skill" so the sweep's scope-in window (does this
    # passage concern run_skill at all?) matches, mirroring how the rules
    # are actually delivered — always adjacent to run_skill-referencing text.
    violations = find_execution_tuning_forwarding_violations("run_skill " + rules, names)
    assert not violations, f"generated rules trip their own prose-forwarding sweep: {violations}"


def test_a_new_execution_tuning_param_appears_with_no_generator_edit(monkeypatch) -> None:
    """Simulate a fifth EXECUTION_TUNING param via monkeypatched registry
    state — the generator must surface it with zero code changes, proving it
    derives from the live registry rather than a hardcoded list."""
    import autoskillit.core.tool_registry as tool_registry_module
    from autoskillit.core.types._type_recipe_binding import ToolDef, ToolParamDef, ToolWireType

    real_tool_def = tool_registry_module.get_tool_def("run_skill")
    assert real_tool_def is not None
    fake_param = ToolParamDef(
        "fifth_tuning_param", ToolWireType.STRING, role=ToolParamRole.EXECUTION_TUNING
    )
    fake_tool_def = ToolDef(
        name="run_skill",
        params=(fake_param,),
        initialization_operation=real_tool_def.initialization_operation,
    )
    monkeypatch.setattr(tool_registry_module, "get_tool_def", lambda name: fake_tool_def)
    monkeypatch.setattr(
        tool_registry_module, "EXECUTION_TUNING_STEP_FIELDS", {"fifth_tuning_param": "fifth_field"}
    )
    monkeypatch.setattr(tool_registry_module, "EXECUTION_TUNING_EXTERNALLY_RESOLVED", {})

    rules = tool_registry_module.build_parameter_forwarding_rules()
    assert "`fifth_tuning_param`" in rules
    assert "`fifth_field:`" in rules
