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
        self.allowed_root: Path | None = None

    def resolve(self, _request: object, *, allowed_root: Path):
        self.calls.append("audit")
        self.allowed_root = allowed_root
        return SimpleNamespace(
            decision=SimpleNamespace(
                status=self.status,
                reason=SimpleNamespace(value="inventory_rejected"),
                details=("inventory rejected",),
            ),
            evidence=(),
        )


class _PlanSetResolver:
    def __init__(self, calls: list[str], digest: str) -> None:
        self.calls = calls
        self.digest = digest
        self.allowed_root: Path | None = None

    def resolve(self, _request: object, *, allowed_root: Path):
        self.calls.append("plan_set")
        self.allowed_root = allowed_root
        return SimpleNamespace(
            accepted=True,
            evidence=SimpleNamespace(
                plan_set_authority_digest=self.digest,
                to_dict=lambda: {},
            ),
        )


class _PlanSetMaterializer:
    def __init__(self, calls: list[str], root: Path, digest: str) -> None:
        self.calls = calls
        self.root = root
        self.digest = digest

    def lookup_binding(self, execution_id: str, authority_path: str) -> tuple[Path, str] | None:
        self.calls.append("lookup")
        assert execution_id == "generation"
        assert authority_path == "authority.json"
        return self.root, self.digest


@pytest.mark.parametrize(
    ("status", "expected_calls"),
    [
        (AdmissionStatus.REJECT, ["audit"]),
        (AdmissionStatus.PASS, ["audit", "lookup", "plan_set"]),
    ],
)
def test_preflights_run_in_contract_order(
    tmp_path: Path, status: AdmissionStatus, expected_calls: list[str]
) -> None:
    calls: list[str] = []
    invocation_root = tmp_path / "invocation"
    bind_root = tmp_path / "bind"
    digest = "sha256:issued"
    audit_resolver = _AuditResolver(calls, status)
    plan_set_resolver = _PlanSetResolver(calls, digest)
    context = SimpleNamespace(
        kitchen_id="kitchen",
        plan_set_materializer=_PlanSetMaterializer(calls, bind_root, digest),
        skill_contract_resolver=lambda _command: SimpleNamespace(
            input_preflight=("audit_cycle_inventory", "plan_set_coverage")
        ),
    )
    installed = SimpleNamespace(
        input_preflight_resolver=audit_resolver,
        plan_set_preflight_resolver=plan_set_resolver,
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
        allowed_root=invocation_root,
    )
    if status is AdmissionStatus.REJECT:
        with pytest.raises(RecipeExecutionAdmissionError, match="inventory rejected"):
            resolve_attested_input_preflight(context, installed, **kwargs)
    else:
        resolved = resolve_attested_input_preflight(context, installed, **kwargs)
        assert resolved.audit is not None and resolved.plan_set is not None
    assert calls == expected_calls
    assert audit_resolver.allowed_root == invocation_root
    if status is AdmissionStatus.PASS:
        assert plan_set_resolver.allowed_root == bind_root
