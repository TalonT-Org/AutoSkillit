"""Implementation admission binds the current part to a sealed local authority."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from autoskillit.core import (
    PlanSetBindingMode,
    PlanSetBindRequest,
    PlanSetPreflightRequest,
    PlanSetRejectReason,
    RecipeExecutionId,
    canonical_json_bytes,
    compute_canonical_hash,
    resolve_temp_dir,
)
from autoskillit.core.planset import verifier as plan_set_verifier
from autoskillit.pipeline import ReadyRecipe
from autoskillit.recipe.io import builtin_scripts_dir
from autoskillit.server._plan_set_materializer import DefaultPlanSetMaterializer
from autoskillit.server.lifecycle._guards import _check_dry_walkthrough_plan
from autoskillit.server.recipe._plan_set_preflight import DefaultPlanSetPreflightResolver
from autoskillit.server.recipe._recipe_execution import install_recipe_execution
from autoskillit.server.tools.tools_execution import run_skill
from tests.fakes import InMemoryHeadlessExecutor
from tests.server._helpers import _ready_recipe_segment_step, _skill_ok
from tests.server._pipeline_test_helpers import _write_tracker
from tests.server._plan_set_test_helpers import write_plan_set_parts
from tests.server.test_plan_set_materializer import _FIXTURES, _GitHub

pytestmark = [pytest.mark.layer("server"), pytest.mark.anyio, pytest.mark.medium]


async def _bind(
    root: Path,
    *,
    kitchen: str = "kitchen",
    generation: str = "generation",
    mode: PlanSetBindingMode = PlanSetBindingMode.RECIPE,
    seal: bool = True,
    parent: str = "",
    write_parts: bool = True,
    materializer: DefaultPlanSetMaterializer | None = None,
):
    root.mkdir(parents=True, exist_ok=True)
    fixture = _FIXTURES / "split_r1_r2"
    parts = (root / "part_a.md", root / "part_b.md")
    if write_parts:
        for part in parts:
            part.write_text((fixture / part.name).read_text(), encoding="utf-8")
    materializer = materializer or DefaultPlanSetMaterializer(_GitHub())
    bound = await materializer.bind(
        PlanSetBindRequest(
            plan_parts_raw="\n".join(str(part) for part in parts),
            allowed_root=root,
            issue_url="owner/repo#1" if not parent else "",
            parent_authority_path=parent,
            seal=seal,
            step_name="bind" if not parent else "renew",
            execution_generation=generation,
            kitchen_id=kitchen,
            dispatch_id="",
            binding_mode=mode,
        )
    )
    assert bound.success
    return bound, parts, materializer


def _consumer_args(
    step: dict[str, Any],
    credential: dict[str, Any],
    *,
    step_name: str,
    worktree: Path,
    plan: Path,
    authority_path: str,
) -> dict[str, Any]:
    with_args = step["with"]
    assert isinstance(with_args, dict)
    skill_inputs = {
        "plan_path": str(plan),
        "plan_set_authority_path": authority_path,
    }
    if step_name == "retry_worktree":
        skill_inputs["worktree_path"] = str(worktree)
    digests = credential["invocation_template_digests"]
    assert isinstance(digests, dict)
    return {
        "skill_command": with_args["skill_command"],
        "cwd": str(worktree),
        "step_name": step_name,
        "output_dir": with_args["output_dir"],
        "recipe_execution_id": credential["execution_id"],
        "invocation_template_digest": digests[step_name],
        "skill_inputs": skill_inputs,
    }


def _bound_payload(command: str) -> dict[str, Any]:
    marker = "AUTOSKILLIT_BOUND_INVOCATION_V1\n"
    assert marker in command
    payload = json.loads(command.split(marker, 1)[1])
    assert isinstance(payload, dict)
    return payload


def _create_impl_worktree(clone: Path, temp_dir: str) -> Path:
    completed = subprocess.run(
        [
            "bash",
            str(builtin_scripts_dir() / "create_impl_worktree.sh"),
            "impl-plan-set-handoff",
            temp_dir,
        ],
        cwd=clone,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    token = next(
        line for line in completed.stdout.splitlines() if line.startswith("worktree_path=")
    )
    return Path(token.split("=", 1)[1]).resolve()


def _preflight(
    authority_path: str,
    plan: Path,
    root: Path,
    *,
    kitchen: str = "kitchen",
    generation: str = "generation",
):
    resolver = DefaultPlanSetPreflightResolver(RecipeExecutionId(generation), kitchen)
    return resolver.resolve(
        PlanSetPreflightRequest(
            execution_generation=generation,
            expected_kitchen_id=kitchen,
            step_name="implement",
            skill_name="implement-worktree-no-merge",
            plan_path=str(plan),
            plan_set_authority_path=authority_path,
        ),
        allowed_root=root,
    )


async def test_unchanged_part_admits_only_its_assigned_requirements(tmp_path: Path) -> None:
    bound, parts, _ = await _bind(tmp_path)
    result = _preflight(bound.plan_set_authority_path, parts[0], tmp_path)
    assert result.accepted and result.evidence is not None
    assert [row.requirement_id for row in result.evidence.assigned_requirements] == ["R1"]


async def test_walkthrough_edit_requires_renewal(tmp_path: Path) -> None:
    bound, parts, materializer = await _bind(tmp_path)
    parts[0].write_text(
        parts[0]
        .read_text()
        .replace(
            "## Issue Requirement Allocation",
            "Walkthrough clarification for R1.\n\n## Issue Requirement Allocation",
        )
    )
    assert _preflight(bound.plan_set_authority_path, parts[0], tmp_path).reason is (
        PlanSetRejectReason.PART_CONTENT_CHANGED
    )
    renewed = await materializer.bind(
        PlanSetBindRequest(
            plan_parts_raw="\n".join(str(part) for part in parts),
            allowed_root=tmp_path,
            issue_url="",
            parent_authority_path=bound.plan_set_authority_path,
            seal=True,
            step_name="renew",
            execution_generation="generation",
            kitchen_id="kitchen",
            dispatch_id="",
            binding_mode=PlanSetBindingMode.RECIPE,
        )
    )
    assert renewed.success
    assert _preflight(renewed.plan_set_authority_path, parts[0], tmp_path).accepted


async def test_foreign_unsealed_and_standalone_authorities_never_admit(tmp_path: Path) -> None:
    bound, parts, _ = await _bind(tmp_path / "one")
    other = tmp_path / "other"
    other.mkdir()
    assert _preflight(bound.plan_set_authority_path, parts[0], other).reason is (
        PlanSetRejectReason.PATH_ESCAPE
    )
    assert _preflight(
        bound.plan_set_authority_path, parts[0], tmp_path / "one", generation="other"
    ).reason is (PlanSetRejectReason.EXECUTION_GENERATION)
    opened, open_parts, _ = await _bind(tmp_path / "open", seal=False)
    assert _preflight(opened.plan_set_authority_path, open_parts[0], tmp_path / "open").reason is (
        PlanSetRejectReason.AUTHORITY_NOT_SEALED
    )
    standalone, standalone_parts, _ = await _bind(
        tmp_path / "standalone", mode=PlanSetBindingMode.STANDALONE
    )
    assert (
        _preflight(
            standalone.plan_set_authority_path, standalone_parts[0], tmp_path / "standalone"
        ).reason
        is PlanSetRejectReason.BINDING_MODE
    )


async def test_two_kitchens_cannot_consume_each_others_authority(tmp_path: Path) -> None:
    first, parts, _ = await _bind(tmp_path, kitchen="first")
    second, _, _ = await _bind(tmp_path, kitchen="second", write_parts=False)
    assert first.plan_set_authority_id != second.plan_set_authority_id
    assert _preflight(
        first.plan_set_authority_path, parts[0], tmp_path, kitchen="second"
    ).reason is (PlanSetRejectReason.KITCHEN_ID)


async def test_resumed_execution_consumes_original_authority_without_rebinding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tool_ctx_ready_recipe
) -> None:
    ready = tool_ctx_ready_recipe
    state = ready.tool_ctx.recipe_initialization_state
    assert isinstance(state, ReadyRecipe)
    original_execution = state.installed_execution
    original_id = original_execution.snapshot.execution_id
    kitchen = ready.tool_ctx.kitchen_id
    bound, parts, _ = await _bind(tmp_path, kitchen=kitchen, generation=original_id)

    async def unexpected_bind(_request: object) -> None:
        raise AssertionError("resume must not re-bind an existing authority")

    monkeypatch.setattr(ready.tool_ctx.plan_set_materializer, "bind", unexpected_bind)
    install_recipe_execution(ready.tool_ctx, prepared_execution=original_execution)
    resumed_state = ready.tool_ctx.recipe_initialization_state
    assert isinstance(resumed_state, ReadyRecipe)
    resumed_execution = resumed_state.installed_execution
    assert resumed_execution.snapshot.execution_id == original_id
    assert resumed_execution.plan_set_preflight_resolver is not None
    admitted = resumed_execution.plan_set_preflight_resolver.resolve(
        PlanSetPreflightRequest(
            execution_generation=original_id,
            expected_kitchen_id=kitchen,
            step_name="implement",
            skill_name="implement-worktree-no-merge",
            plan_path=str(parts[0]),
            plan_set_authority_path=bound.plan_set_authority_path,
        ),
        allowed_root=tmp_path,
    )
    assert admitted.accepted


@pytest.mark.parametrize(
    "tool_ctx_ready_recipe",
    [
        (
            "implementation",
            "implement",
            {"issue_url": "https://github.com/TalonT-Org/AutoSkillit/issues/4261", "task": "test"},
        )
    ],
    indirect=True,
)
async def test_clone_authority_admits_implement_and_retry_from_fresh_sibling_worktree(
    tool_ctx_ready_recipe,
    git_checkout: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.server.tools.tools_plan_set import bind_plan_set

    ready = tool_ctx_ready_recipe
    tool_ctx = ready.tool_ctx
    _, part_a, part_b = write_plan_set_parts(git_checkout, tool_ctx, monkeypatch)
    plan_parts = f"{part_a}\n{part_b}"
    initial = json.loads(
        await bind_plan_set(
            plan_parts=plan_parts,
            cwd=str(git_checkout),
            issue_url="https://github.com/TalonT-Org/AutoSkillit/issues/4261",
            step_name="bind_plan_set",
        )
    )
    assert initial["success"] is True, initial
    part_a.write_text("Dry-walkthrough verified = TRUE\n" + part_a.read_text())
    renewed = json.loads(
        await bind_plan_set(
            plan_parts=plan_parts,
            cwd=str(git_checkout),
            parent_authority_path=initial["plan_set_authority_path"],
            step_name="renew_plan_set",
        )
    )
    assert renewed["success"] is True, renewed
    assert renewed["plan_set_authority_digest"] != initial["plan_set_authority_digest"]

    worktree = _create_impl_worktree(git_checkout, str(tool_ctx.config.workspace.temp_dir))
    worktree_temp = resolve_temp_dir(worktree, tool_ctx.config.workspace.temp_dir)
    assert not worktree_temp.exists()
    authority_path = Path(renewed["plan_set_authority_path"])
    assert part_a.is_file() and authority_path.is_file()
    common_git_dir = Path(
        subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=git_checkout,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
    )
    exclude_path = common_git_dir / "info" / "exclude"
    exclude_before = exclude_path.read_bytes()

    executor = InMemoryHeadlessExecutor(
        default_result=_skill_ok(f"worktree_path = {worktree}\nhas_implementation_progress = true")
    )
    monkeypatch.setattr(tool_ctx, "executor", executor)
    _write_tracker(
        tool_ctx.project_dir,
        "AB",
        {
            "implement": {"status": "pending"},
            "retry_worktree": {"status": "pending"},
        },
        {},
        kitchen_id=tool_ctx.kitchen_id,
    )

    for step_name in ("implement", "retry_worktree"):
        step, credential = _ready_recipe_segment_step(tool_ctx, step_name)
        before = len(executor.calls)
        response = json.loads(
            await run_skill(
                **_consumer_args(
                    step,
                    credential,
                    step_name=step_name,
                    worktree=worktree,
                    plan=part_a,
                    authority_path=str(authority_path),
                )
            )
        )
        assert response["success"] is True, response
        assert len(executor.calls) == before + 1
        call = executor.calls[-1]
        assert Path(call.cwd) == worktree
        payload = _bound_payload(call.skill_command)
        evidence = payload["verified_plan_set_preflight"]
        assert evidence["plan_set_authority_digest"] == renewed["plan_set_authority_digest"]
        assert [row["requirement_id"] for row in evidence["assigned_requirements"]] == ["R1"]
        assert exclude_path.read_bytes() == exclude_before
        assert not worktree_temp.exists()
        assert part_a.is_file() and authority_path.is_file()

    materializer = tool_ctx.plan_set_materializer
    assert materializer is not None
    execution_id = ready.receipt["recipe_execution"]["execution_id"]
    assert materializer.lookup_binding(execution_id, initial["plan_set_authority_path"]) == (
        resolve_temp_dir(git_checkout, tool_ctx.config.workspace.temp_dir).resolve(),
        initial["plan_set_authority_digest"],
    )
    assert materializer.lookup_binding(execution_id, str(authority_path)) == (
        resolve_temp_dir(git_checkout, tool_ctx.config.workspace.temp_dir).resolve(),
        renewed["plan_set_authority_digest"],
    )


@pytest.mark.parametrize("case", ["unissued", "rewritten", "other_execution"])
@pytest.mark.parametrize(
    "tool_ctx_ready_recipe",
    [
        (
            "implementation",
            "implement",
            {"issue_url": "https://github.com/TalonT-Org/AutoSkillit/issues/4261", "task": "test"},
        )
    ],
    indirect=True,
)
async def test_attested_implementation_rejects_untrusted_authority_provenance(
    case: str,
    tool_ctx_ready_recipe,
    git_checkout: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.server.tools.tools_plan_set import bind_plan_set

    ready = tool_ctx_ready_recipe
    tool_ctx = ready.tool_ctx
    root, part_a, part_b = write_plan_set_parts(git_checkout, tool_ctx, monkeypatch)
    part_a.write_text("Dry-walkthrough verified = TRUE\n" + part_a.read_text())
    execution_id = ready.receipt["recipe_execution"]["execution_id"]
    materializer = tool_ctx.plan_set_materializer
    assert isinstance(materializer, DefaultPlanSetMaterializer)

    if case == "unissued":
        foreign = DefaultPlanSetMaterializer(_GitHub())
        bound, _, _ = await _bind(
            root,
            kitchen=tool_ctx.kitchen_id,
            generation=execution_id,
            write_parts=False,
            materializer=foreign,
        )
        authority_path = bound.plan_set_authority_path
        expected_code = "input_preflight_plan_set_authority_invalid"
    elif case == "other_execution":
        bound, _, _ = await _bind(
            root,
            kitchen=tool_ctx.kitchen_id,
            generation="other-execution",
            write_parts=False,
            materializer=materializer,
        )
        authority_path = bound.plan_set_authority_path
        expected_code = "input_preflight_plan_set_authority_invalid"
    else:
        bound = json.loads(
            await bind_plan_set(
                plan_parts=f"{part_a}\n{part_b}",
                cwd=str(git_checkout),
                issue_url="https://github.com/TalonT-Org/AutoSkillit/issues/4261",
                step_name="bind_plan_set",
            )
        )
        assert bound["success"] is True, bound
        authority_path = bound["plan_set_authority_path"]
        payload = json.loads(Path(authority_path).read_text())
        payload["dispatch_id"] = "self-consistent-rewrite"
        unsigned = dict(payload)
        unsigned.pop("authority_digest")
        payload["authority_digest"] = compute_canonical_hash(
            unsigned, domain=plan_set_verifier.PLAN_SET_AUTHORITY_DOMAIN
        )
        Path(authority_path).write_bytes(canonical_json_bytes(payload))
        expected_code = "input_preflight_plan_set_authority_digest"

    executor = InMemoryHeadlessExecutor(default_result=_skill_ok("unexpected dispatch"))
    monkeypatch.setattr(tool_ctx, "executor", executor)
    _write_tracker(
        tool_ctx.project_dir,
        "AB",
        {"implement": {"status": "pending"}},
        {},
        kitchen_id=tool_ctx.kitchen_id,
    )
    step, credential = _ready_recipe_segment_step(tool_ctx, "implement")
    response = json.loads(
        await run_skill(
            **_consumer_args(
                step,
                credential,
                step_name="implement",
                worktree=git_checkout,
                plan=part_a,
                authority_path=authority_path,
            )
        )
    )
    assert expected_code in json.dumps(response)
    assert executor.calls == []


async def test_marker_gate_precedes_digest_admission(tmp_path: Path, tool_ctx) -> None:
    root = tmp_path / "make-plan"
    bound, parts, _ = await _bind(root)
    skill = next(name for name in tool_ctx.config.implement_gate.skill_names if "no-merge" in name)
    unmarked = _check_dry_walkthrough_plan(
        skill, str(tmp_path), str(parts[0]), config=tool_ctx.config
    )
    assert unmarked is not None
    assert "dry-walked" in json.loads(unmarked)["result"].lower()

    parts[0].write_text("Dry-walkthrough verified = TRUE\n" + parts[0].read_text())
    assert (
        _check_dry_walkthrough_plan(skill, str(tmp_path), str(parts[0]), config=tool_ctx.config)
        is None
    )
    assert _preflight(bound.plan_set_authority_path, parts[0], root).reason is (
        PlanSetRejectReason.PART_CONTENT_CHANGED
    )


@pytest.mark.parametrize(
    "tool_ctx_ready_recipe",
    [
        (
            "implementation",
            "implement",
            {"issue_url": "https://github.com/TalonT-Org/AutoSkillit/issues/4261", "task": "test"},
        )
    ],
    indirect=True,
)
async def test_attested_marker_precedes_plan_set_digest_preflight(
    tool_ctx_ready_recipe, git_checkout: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ready = tool_ctx_ready_recipe
    assert ready.tool_ctx.config.safety.require_dry_walkthrough
    step, credential = _ready_recipe_segment_step(ready.tool_ctx, "implement")
    work_dir = git_checkout
    allowed_root = resolve_temp_dir(work_dir, ready.tool_ctx.config.workspace.temp_dir)
    materializer = DefaultPlanSetMaterializer(_GitHub())
    monkeypatch.setattr(ready.tool_ctx, "plan_set_materializer", materializer)
    bound, parts, _ = await _bind(
        allowed_root / "make-plan",
        kitchen=ready.tool_ctx.kitchen_id,
        generation=credential["execution_id"],
        materializer=materializer,
    )
    _write_tracker(
        ready.tool_ctx.project_dir,
        "AB",
        {"implement": {"status": "pending"}},
        {},
        kitchen_id=ready.tool_ctx.kitchen_id,
    )
    with_args = step["with"]
    args = dict(
        skill_command=with_args["skill_command"],
        cwd=str(work_dir),
        step_name="implement",
        output_dir=with_args["output_dir"],
        recipe_execution_id=credential["execution_id"],
        invocation_template_digest=credential["invocation_template_digests"]["implement"],
        skill_inputs={
            "plan_path": str(parts[0]),
            "plan_set_authority_path": bound.plan_set_authority_path,
        },
    )
    unmarked = json.loads(await run_skill(**args))
    assert "dry-walked" in json.dumps(unmarked).lower()

    parts[0].write_text("Dry-walkthrough verified = TRUE\n" + parts[0].read_text())
    changed = json.loads(await run_skill(**args))
    assert "input_preflight_plan_set_part_digest_mismatch" in json.dumps(changed)
