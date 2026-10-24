"""Test-side oracle for caller-built ``run_skill`` arguments.

Models the delivered ``with:`` block, the ``recipe_execution`` credential,
and the sous-chef's forwarding rules. It must never derive actual values from
the compiled template. Replay xfails use ``raises=AdmissionDefect`` to match
the class and its subclasses; the marker alone does not check a rejection's
code or detail. Replay ``_assert_admitted`` checks those fields before raising
``AdmissionDefect``; other failures remain plain ``AssertionError`` instances.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from inspect import Parameter, signature
from typing import cast

from autoskillit.core import BoundScalar
from autoskillit.server.tools import tools_execution


class AdmissionDefect(AssertionError):
    """Raised only for a rejection matching both the pinned code and detail."""


ORCHESTRATOR_SAMPLES: Mapping[str, Mapping[str, BoundScalar]] = {
    "context": {"pr_number": 7, "review_pr_number": 7},
    "inputs": {"pr_number": 7, "review_pr_number": 7},
    "item": {
        "phase_id": "P1",
        "context_path": ".autoskillit/temp/planner/run-test/assignments/ctx_P1.json",
        "pr_number": "7",
        "complexity": "simple",
    },
}

_CONTEXT_INPUT_REF = re.compile(r"\$\{\{\s*(context|inputs)\.([^{}\s.]+)\s*\}\}")
_SINGLE_BRACE_TOKEN = re.compile(r"(?<!\{)\{([^{}]+)\}(?!\})")


def _sample_for(
    samples: Mapping[str, Mapping[str, BoundScalar]], namespace: str, name: str
) -> BoundScalar:
    """Return a pinned sample or a stable path for an otherwise unknown name."""
    values = samples.get(namespace)
    if values is not None and name in values:
        return values[name]
    return f".autoskillit/temp/sample/{name}"


def _substitute_scalar(
    value: BoundScalar, samples: Mapping[str, Mapping[str, BoundScalar]]
) -> BoundScalar:
    """Apply supported typed refs, then isolated item tokens, to one scalar."""
    if not isinstance(value, str):
        return value

    exact_ref = _CONTEXT_INPUT_REF.fullmatch(value)
    if exact_ref is not None:
        return _sample_for(samples, exact_ref.group(1), exact_ref.group(2))

    def replace_context_input(match: re.Match[str]) -> str:
        return str(_sample_for(samples, match.group(1), match.group(2)))

    substituted = _CONTEXT_INPUT_REF.sub(replace_context_input, value)

    def replace_item(match: re.Match[str]) -> str:
        return str(_sample_for(samples, "item", match.group(1).strip()))

    return _SINGLE_BRACE_TOKEN.sub(replace_item, substituted)


def _declared_skill_inputs(value: object) -> dict[str, BoundScalar]:
    """Validate the scalar map declared by ``with.skill_inputs``."""
    if not isinstance(value, Mapping):
        raise TypeError("run_skill skill_inputs must be a mapping")
    declared: dict[str, BoundScalar] = {}
    for key, raw_value in value.items():
        if not isinstance(key, str) or not isinstance(raw_value, (str, int, bool)):
            raise TypeError("run_skill skill_inputs must contain scalar string keys")
        declared[key] = raw_value
    return declared


def _skill_input_shape(
    step_key: str, credential: Mapping[str, object]
) -> tuple[list[str], Mapping[str, object]]:
    """Read ordered input keys and absence values from the caller credential."""
    shapes_value = credential.get("skill_input_shapes")
    if not isinstance(shapes_value, Mapping):
        raise TypeError("recipe_execution skill_input_shapes must be a mapping")
    shape_value = shapes_value.get(step_key)
    if not isinstance(shape_value, Mapping):
        raise AssertionError(f"step {step_key!r} has no skill input shape")
    keys_value = shape_value.get("keys")
    absence_values = shape_value.get("absence_values")
    if not isinstance(keys_value, list):
        raise TypeError(f"step {step_key!r} skill input shape keys must be strings")
    if not isinstance(absence_values, Mapping):
        raise TypeError(f"step {step_key!r} absence_values must be a mapping")
    keys: list[str] = []
    for key in keys_value:
        if not isinstance(key, str):
            raise TypeError(f"step {step_key!r} skill input shape keys must be strings")
        keys.append(key)
    return keys, absence_values


def _absence_value(step_key: str, key: str, absence_values: Mapping[str, object]) -> BoundScalar:
    """Return one advertised scalar absence value."""
    value = absence_values[key]
    if not isinstance(value, (str, int, bool)):
        raise TypeError(f"step {step_key!r} absence value for {key!r} must be scalar")
    return value


def _skill_input_payload(
    step_key: str,
    declared_value: object,
    credential: Mapping[str, object],
    samples: Mapping[str, Mapping[str, BoundScalar]],
) -> dict[str, BoundScalar]:
    """Build declared inputs in credential order with advertised absence values."""
    declared = _declared_skill_inputs(declared_value)
    keys, absence_values = _skill_input_shape(step_key, credential)
    substituted = {key: _substitute_scalar(value, samples) for key, value in declared.items()}
    result: dict[str, BoundScalar] = {}
    for key in keys:
        if key in substituted:
            result[key] = substituted[key]
        elif key in absence_values:
            result[key] = _absence_value(step_key, key, absence_values)
        else:
            raise AssertionError(f"step {step_key!r} skill input {key!r} has no caller value")
    for key, value in substituted.items():
        if key not in result:
            result[key] = value
    return result


def build_run_skill_payload(
    step_key: str,
    step_body: Mapping[str, object],
    credential: Mapping[str, object],
    *,
    cwd: str,
    samples: Mapping[str, Mapping[str, BoundScalar]] = ORCHESTRATOR_SAMPLES,
) -> dict[str, object]:
    """Build one caller payload after orchestrator placeholder substitution."""
    payload = literal_run_skill_payload(step_key, step_body, credential, cwd=cwd)
    with_values = cast(Mapping[str, object], step_body.get("with", {}))
    for name, value in with_values.items():
        if name == "skill_inputs":
            payload["skill_inputs"] = _skill_input_payload(step_key, value, credential, samples)
        elif name in payload:
            if value is None:
                payload[name] = None
            elif isinstance(value, (str, int, bool)):
                payload[name] = _substitute_scalar(value, samples)
    payload["cwd"] = cwd
    return payload


def literal_run_skill_payload(
    step_key: str,
    step_body: Mapping[str, object],
    credential: Mapping[str, object],
    *,
    cwd: str,
) -> dict[str, object]:
    """Build delivered ``with:`` kwargs verbatim, without template substitution."""
    with_value = step_body.get("with", {})
    if not isinstance(with_value, Mapping):
        raise TypeError("recipe step 'with' must be a mapping")
    with_values: Mapping[str, object] = with_value

    parameters = signature(tools_execution.run_skill).parameters
    payload: dict[str, object] = {}
    for name, value in with_values.items():
        if name in parameters and name != "skill_inputs":
            if value is not None and not isinstance(value, (str, int, bool)):
                raise TypeError(f"run_skill payload value {name!r} must be a scalar")
            payload[name] = value

    step_name_value = with_values.get("step_name", step_key)
    if not isinstance(step_name_value, str):
        raise TypeError("run_skill step_name must be a string")
    execution_id = credential.get("execution_id")
    if not isinstance(execution_id, str):
        raise TypeError("recipe_execution execution_id must be a string")
    digests_value = credential.get("invocation_template_digests")
    if not isinstance(digests_value, Mapping):
        raise TypeError("recipe_execution invocation_template_digests must be a mapping")
    digest = digests_value.get(step_key)
    if not isinstance(digest, str):
        raise TypeError("recipe_execution step digest must be a string")

    payload["step_name"] = step_name_value
    payload["recipe_execution_id"] = execution_id
    payload["invocation_template_digest"] = digest
    payload["cwd"] = cwd
    return payload


def run_skill_actual_mcp_kwargs(
    payload: Mapping[str, object],
) -> dict[str, BoundScalar]:
    """Project payload values and signature defaults through the real MCP builder."""
    parameters = signature(tools_execution.run_skill).parameters
    values: dict[str, BoundScalar | None] = {}
    for name, parameter in parameters.items():
        if name in {"ctx", "skill_inputs"}:
            continue
        if name in payload:
            value = payload[name]
        elif parameter.default is not Parameter.empty:
            value = parameter.default
        else:
            continue
        if value is None:
            values[name] = None
        elif isinstance(value, (str, int, bool)):
            values[name] = value
        else:
            raise TypeError(f"run_skill argument {name!r} must be a scalar")

    tool_def = tools_execution.get_tool_def("run_skill")
    if tool_def is None:
        raise RuntimeError("run_skill must be a registered ToolDef")
    return tools_execution._build_actual_mcp_kwargs(tool_def, values)
