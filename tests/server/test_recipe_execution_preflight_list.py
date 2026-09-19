"""Ordered recipe preflights stop before dispatch at the first rejection."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from autoskillit.core import AdmissionStatus
from autoskillit.server.recipe._recipe_execution import (
    RecipeExecutionAdmissionError,
    resolve_attested_input_preflight,
)

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


class _AuditResolver:
    def __init__(self, calls: list[str], status: AdmissionStatus) -> None:
        self.calls = calls
        self.status = status

    def resolve(self, _request: object, *, allowed_root: Path):
        self.calls.append("audit")
        return SimpleNamespace(
            decision=SimpleNamespace(
                status=self.status,
                reason=SimpleNamespace(value="inventory_rejected"),
                details=("inventory rejected",),
            ),
            evidence=(),
        )


class _PlanSetResolver:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def resolve(self, _request: object, *, allowed_root: Path):
        self.calls.append("plan_set")
        return SimpleNamespace(accepted=True, evidence=SimpleNamespace(to_dict=lambda: {}))


@pytest.mark.parametrize(
    ("status", "expected_calls"),
    [
        (AdmissionStatus.REJECT, ["audit"]),
        (AdmissionStatus.PASS, ["audit", "plan_set"]),
    ],
)
def test_preflights_run_in_contract_order(
    tmp_path: Path, status: AdmissionStatus, expected_calls: list[str]
) -> None:
    calls: list[str] = []
    context = SimpleNamespace(
        kitchen_id="kitchen",
        skill_contract_resolver=lambda _command: SimpleNamespace(
            input_preflight=("audit_cycle_inventory", "plan_set_coverage")
        ),
    )
    installed = SimpleNamespace(
        input_preflight_resolver=_AuditResolver(calls, status),
        plan_set_preflight_resolver=_PlanSetResolver(calls),
    )
    template = SimpleNamespace(invocation=SimpleNamespace(skill_name="dry-walkthrough"))
    kwargs = dict(
        skill_command="/autoskillit:dry-walkthrough",
        execution_id="generation",
        step_name="verify",
        template=template,
        bound_inputs=(
            ("plan_path", str(tmp_path / "plan.md")),
            ("plan_set_authority_path", "authority.json"),
        ),
        allowed_root=tmp_path,
    )
    if status is AdmissionStatus.REJECT:
        with pytest.raises(RecipeExecutionAdmissionError, match="inventory rejected"):
            resolve_attested_input_preflight(context, installed, **kwargs)
    else:
        resolved = resolve_attested_input_preflight(context, installed, **kwargs)
        assert resolved.audit is not None and resolved.plan_set is not None
    assert calls == expected_calls
