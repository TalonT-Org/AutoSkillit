"""Test-side oracle for caller-built ``run_skill`` arguments.

Models the delivered ``with:`` block, the ``recipe_execution`` credential,
and the sous-chef's forwarding rules. It must never derive actual values from
the compiled template.
"""

from __future__ import annotations

from collections.abc import Mapping
from inspect import Parameter, signature

from autoskillit.core import BoundScalar
from autoskillit.server.tools import tools_execution


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
