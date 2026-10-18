"""Delegated child writes can satisfy a bound write target without polluting root evidence."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from structlog.testing import capture_logs

from autoskillit.core import DelegatedWriteTarget, RetryReason, WriteBehaviorSpec
from autoskillit.core.types import TerminationReason
from autoskillit.execution.backends.claude import ClaudeCodeBackend
from autoskillit.execution.headless import _build_skill_result, run_headless_core
from autoskillit.execution.headless._headless_adjudication import (
    _apply_contract_recovery_gate,
)
from autoskillit.execution.headless._headless_recovery import (
    _infer_enum_token_from_write_contract,
    _synthesize_from_write_artifacts,
)
from autoskillit.execution.session import parse_session_result
from tests.conftest import _make_result
from tests.execution.conftest import (
    _mock_backend,
    _result_ndjson,
    _sr,
    delegated_child_prompt,
    delegated_child_tool_result,
    delegated_child_tool_use,
    delegated_result_record,
    delegated_root_agent_call,
    delegated_root_agent_result,
    delegated_root_agent_text_result,
    delegated_root_text,
    delegated_stream,
    delegated_task_notification,
    delegated_task_started,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]

_BASE_PLAN = "Plan draft\n"
_PREPENDED_MARKER = "Dry-walkthrough verified = TRUE\n"


@pytest.fixture
def plan_and_target(tmp_path: Path) -> tuple[Path, DelegatedWriteTarget]:
    plan_dir = tmp_path / ".autoskillit" / "temp" / "make-plan"
    plan_dir.mkdir(parents=True)
    plan = plan_dir / "plan.md"
    plan.write_text(_BASE_PLAN)
    return plan, DelegatedWriteTarget.bind(os.path.realpath(plan))


def _edit_stream(
    file_path: str,
    result_record: str | None = None,
    *,
    tool_name: str = "Edit",
    child_result_is_error: bool | None = None,
) -> str:
    return delegated_stream(
        [
            delegated_root_agent_call(),
            delegated_task_started(),
            delegated_child_prompt(),
            delegated_child_tool_use(file_path=file_path, tool_name=tool_name),
            delegated_child_tool_result(is_error=child_result_is_error),
            delegated_task_notification(),
            delegated_root_agent_result(),
            delegated_root_text(),
            result_record or delegated_result_record(),
        ]
    )


def _bound_behavior(target: DelegatedWriteTarget | None) -> WriteBehaviorSpec:
    return WriteBehaviorSpec(mode="always", delegated_target=target)


@pytest.mark.parametrize(
    "scenario",
    ["owned", "unbound", "failed", "nul", "worktree_temp"],
    ids=["owned-success", "unbound", "failed-child", "nul-path", "worktree-temp-only"],
)
def test_collects_only_bound_successful_implementation_paths(
    tmp_path: Path, scenario: str
) -> None:
    from autoskillit.core import DelegatedOutputScope
    from autoskillit.execution.session._write_evidence import _compute_write_evidence
    from tests.execution.conftest import make_write_evidence_scope

    root = tmp_path.resolve()
    child_path = root / "plan.md"
    skill_command = "/autoskillit:prepare-pr"
    write_watch_dirs: tuple[Path, ...] = ()
    if scenario == "worktree_temp":
        child_path = root / ".autoskillit" / "temp" / "plan.md"
        skill_command = "/autoskillit:implement-worktree plan"
        write_watch_dirs = (root / "src",)
    child_path_text = str(child_path)
    if scenario == "nul":
        child_path_text += "\x00invalid"

    output_scope = None if scenario == "unbound" else DelegatedOutputScope(str(root), frozenset())
    evidence_scope = make_write_evidence_scope(
        cwd=str(root),
        skill_command=skill_command,
        write_watch_dirs=write_watch_dirs,
        delegated_output_scope=output_scope,
    )
    session = parse_session_result(
        _edit_stream(
            child_path_text,
            tool_name="Write",
            child_result_is_error=scenario == "failed",
        )
    )

    evidence = _compute_write_evidence(session, evidence_scope)

    expected = (os.path.realpath(child_path),) if scenario == "owned" else ()
    assert evidence.delegated_write_paths == expected


def test_canonical_path_rejects_nul(
    tmp_path: Path,
    plan_and_target: tuple[Path, DelegatedWriteTarget],
) -> None:
    from autoskillit.execution.session._write_evidence import _compute_write_evidence
    from tests.execution.conftest import make_write_evidence_scope

    plan, target = plan_and_target
    plan.write_text(_PREPENDED_MARKER + plan.read_text())
    child_paths = (str(plan), f"{plan}\x00invalid")
    records = [delegated_root_agent_call(), delegated_task_started(), delegated_child_prompt()]
    for index, child_path in enumerate(child_paths):
        tool_use_id = f"call_child_edit_{index}"
        records.extend(
            (
                delegated_child_tool_use(
                    tool_use_id=tool_use_id,
                    tool_name="Write",
                    file_path=child_path,
                ),
                delegated_child_tool_result(tool_use_id=tool_use_id, is_error=False),
            )
        )
    records.extend(
        (delegated_task_notification(), delegated_root_agent_result(), delegated_root_text())
    )
    records.append(delegated_result_record())
    session = parse_session_result(delegated_stream(records))
    evidence_scope = make_write_evidence_scope(
        cwd=str(tmp_path),
        delegated_target=target,
    )

    evidence = _compute_write_evidence(session, evidence_scope)

    assert evidence.delegated_write_count == 1


@pytest.mark.parametrize("missing", ["output_name", "scope", "paths", "value"])
def test_output_admission_returns_same_evidence_when_unarmed(
    tmp_path: Path,
    missing: str,
) -> None:
    from autoskillit.core import DelegatedOutputScope, WriteEvidence
    from autoskillit.execution.session._write_evidence import _admit_declared_output_write

    root = tmp_path.resolve()
    output_path = root / "plan.md"
    output_path.write_text("new output")
    output_scope = DelegatedOutputScope(str(root), frozenset())
    observed_paths = () if missing == "paths" else (str(output_path),)
    evidence = WriteEvidence(
        write_call_count=0,
        fs_writes_detected=False,
        git_writes_detected=False,
        delegated_write_paths=observed_paths,
    )
    contract = (
        SimpleNamespace()
        if missing == "output_name"
        else SimpleNamespace(delegated_write_output="prep_path")
    )
    behavior = (
        SimpleNamespace()
        if missing == "scope"
        else SimpleNamespace(delegated_output_scope=output_scope)
    )
    fields = {} if missing == "value" else {"prep_path": "plan.md"}

    admitted = _admit_declared_output_write(evidence, fields, contract, behavior, str(root))

    assert admitted is evidence


@pytest.mark.parametrize("absolute", [False, True], ids=["relative-path", "absolute-path"])
def test_output_admission_counts_matching_fresh_observations(
    tmp_path: Path,
    absolute: bool,
) -> None:
    from autoskillit.core import DelegatedOutputScope, WriteEvidence
    from autoskillit.execution.session._write_evidence import _admit_declared_output_write

    root = tmp_path.resolve()
    output_path = root / "plan.md"
    output_path.write_text("new output")
    canonical_path = str(output_path.resolve())
    evidence = WriteEvidence(
        write_call_count=2,
        fs_writes_detected=True,
        git_writes_detected=False,
        file_changes_count=1,
        delegated_write_count=3,
        delegated_write_paths=(canonical_path, canonical_path, str(root / "other.md")),
    )
    contract = SimpleNamespace(delegated_write_output="prep_path")
    behavior = SimpleNamespace(delegated_output_scope=DelegatedOutputScope(str(root), frozenset()))
    emitted_path = str(output_path) if absolute else "plan.md"

    admitted = _admit_declared_output_write(
        evidence,
        {"prep_path": emitted_path},
        contract,
        behavior,
        str(root),
    )

    assert admitted is not evidence
    assert admitted.write_call_count == evidence.write_call_count
    assert admitted.file_changes_count == evidence.file_changes_count
    assert admitted.delegated_write_count == evidence.delegated_write_count + 2
    assert admitted.delegated_write_paths == evidence.delegated_write_paths


@pytest.mark.parametrize("case", ["preexisting", "stale", "mismatched"])
def test_output_admission_rejections_preserve_evidence_identity(
    tmp_path: Path,
    case: str,
) -> None:
    from autoskillit.core import DelegatedOutputScope, WriteEvidence
    from autoskillit.execution.session._write_evidence import _admit_declared_output_write

    root = tmp_path.resolve()
    output_path = root / "plan.md"
    canonical_path = str(output_path)
    if case != "stale":
        output_path.write_text("output")
    preexisting = frozenset({canonical_path}) if case == "preexisting" else frozenset()
    observed_path = str(root / "other.md") if case == "mismatched" else canonical_path
    evidence = WriteEvidence(
        write_call_count=1,
        fs_writes_detected=False,
        git_writes_detected=False,
        delegated_write_paths=(observed_path,),
    )
    behavior = SimpleNamespace(delegated_output_scope=DelegatedOutputScope(str(root), preexisting))

    admitted = _admit_declared_output_write(
        evidence,
        {"prep_path": "plan.md"},
        SimpleNamespace(delegated_write_output="prep_path"),
        behavior,
        str(root),
    )

    assert admitted is evidence


def test_output_admission_preserves_identity_when_freshness_check_raises(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from autoskillit.core import DelegatedOutputScope, WriteEvidence
    from autoskillit.execution.session._write_evidence import _admit_declared_output_write

    root = tmp_path.resolve()
    output_path = root / "plan.md"
    output_path.write_text("output")
    evidence = WriteEvidence(
        write_call_count=1,
        fs_writes_detected=False,
        git_writes_detected=False,
        delegated_write_paths=(str(output_path),),
    )
    behavior = SimpleNamespace(delegated_output_scope=DelegatedOutputScope(str(root), frozenset()))

    def unavailable(_target: DelegatedWriteTarget) -> bool:
        raise OSError("target unavailable")

    monkeypatch.setattr(DelegatedWriteTarget, "changed", unavailable)
    with capture_logs() as events:
        admitted = _admit_declared_output_write(
            evidence,
            {"prep_path": "plan.md"},
            SimpleNamespace(delegated_write_output="prep_path"),
            behavior,
            str(root),
        )

    assert admitted is evidence
    assert events == [
        {
            "event": "delegated_output_freshness_check_failed",
            "output": "prep_path",
            "error_type": "OSError",
            "log_level": "debug",
        }
    ]


def test_unarmed_conditional_output_does_not_log_admission_warning(tmp_path: Path) -> None:
    from autoskillit.core import DelegatedOutputScope

    root = tmp_path.resolve()
    behavior = WriteBehaviorSpec(
        mode="conditional",
        expected_when=(r"prep_path\s*=",),
        delegated_output_scope=DelegatedOutputScope(str(root), frozenset()),
    )
    contract = SimpleNamespace(
        delegated_write_output="prep_path",
        outputs=(),
        outcome_invariants=(),
    )

    with capture_logs() as events:
        result = _build_skill_result(
            _make_result(
                returncode=0,
                stdout=_edit_stream(str(root / "plan.md"), tool_name="Write"),
            ),
            skill_command="/autoskillit:prepare-pr",
            cwd=str(root),
            write_behavior=behavior,
            backend=ClaudeCodeBackend(),
            skill_contract=contract,
        )

    assert result.success is True
    assert result.evidence.delegated_write_paths == (os.path.realpath(root / "plan.md"),)
    assert result.evidence.has_implementation_evidence is False
    assert not any(event.get("event") == "delegated_output_write_not_admitted" for event in events)


@pytest.mark.parametrize("fs_writes_detected", [False, True])
def test_bound_child_edit_is_implementation_progress(
    tmp_path: Path,
    plan_and_target: tuple[Path, DelegatedWriteTarget],
    fs_writes_detected: bool,
) -> None:
    plan, target = plan_and_target
    plan.write_text(_PREPENDED_MARKER + plan.read_text())
    result = _build_skill_result(
        _make_result(returncode=0, stdout=_edit_stream(str(plan))),
        skill_command=f"/autoskillit:dry-walkthrough {plan}",
        cwd=str(tmp_path),
        write_watch_dirs=(plan.parent,),
        write_behavior=_bound_behavior(target),
        fs_writes_detected=fs_writes_detected,
        backend=ClaudeCodeBackend(),
    )

    assert result.success is True
    assert result.subtype != "zero_writes"
    assert result.evidence.write_call_count == 0
    assert result.evidence.file_changes_count == 0
    assert result.evidence.delegated_write_count == 1
    assert result.has_progress_evidence is True
    assert result.has_implementation_progress is True


def test_unreadable_target_rejects_child_write_and_logs_cause(
    tmp_path: Path,
    plan_and_target: tuple[Path, DelegatedWriteTarget],
    monkeypatch,
) -> None:
    plan, target = plan_and_target

    def unreadable(path: Path) -> bytes:
        raise PermissionError("target unavailable")

    monkeypatch.setattr(Path, "read_bytes", unreadable)
    with capture_logs() as events:
        result = _build_skill_result(
            _make_result(returncode=0, stdout=_edit_stream(str(plan))),
            skill_command=f"/autoskillit:dry-walkthrough {plan}",
            cwd=str(tmp_path),
            write_behavior=_bound_behavior(target),
            backend=ClaudeCodeBackend(),
        )

    assert result.evidence.delegated_write_count == 0
    assert any(
        event.get("event") == "delegated_writes_not_admitted"
        and event.get("error") == "target unavailable"
        and event.get("exc_info") is True
        for event in events
    )


def test_text_handback_without_task_lifecycle_records_is_admitted(
    tmp_path: Path,
    plan_and_target: tuple[Path, DelegatedWriteTarget],
) -> None:
    plan, target = plan_and_target
    plan.write_text(_PREPENDED_MARKER + plan.read_text())
    stdout = delegated_stream(
        [
            delegated_root_agent_call(),
            delegated_child_prompt(),
            delegated_child_tool_use(file_path=str(plan)),
            delegated_child_tool_result(),
            delegated_root_agent_text_result(),
            delegated_root_text(),
            delegated_result_record(),
        ]
    )

    result = _build_skill_result(
        _make_result(returncode=0, stdout=stdout),
        skill_command=f"/autoskillit:dry-walkthrough {plan}",
        cwd=str(tmp_path),
        write_behavior=_bound_behavior(target),
        backend=ClaudeCodeBackend(),
    )

    assert result.success is True
    assert result.subtype != "zero_writes"
    assert result.evidence.delegated_write_count == 1


def test_root_edit_remains_root_write_evidence(tmp_path: Path) -> None:
    root_edit = json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "root_edit",
                        "name": "Edit",
                        "input": {"file_path": str(tmp_path / "root.md")},
                    }
                ]
            },
        }
    )
    result = _build_skill_result(
        _make_result(
            returncode=0, stdout=delegated_stream([root_edit, delegated_result_record()])
        ),
        skill_command="/autoskillit:dry-walkthrough root.md",
        cwd=str(tmp_path),
        write_behavior=_bound_behavior(None),
        backend=ClaudeCodeBackend(),
    )

    assert result.evidence.write_call_count == 1
    assert result.evidence.delegated_write_count == 0


@pytest.mark.parametrize(
    ("scenario", "tool_name", "parent_id", "is_error", "mutation", "fs_writes", "bound"),
    [
        ("child-read", "Read", "call_agent_A", False, "none", False, True),
        ("unchanged-bytes", "Write", "call_agent_A", False, "none", False, True),
        ("denied-edit", "Edit", "call_agent_A", True, "plan", False, True),
        ("unowned-edit", "Edit", "call_other", False, "plan", False, True),
        ("sibling-edit", "Edit", "call_agent_A", False, "sibling", False, True),
        ("filesystem-noise", None, "call_agent_A", False, "none", True, True),
        ("unbound-target", "Edit", "call_agent_A", False, "plan", False, False),
    ],
)
def test_non_admitted_child_activity_still_triggers_zero_writes(
    tmp_path: Path,
    plan_and_target: tuple[Path, DelegatedWriteTarget],
    scenario: str,
    tool_name: str | None,
    parent_id: str,
    is_error: bool,
    mutation: str,
    fs_writes: bool,
    bound: bool,
) -> None:
    plan, target = plan_and_target
    file_path = plan
    if scenario == "unchanged-bytes":
        plan.write_text(_PREPENDED_MARKER + plan.read_text())
        target = DelegatedWriteTarget.bind(os.path.realpath(plan))
        plan.write_text(plan.read_text())
    elif mutation == "plan":
        plan.write_text(_PREPENDED_MARKER + plan.read_text())
    elif mutation == "sibling":
        file_path = tmp_path / "sibling.md"
        file_path.write_text("changed sibling\n")

    records = [delegated_root_agent_call(), delegated_task_started(), delegated_child_prompt()]
    if tool_name is not None:
        records.extend(
            [
                delegated_child_tool_use(
                    tool_use_id=f"child_{scenario}",
                    tool_name=tool_name,
                    file_path=str(file_path),
                    parent_tool_use_id=parent_id,
                ),
                delegated_child_tool_result(
                    tool_use_id=f"child_{scenario}",
                    is_error=True if is_error else None,
                    parent_tool_use_id=parent_id,
                ),
            ]
        )
    records.extend(
        [delegated_task_notification(), delegated_root_agent_result(), delegated_result_record()]
    )
    stdout = delegated_stream(records)
    behavior = _bound_behavior(target if bound else None)

    if bound:
        result = _build_skill_result(
            _make_result(returncode=0, stdout=stdout),
            skill_command=f"/autoskillit:dry-walkthrough {plan}",
            cwd=str(tmp_path),
            write_behavior=behavior,
            fs_writes_detected=fs_writes,
            backend=ClaudeCodeBackend(),
        )
    else:
        with capture_logs() as captured:
            result = _build_skill_result(
                _make_result(returncode=0, stdout=stdout),
                skill_command=f"/autoskillit:dry-walkthrough {plan}",
                cwd=str(tmp_path),
                write_behavior=behavior,
                fs_writes_detected=fs_writes,
                backend=ClaudeCodeBackend(),
            )
        assert any(log.get("event") == "delegated_writes_not_admitted" for log in captured)

    assert result.subtype == "zero_writes"
    assert result.evidence.delegated_write_count == 0


def test_worktree_temp_target_is_not_admitted(tmp_path: Path) -> None:
    plan_dir = tmp_path / ".autoskillit" / "temp" / "implement-worktree-no-merge"
    plan_dir.mkdir(parents=True)
    plan = plan_dir / "plan.md"
    plan.write_text(_BASE_PLAN)
    target = DelegatedWriteTarget.bind(os.path.realpath(plan))
    plan.write_text(_PREPENDED_MARKER + plan.read_text())

    result = _build_skill_result(
        _make_result(returncode=0, stdout=_edit_stream(str(plan))),
        skill_command=f"/autoskillit:implement-worktree-no-merge {plan}",
        cwd=str(tmp_path),
        write_watch_dirs=(plan_dir,),
        write_behavior=_bound_behavior(target),
        backend=ClaudeCodeBackend(),
    )

    assert result.subtype == "zero_writes"
    assert result.evidence.delegated_write_count == 0


def test_worktree_tracked_target_is_admitted(tmp_path: Path) -> None:
    plan = tmp_path / "src" / "x.py"
    plan.parent.mkdir(parents=True)
    plan.write_text("before\n")
    target = DelegatedWriteTarget.bind(os.path.realpath(plan))
    plan.write_text("after\n")

    result = _build_skill_result(
        _make_result(returncode=0, stdout=_edit_stream(str(plan))),
        skill_command=f"/autoskillit:implement-worktree-no-merge {plan}",
        cwd=str(tmp_path),
        write_watch_dirs=(tmp_path / ".autoskillit" / "temp",),
        write_behavior=_bound_behavior(target),
        backend=ClaudeCodeBackend(),
    )

    assert result.success is True
    assert result.evidence.delegated_write_count == 1


@pytest.mark.parametrize("recovery_path", ["adjudicated_failure", "empty_output"])
def test_delegated_only_evidence_reaches_completion_recovery_gates(
    tmp_path: Path,
    plan_and_target: tuple[Path, DelegatedWriteTarget],
    recovery_path: str,
) -> None:
    plan, target = plan_and_target
    plan.write_text(_PREPENDED_MARKER + plan.read_text())
    output_record = delegated_result_record()
    if recovery_path == "empty_output":
        output_record = json.dumps(
            {
                "type": "result",
                "subtype": "empty_output",
                "is_error": True,
                "result": "",
                "session_id": "test-sess",
            }
        )
    stdout = _edit_stream(str(plan), result_record=output_record)
    result = _build_skill_result(
        _make_result(returncode=0, stdout=stdout),
        skill_command=f"/autoskillit:dry-walkthrough {plan}",
        cwd=str(tmp_path),
        write_behavior=_bound_behavior(target),
        backend=ClaudeCodeBackend(),
    )
    assert result.evidence.delegated_write_count == 1

    if recovery_path == "adjudicated_failure":
        adjudicated = replace(
            result,
            success=False,
            needs_retry=False,
            subtype="adjudicated_failure",
            retry_reason=RetryReason.NONE,
        )
        promoted = _apply_contract_recovery_gate(
            adjudicated,
            has_write_evidence=result.evidence.has_evidence,
            readonly_skill=False,
            returncode=0,
            skill_command=f"/autoskillit:dry-walkthrough {plan}",
            audit=None,
            max_consecutive_retries=3,
        )
        assert promoted.needs_retry is True
        assert promoted.retry_reason is RetryReason.CONTRACT_RECOVERY
    else:
        assert result.retry_reason is RetryReason.COMPLETED_NO_FLUSH

    session = parse_session_result(_edit_stream(str(plan)))
    expected_patterns = (r"^plan_path\s*=\s*/.+", r"^verdict\s*=\s*real_fix$")
    assert (
        _synthesize_from_write_artifacts(
            session,
            list(expected_patterns),
            write_call_count=0,
            file_changes=(),
        )
        is None
    )
    contract = SimpleNamespace(
        write_behavior="conditional",
        write_expected_when=(r"verdict\s*=\s*real_fix",),
        outputs=(SimpleNamespace(name="verdict", allowed_values=("real_fix",)),),
    )
    assert (
        _infer_enum_token_from_write_contract(
            session,
            expected_patterns,
            contract,
            write_call_count=0,
            file_changes=(),
        )
        is None
    )


def test_stale_success_recovery_keeps_delegated_write_evidence(
    tmp_path: Path,
    plan_and_target: tuple[Path, DelegatedWriteTarget],
) -> None:
    plan, target = plan_and_target
    plan.write_text(_PREPENDED_MARKER + plan.read_text())
    result = _build_skill_result(
        _sr(
            0,
            _edit_stream(str(plan)),
            termination=TerminationReason.STALE,
        ),
        skill_command=f"/autoskillit:dry-walkthrough {plan}",
        cwd=str(tmp_path),
        write_behavior=_bound_behavior(target),
        backend=ClaudeCodeBackend(),
    )

    assert result.success is True
    assert result.evidence.delegated_write_count == 1


@pytest.mark.anyio
@pytest.mark.parametrize("write_marker", [True, False], ids=["child-edits", "no-op-retry"])
async def test_run_headless_core_composes_delegated_write_evidence(
    tmp_path: Path,
    minimal_ctx,
    monkeypatch,
    write_marker: bool,
) -> None:
    plan_dir = tmp_path / ".autoskillit" / "temp" / "make-plan"
    plan_dir.mkdir(parents=True)
    plan = plan_dir / "plan.md"
    plan.write_text((_PREPENDED_MARKER if not write_marker else "") + _BASE_PLAN)
    target = DelegatedWriteTarget.bind(os.path.realpath(plan))

    async def runner(cmd, **kwargs):
        if cmd and cmd[0] == "git":
            return _make_result(returncode=1, stdout="")
        if write_marker:
            plan.write_text(_PREPENDED_MARKER + plan.read_text())
        return _make_result(
            returncode=0,
            stdout=_edit_stream(str(plan), _result_ndjson("Plan updated.\n%%ORDER_UP%%")),
        )

    monkeypatch.setattr(minimal_ctx, "runner", runner)
    monkeypatch.setattr(
        minimal_ctx,
        "backend",
        _mock_backend(pty_required=True, channel_b_capable=True),
    )
    result = await run_headless_core(
        f"/autoskillit:dry-walkthrough {plan}",
        str(tmp_path),
        minimal_ctx,
        write_watch_dirs=(plan_dir,),
        write_behavior=_bound_behavior(target),
    )

    if write_marker:
        assert result.success is True
        assert result.evidence.delegated_write_count == 1
    else:
        assert result.subtype == "zero_writes"
        assert result.evidence.delegated_write_count == 0
