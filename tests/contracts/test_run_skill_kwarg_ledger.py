"""run_skill param/role ledger — change-visibility forcing function (#4402).

A frozen ``(param_name, role)`` table, diffed bidirectionally against the
live ``get_tool_def("run_skill").params`` registry. A parameter added,
removed, or re-roled without a matching edit here fails CI, naming the
drifted entry.

This intentionally overlaps ``test_tool_param_roles.py`` (T2): that module
proves internal consistency (the gate's admission set derives correctly from
role); this ledger proves *change visibility* — the same
frozen-review-artifact discipline ``test_config_key_ledger.py`` established
for ``_CONFIG_SCHEMA`` (issue #4303), applied to ``ToolParamRole``. Unlike
that ledger, this one is a name -> classification mapping whose values carry
structure, so an inline Python dict is the honest representation rather than
an external sorted ``.txt`` file — the sortedness/dedup discipline is
preserved by asserting the dict's key order directly.
"""

from __future__ import annotations

import pytest

from autoskillit.core import ToolParamRole, get_tool_def

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]

# Frozen (param_name -> role) ledger. Keys MUST stay sorted — enforced by
# test_ledger_is_sorted() below — so a diff always shows exactly the entry
# that changed.
RUN_SKILL_PARAM_ROLE_LEDGER: dict[str, ToolParamRole] = {
    "closure_authority_hash": ToolParamRole.SESSION_FLOW,
    "closure_authority_path": ToolParamRole.SESSION_FLOW,
    "closure_base_sha": ToolParamRole.SESSION_FLOW,
    "closure_diff_sha": ToolParamRole.SESSION_FLOW,
    "closure_plan_paths": ToolParamRole.SESSION_FLOW,
    "closure_target_sha": ToolParamRole.SESSION_FLOW,
    "cwd": ToolParamRole.CHILD_INPUT,
    "dispatch_items": ToolParamRole.SESSION_FLOW,
    "idle_output_timeout": ToolParamRole.EXECUTION_TUNING,
    "invocation_template_digest": ToolParamRole.PROTOCOL,
    "model": ToolParamRole.EXECUTION_TUNING,
    "native_shell_capture_mode": ToolParamRole.SESSION_FLOW,
    "order_id": ToolParamRole.ORCHESTRATOR_SCOPING,
    "output_dir": ToolParamRole.CHILD_INPUT,
    "recipe_execution_id": ToolParamRole.PROTOCOL,
    "resume_session_id": ToolParamRole.SESSION_FLOW,
    "retry_after_audit_attempt_id": ToolParamRole.SESSION_FLOW,
    "skill_command": ToolParamRole.CHILD_INPUT,
    "skill_inputs": ToolParamRole.CHILD_INPUT,
    "stale_threshold": ToolParamRole.EXECUTION_TUNING,
    "step_name": ToolParamRole.PROTOCOL,
    "step_provider": ToolParamRole.EXECUTION_TUNING,
}


def _live_run_skill_roles() -> dict[str, ToolParamRole]:
    tool_def = get_tool_def("run_skill")
    assert tool_def is not None, "run_skill must be a registered ToolDef"
    return {param.name: param.role for param in tool_def.params}


def test_ledger_is_sorted() -> None:
    keys = list(RUN_SKILL_PARAM_ROLE_LEDGER)
    assert keys == sorted(keys), (
        "RUN_SKILL_PARAM_ROLE_LEDGER keys must stay sorted so a diff always "
        "isolates exactly the entry that changed."
    )


def test_no_silent_param_additions() -> None:
    """Every live run_skill param must have a ledger entry."""
    live = _live_run_skill_roles()
    missing = sorted(set(live) - set(RUN_SKILL_PARAM_ROLE_LEDGER))
    assert not missing, (
        f"run_skill has params not present in RUN_SKILL_PARAM_ROLE_LEDGER: {missing}. "
        "Add a (name -> role) entry for each — see ToolParamRole in "
        "core/types/_type_recipe_binding.py for what each role means."
    )


def test_no_silent_param_removals() -> None:
    """Every ledger entry must name a live run_skill param."""
    live = _live_run_skill_roles()
    stale = sorted(set(RUN_SKILL_PARAM_ROLE_LEDGER) - set(live))
    assert not stale, (
        f"RUN_SKILL_PARAM_ROLE_LEDGER has entries for params run_skill no longer "
        f"declares: {stale}. Remove these entries."
    )


def test_no_silent_reroles() -> None:
    """A param's role in the ledger must match its live role exactly."""
    live = _live_run_skill_roles()
    drifted = sorted(
        name
        for name, ledger_role in RUN_SKILL_PARAM_ROLE_LEDGER.items()
        if name in live and live[name] is not ledger_role
    )
    assert not drifted, (
        f"run_skill param(s) re-roled without a matching ledger edit: {drifted}. "
        f"Live roles: {[(n, live[n].value) for n in drifted]!r}. "
        "Update RUN_SKILL_PARAM_ROLE_LEDGER to match, and confirm the re-role "
        "was intentional — a role change alters gate admission behavior."
    )
