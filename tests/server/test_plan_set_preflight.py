"""Server plan-set preflight binds verified evidence into a child invocation."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from autoskillit.core import (
    PlanSetBindingMode,
    PlanSetBindRequest,
    PlanSetPreflightRequest,
    PlanSetRejectReason,
    PreflightKind,
    RecipeExecutionId,
)
from autoskillit.server._plan_set_materializer import DefaultPlanSetMaterializer
from autoskillit.server.recipe._plan_set_preflight import DefaultPlanSetPreflightResolver
from autoskillit.server.recipe._recipe_execution import (
    RecipeExecutionAdmissionError,
    build_bound_child_prompt,
    resolve_attested_input_preflight,
)
from tests.server.test_plan_set_materializer import _FIXTURES, _GitHub

pytestmark = [pytest.mark.layer("server"), pytest.mark.anyio, pytest.mark.medium]


async def _authority(
    tmp_path: Path, *, sealed: bool = True
) -> tuple[str, Path, DefaultPlanSetMaterializer]:
    fixture = _FIXTURES / "split_r1_r2"
    parts = (tmp_path / "part_a.md", tmp_path / "part_b.md")
    for part in parts:
        part.write_text((fixture / part.name).read_text(), encoding="utf-8")
    materializer = DefaultPlanSetMaterializer(_GitHub())
    bound = await materializer.bind(
        PlanSetBindRequest(
            plan_parts_raw="\n".join(str(part) for part in parts),
            allowed_root=tmp_path,
            issue_url="owner/repo#1",
            parent_authority_path="",
            seal=sealed,
            step_name="bind",
            execution_generation="generation",
            kitchen_id="kitchen",
            dispatch_id="",
            binding_mode=PlanSetBindingMode.RECIPE,
        )
    )
    assert bound.success
    return bound.plan_set_authority_path, parts[0], materializer


async def test_sealed_resolver_and_prompt_emit_assigned_subset(tmp_path: Path) -> None:
    assert PreflightKind("plan_set_coverage") is PreflightKind.PLAN_SET_COVERAGE
    path, part, _ = await _authority(tmp_path)
    resolver = DefaultPlanSetPreflightResolver(RecipeExecutionId("generation"), "kitchen")
    verified = resolver.resolve(
        PlanSetPreflightRequest(
            execution_generation="generation",
            expected_kitchen_id="kitchen",
            step_name="verify",
            skill_name="dry-walkthrough",
            plan_path=str(part),
            plan_set_authority_path=path,
        ),
        allowed_root=tmp_path,
    )
    assert verified.accepted and verified.evidence is not None
    prompt = build_bound_child_prompt(
        "/autoskillit:dry-walkthrough",
        (("plan_path", str(part)),),
        None,
        plan_set_preflight=verified.evidence,
    )
    payload = json.loads(prompt.split("AUTOSKILLIT_BOUND_INVOCATION_V1\n", 1)[1])
    evidence = payload["verified_plan_set_preflight"]
    assert evidence["status"] == "admitted"
    assert [row["requirement_id"] for row in evidence["assigned_requirements"]] == ["R1"]


async def test_unsealed_authority_is_rejected(tmp_path: Path) -> None:
    path, part, _ = await _authority(tmp_path, sealed=False)
    result = DefaultPlanSetPreflightResolver(RecipeExecutionId("generation"), "kitchen").resolve(
        PlanSetPreflightRequest(
            execution_generation="generation",
            expected_kitchen_id="kitchen",
            step_name="verify",
            skill_name="dry-walkthrough",
            plan_path=str(part),
            plan_set_authority_path=path,
        ),
        allowed_root=tmp_path,
    )
    assert result.reason is PlanSetRejectReason.AUTHORITY_NOT_SEALED


async def test_absent_authority_omits_evidence_and_kitchen_error_is_namespaced(
    tmp_path: Path,
) -> None:
    path, part, materializer = await _authority(tmp_path)
    resolver = DefaultPlanSetPreflightResolver(RecipeExecutionId("generation"), "kitchen")
    tool_ctx = SimpleNamespace(
        kitchen_id="other-kitchen",
        plan_set_materializer=materializer,
        skill_contract_resolver=lambda _command: SimpleNamespace(
            input_preflight=("plan_set_coverage",)
        ),
    )
    installed = SimpleNamespace(plan_set_preflight_resolver=resolver)
    template = SimpleNamespace(invocation=SimpleNamespace(skill_name="dry-walkthrough"))
    bound = (("plan_path", str(part)), ("plan_set_authority_path", ""))
    result = resolve_attested_input_preflight(
        tool_ctx,
        installed,
        skill_command="/autoskillit:dry-walkthrough",
        execution_id="generation",
        step_name="verify",
        template=template,
        bound_inputs=bound,
        allowed_root=tmp_path,
    )
    assert result.plan_set is None
    with pytest.raises(RecipeExecutionAdmissionError) as exc_info:
        resolve_attested_input_preflight(
            tool_ctx,
            installed,
            skill_command="/autoskillit:dry-walkthrough",
            execution_id="generation",
            step_name="verify",
            template=template,
            bound_inputs=(("plan_path", str(part)), ("plan_set_authority_path", path)),
            allowed_root=tmp_path,
        )
    assert exc_info.value.code == "input_preflight_plan_set_kitchen_mismatch"


def test_plan_only_preflight_uses_issued_root_without_invocation_root(tmp_path: Path) -> None:
    issued_root = tmp_path / "producer"
    issued_digest = "sha256:issued"
    seen_roots: list[Path] = []

    def resolve(_request, *, allowed_root):
        seen_roots.append(allowed_root)
        return SimpleNamespace(
            accepted=True,
            evidence=SimpleNamespace(plan_set_authority_digest=issued_digest),
        )

    resolved = resolve_attested_input_preflight(
        SimpleNamespace(
            kitchen_id="kitchen",
            plan_set_materializer=SimpleNamespace(
                lookup_binding=lambda execution_id, authority_path: (
                    issued_root,
                    issued_digest,
                )
            ),
            skill_contract_resolver=lambda _: SimpleNamespace(
                input_preflight=("plan_set_coverage",)
            ),
        ),
        SimpleNamespace(plan_set_preflight_resolver=SimpleNamespace(resolve=resolve)),
        skill_command="/autoskillit:dry-walkthrough",
        execution_id="generation",
        step_name="verify",
        template=SimpleNamespace(invocation=SimpleNamespace(skill_name="dry-walkthrough")),
        bound_inputs=(("plan_path", "plan.md"), ("plan_set_authority_path", "authority.json")),
        allowed_root=None,
    )

    assert resolved.plan_set is not None
    assert seen_roots == [issued_root]


def test_accepted_plan_set_verification_without_evidence_fails_closed(tmp_path: Path) -> None:
    digest = "sha256:issued"
    with pytest.raises(RecipeExecutionAdmissionError) as exc_info:
        resolve_attested_input_preflight(
            SimpleNamespace(
                kitchen_id="kitchen",
                plan_set_materializer=SimpleNamespace(
                    lookup_binding=lambda execution_id, authority_path: (tmp_path, digest)
                ),
                skill_contract_resolver=lambda _: SimpleNamespace(
                    input_preflight=("plan_set_coverage",)
                ),
            ),
            SimpleNamespace(
                plan_set_preflight_resolver=SimpleNamespace(
                    resolve=lambda request, *, allowed_root: SimpleNamespace(
                        accepted=True,
                        evidence=None,
                    )
                )
            ),
            skill_command="/autoskillit:dry-walkthrough",
            execution_id="generation",
            step_name="verify",
            template=SimpleNamespace(invocation=SimpleNamespace(skill_name="dry-walkthrough")),
            bound_inputs=(
                ("plan_path", "plan.md"),
                ("plan_set_authority_path", "authority.json"),
            ),
            allowed_root=None,
        )

    assert exc_info.value.code == "recipe_execution_preflight_unavailable"


async def _publication_authority(tmp_path: Path, *, sealed: bool = True):
    from tests.server._plan_set_test_helpers import PlanSetGitHub

    fixture = _FIXTURES / "publication_pr"
    part = tmp_path / "part.md"
    part.write_text((fixture / "part_a.md").read_text())
    materializer = DefaultPlanSetMaterializer(PlanSetGitHub(fixture_dir=fixture))
    bound = await materializer.bind(
        PlanSetBindRequest(
            plan_parts_raw=str(part),
            allowed_root=tmp_path,
            issue_url="owner/repo#1",
            parent_authority_path="",
            seal=sealed,
            step_name="bind",
            execution_generation="generation",
            kitchen_id="kitchen",
            dispatch_id="",
            binding_mode=PlanSetBindingMode.RECIPE,
            open_pr=True,
            auto_merge=False,
        )
    )
    assert bound.success, bound
    return bound, part, materializer


def _audit_preflight(
    tmp_path,
    path,
    materializer=None,
    *,
    kitchen="kitchen",
    generation="generation",
):
    return resolve_attested_input_preflight(
        SimpleNamespace(
            kitchen_id=kitchen,
            plan_set_materializer=materializer,
            skill_contract_resolver=lambda _: SimpleNamespace(
                input_preflight=("plan_set_stage_obligations",)
            ),
        ),
        SimpleNamespace(
            plan_set_preflight_resolver=DefaultPlanSetPreflightResolver(
                RecipeExecutionId(generation), kitchen
            )
        ),
        skill_command="/autoskillit:audit-impl",
        execution_id=generation,
        step_name="audit_impl",
        template=SimpleNamespace(invocation=SimpleNamespace(skill_name="audit-impl")),
        bound_inputs=(("plan_set_authority_path", path),),
        allowed_root=tmp_path,
    )


async def test_audit_scope_has_stage_outcomes_while_implementer_scope_has_only_code(tmp_path):
    bound, part, materializer = await _publication_authority(tmp_path)
    evidence = _audit_preflight(tmp_path, bound.plan_set_authority_path, materializer).plan_set
    assert evidence is not None
    assert evidence.part_key is None
    assert evidence.assigned_requirements == ()
    assert evidence.to_dict()["stage_obligations"] == [
        {"requirement_id": "R3", "obligation": "pr_opened"},
        {"requirement_id": "R4", "obligation": "pr_left_unmerged"},
    ]
    implementation = DefaultPlanSetPreflightResolver(
        RecipeExecutionId("generation"), "kitchen"
    ).resolve(
        PlanSetPreflightRequest(
            execution_generation="generation",
            expected_kitchen_id="kitchen",
            step_name="implement",
            skill_name="implement-worktree-no-merge",
            plan_path=str(part),
            plan_set_authority_path=bound.plan_set_authority_path,
        ),
        allowed_root=tmp_path,
    )
    assert implementation.accepted and implementation.evidence is not None
    assert [r.requirement_id for r in implementation.evidence.assigned_requirements] == [
        "R1",
        "R2",
    ]
    assert "stage_obligations" not in implementation.evidence.to_dict()
    part.write_text(part.read_text() + "\nImplementation changed this part.\n")
    assert (
        _audit_preflight(tmp_path, bound.plan_set_authority_path, materializer).plan_set
        == evidence
    )


def test_audit_without_authority_preserves_existing_scope(tmp_path):
    assert _audit_preflight(tmp_path, "").plan_set is None


@pytest.mark.parametrize(
    ("sealed", "kitchen", "generation", "reason"),
    [
        (False, "kitchen", "generation", "not_sealed"),
        (True, "foreign", "generation", "kitchen_mismatch"),
        (True, "kitchen", "foreign", "execution_mismatch"),
    ],
)
async def test_audit_rejects_foreign_or_unsealed_authority(
    tmp_path, sealed, kitchen, generation, reason
):
    bound, _, materializer = await _publication_authority(tmp_path, sealed=sealed)
    with pytest.raises(RecipeExecutionAdmissionError) as exc_info:
        _audit_preflight(
            tmp_path,
            bound.plan_set_authority_path,
            materializer,
            kitchen=kitchen,
            generation=generation,
        )
    expected = (
        "recipe_execution_preflight_unavailable"
        if generation != "generation"
        else f"input_preflight_plan_set_{reason}"
    )
    assert exc_info.value.code == expected


def test_audit_contract_declares_authenticated_stage_scope():
    from autoskillit.core import load_yaml

    manifest = load_yaml(
        Path(__file__).resolve().parents[2] / "src/autoskillit/recipe/skill_contracts.yaml"
    )
    contract = manifest["skills"]["audit-impl"]
    assert contract["input_preflight"] == ["plan_set_stage_obligations"]
    authority_input = next(i for i in contract["inputs"] if i["name"] == "plan_set_authority_path")
    assert authority_input == {
        "name": "plan_set_authority_path",
        "type": "file_path",
        "required": False,
        "absence_value": "",
    }
