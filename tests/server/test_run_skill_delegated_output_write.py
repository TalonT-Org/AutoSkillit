"""A fresh declared output may receive credit for an owned child write."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from structlog.testing import capture_logs

from tests.execution.conftest import (
    _mock_backend,
    _result_ndjson,
    _sr,
    delegated_child_tool_result,
    delegated_child_tool_use,
    delegated_root_agent_call,
    delegated_root_agent_result,
)

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]

_PREP_CONTENT = "Prepared pull request notes.\n"


def _contract_and_spec(tmp_path: Path) -> tuple[Any, Any]:
    from autoskillit.config import AutomationConfig
    from autoskillit.recipe.contracts import get_skill_contract, load_bundled_manifest
    from autoskillit.server._factory import make_context
    from tests.server._factory_test_helpers import _runner

    contract = get_skill_contract("prepare-pr", load_bundled_manifest())
    assert contract is not None
    ctx = make_context(AutomationConfig(), runner=_runner(), project_dir=tmp_path)
    assert ctx.write_expected_resolver is not None
    spec = ctx.write_expected_resolver("/autoskillit:prepare-pr")
    assert spec is not None
    return contract, spec


def _dispatch_state(
    cwd: Path,
    contract: Any,
    spec: Any,
    *,
    skill_name: str = "prepare-pr",
    output_dir: str = "",
) -> SimpleNamespace:
    plan = cwd / "plans" / "test-plan.md"
    plan.parent.mkdir(parents=True, exist_ok=True)
    plan.write_text("# Test plan\n", encoding="utf-8")
    inputs: tuple[tuple[str, object], ...]
    if skill_name == "prepare-pr":
        inputs = (
            ("plan_paths", [str(plan)]),
            ("run_name", "test-run"),
            ("base_branch", "main"),
            ("closing_issue", ""),
        )
    else:
        inputs = (("plan_path", str(plan)),)
    return SimpleNamespace(
        skill_command=f"/autoskillit:{skill_name}",
        _skill_contract=contract,
        _stored_contract=None,
        write_spec=spec,
        _bound_recipe_inputs=inputs,
        output_dir=output_dir,
        _default_temp=cwd / ".autoskillit" / "temp" / "prepare-pr",
        cwd=str(cwd),
    )


def _bound_state(cwd: Path, contract: Any, spec: Any) -> SimpleNamespace:
    from autoskillit.server.tools.tools_execution._run_skill_session import (
        _bind_delegated_target,
    )

    state = _dispatch_state(cwd, contract, spec)
    _bind_delegated_target(state)
    return state


def _output_scope(spec: Any) -> Any | None:
    assert hasattr(spec, "delegated_output_scope")
    return getattr(spec, "delegated_output_scope")


def _assert_no_output_scope(spec: Any) -> None:
    assert hasattr(spec, "delegated_output_scope")
    assert getattr(spec, "delegated_output_scope") is None


def _prep_stream(
    prep: str,
    *,
    agent_error: bool = False,
    parent_id: str = "call_agent_A",
    emitted: str | None = None,
    child_path: str | None = None,
    include_child: bool = True,
) -> str:
    records = [delegated_root_agent_call()]
    if include_child:
        records.extend(
            [
                delegated_child_tool_use(
                    tool_name="Write",
                    file_path=child_path or prep,
                    parent_tool_use_id=parent_id,
                ),
                delegated_child_tool_result(parent_tool_use_id=parent_id),
            ]
        )
    if agent_error:
        records.append(
            json.dumps(
                {
                    "type": "user",
                    "message": {
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "call_agent_A",
                                "is_error": True,
                            }
                        ]
                    },
                }
            )
        )
    else:
        records.append(delegated_root_agent_result())
    records.extend(
        [
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "name": "Read",
                                "id": "read_prep",
                                "input": {"file_path": prep},
                            }
                        ]
                    },
                }
            ),
            json.dumps(
                {
                    "type": "user",
                    "message": {
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "read_prep",
                                "content": [{"type": "text", "text": _PREP_CONTENT}],
                            }
                        ]
                    },
                }
            ),
            _result_ndjson(
                f"prep_path = {emitted or prep}\n"
                "selected_lenses = none\n"
                "lens_context_paths = none\n"
                "%%ORDER_UP%%"
            ),
        ]
    )
    return "\n".join(records)


def _root_write_stream(prep: str) -> str:
    return "\n".join(
        [
            delegated_child_tool_use(
                tool_use_id="root_write",
                tool_name="Write",
                file_path=prep,
                parent_tool_use_id=None,
                agent_type=None,
            ),
            delegated_child_tool_result(
                tool_use_id="root_write",
                parent_tool_use_id=None,
                agent_type=None,
            ),
            _result_ndjson(
                f"prep_path = {prep}\n"
                "selected_lenses = none\n"
                "lens_context_paths = none\n"
                "%%ORDER_UP%%"
            ),
        ]
    )


def _write_file(path: str | Path) -> None:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(_PREP_CONTENT, encoding="utf-8")


def _skill_result(state: SimpleNamespace, contract: Any, stream: str) -> Any:
    from autoskillit.execution.backends.claude import ClaudeCodeBackend
    from autoskillit.execution.headless import _build_skill_result

    return _build_skill_result(
        _sr(0, stdout=stream),
        skill_command=state.skill_command,
        cwd=state.cwd,
        write_behavior=state.write_spec,
        write_watch_dirs=(state._default_temp,),
        skill_contract=contract,
        backend=ClaudeCodeBackend(),
    )


@pytest.mark.anyio
async def test_fresh_child_written_prep_is_credited_end_to_end(
    tmp_path: Path, minimal_ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autoskillit.execution.headless import run_headless_core
    from tests.conftest import _make_result

    contract, spec = _contract_and_spec(tmp_path)
    state = _bound_state(tmp_path, contract, spec)
    prep = state._default_temp / "pr_prep.md"
    assert not prep.exists()

    async def runner(cmd, **kwargs):
        if cmd and cmd[0] == "git":
            return _make_result(returncode=1)
        _write_file(prep)
        return _make_result(returncode=0, stdout=_prep_stream(str(prep)))

    monkeypatch.setattr(
        minimal_ctx, "backend", _mock_backend(pty_required=True, channel_b_capable=True)
    )
    monkeypatch.setattr(minimal_ctx, "runner", runner)
    result = await run_headless_core(
        state.skill_command,
        str(tmp_path),
        minimal_ctx,
        write_watch_dirs=(state._default_temp,),
        write_behavior=state.write_spec,
        skill_contract=contract,
    )

    assert result.success is True
    assert result.subtype != "zero_writes"
    assert result.evidence.write_call_count == 0
    assert result.evidence.delegated_write_count == 1


@pytest.mark.parametrize(
    "case",
    [
        "denied-parent",
        "unowned-child",
        "wrong-output",
        "absolute-output",
        "traversal-output",
        "symlink-output",
        "preexisting-output",
        "disappeared-output",
        "filesystem-noise",
    ],
)
def test_unproven_prep_write_stays_zero_writes(tmp_path: Path, case: str) -> None:
    from autoskillit.server.tools.tools_execution._run_skill_session import (
        _bind_delegated_target,
    )

    contract, spec = _contract_and_spec(tmp_path)
    state = _dispatch_state(tmp_path, contract, spec)
    root = state._default_temp
    prep = str(root / "pr_prep.md")
    child_path = prep
    emitted = prep
    include_child = True

    if case == "preexisting-output":
        _write_file(prep)
    _bind_delegated_target(state)

    if case in {"denied-parent", "unowned-child", "preexisting-output", "disappeared-output"}:
        _write_file(prep)
    elif case == "wrong-output":
        child_path = str(root / "pr_prep_A.md")
        prep = str(root / "pr_prep_B.md")
        emitted = prep
        _write_file(child_path)
        _write_file(prep)
    elif case == "filesystem-noise":
        include_child = False
        _write_file(prep)
    elif case.endswith("-output") and case != "preexisting-output":
        root.mkdir(parents=True, exist_ok=True)
        name = "pr_prep.md"
        if case == "absolute-output":
            prep = str(tmp_path / "outside" / name)
        elif case == "traversal-output":
            prep = str(root / ".." / "elsewhere" / name)
        else:
            outside = tmp_path / "outside"
            outside.mkdir(parents=True, exist_ok=True)
            (root / "alias").symlink_to(outside, target_is_directory=True)
            prep = str(root / "alias" / name)
        child_path = prep
        emitted = prep
        _write_file(prep)

    stream = _prep_stream(
        prep,
        agent_error=case == "denied-parent",
        parent_id="unregistered-parent" if case == "unowned-child" else "call_agent_A",
        emitted=emitted,
        child_path=child_path,
        include_child=include_child,
    )
    if case == "disappeared-output":
        Path(prep).unlink()
    result = _skill_result(state, contract, stream)

    assert result.subtype == "zero_writes"
    assert result.evidence.delegated_write_count == 0


def test_root_written_prep_still_credited(tmp_path: Path) -> None:
    contract, spec = _contract_and_spec(tmp_path)
    state = _bound_state(tmp_path, contract, spec)
    prep = state._default_temp / "pr_prep.md"
    _write_file(prep)

    result = _skill_result(state, contract, _root_write_stream(str(prep)))

    assert result.success is True
    assert result.evidence.write_call_count == 1


def test_binder_records_preexisting_names(tmp_path: Path) -> None:
    contract, spec = _contract_and_spec(tmp_path)
    state = _dispatch_state(tmp_path, contract, spec)
    state._default_temp.mkdir(parents=True)
    (state._default_temp / "old.md").write_text("old", encoding="utf-8")

    from autoskillit.server.tools.tools_execution._run_skill_session import (
        _bind_delegated_target,
    )

    _bind_delegated_target(state)
    scope = _output_scope(state.write_spec)

    assert scope is not None
    assert scope.root == os.path.realpath(state._default_temp)
    assert os.path.join(scope.root, "old.md") in scope.preexisting


def test_binder_missing_root_is_known_empty(tmp_path: Path) -> None:
    contract, spec = _contract_and_spec(tmp_path)
    state = _dispatch_state(tmp_path, contract, spec)

    from autoskillit.server.tools.tools_execution._run_skill_session import (
        _bind_delegated_target,
    )

    _bind_delegated_target(state)
    scope = _output_scope(state.write_spec)

    assert scope is not None
    assert scope.root == os.path.realpath(state._default_temp)
    assert scope.preexisting == frozenset()


def test_binder_rejects_non_directory_ancestor(tmp_path: Path) -> None:
    contract, spec = _contract_and_spec(tmp_path)
    state = _dispatch_state(tmp_path, contract, spec)
    temp_parent = state._default_temp.parent
    temp_parent.parent.mkdir(parents=True)
    temp_parent.write_text("not a directory", encoding="utf-8")

    from autoskillit.server.tools.tools_execution._run_skill_session import (
        _bind_delegated_target,
    )

    with capture_logs() as events:
        _bind_delegated_target(state)

    _assert_no_output_scope(state.write_spec)
    assert any(event.get("event") == "delegated_write_target_unbound" for event in events)


def test_binder_skips_explicit_output_dir(tmp_path: Path) -> None:
    contract, spec = _contract_and_spec(tmp_path)
    state = _dispatch_state(tmp_path, contract, spec, output_dir=".")

    from autoskillit.server.tools.tools_execution._run_skill_session import (
        _bind_delegated_target,
    )

    with capture_logs() as events:
        _bind_delegated_target(state)

    _assert_no_output_scope(state.write_spec)
    assert any(
        event.get("event") == "delegated_write_target_unbound"
        and event.get("reason") == "explicit_output_dir"
        for event in events
    )


def test_binder_rejects_root_outside_cwd(tmp_path: Path) -> None:
    contract, spec = _contract_and_spec(tmp_path)
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    state = _dispatch_state(cwd, contract, spec)
    (cwd / ".autoskillit").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (cwd / ".autoskillit" / "temp").symlink_to(outside, target_is_directory=True)

    from autoskillit.server.tools.tools_execution._run_skill_session import (
        _bind_delegated_target,
    )

    with capture_logs() as events:
        _bind_delegated_target(state)

    _assert_no_output_scope(state.write_spec)
    assert any(event.get("event") == "delegated_write_target_unbound" for event in events)


def test_binder_rebinds_output_scope_on_resume(tmp_path: Path) -> None:
    from autoskillit.core import DelegatedWriteTarget
    from autoskillit.server.tools.tools_execution._run_skill_session import (
        _bind_delegated_target,
    )

    contract, spec = _contract_and_spec(tmp_path)
    state = _dispatch_state(tmp_path, contract, spec)
    input_path = tmp_path / "input.md"
    input_path.write_text("input", encoding="utf-8")
    original_target = DelegatedWriteTarget.bind(str(input_path))
    state.write_spec = replace(state.write_spec, delegated_target=original_target)
    state._stored_contract = object()

    _bind_delegated_target(state)

    assert _output_scope(state.write_spec) is not None
    assert state.write_spec.delegated_target is original_target


def test_binder_ignores_contracts_without_output_route(tmp_path: Path) -> None:
    from autoskillit.config import AutomationConfig
    from autoskillit.recipe.contracts import get_skill_contract, load_bundled_manifest
    from autoskillit.server._factory import make_context
    from autoskillit.server.tools.tools_execution._run_skill_session import (
        _bind_delegated_target,
    )
    from tests.server._factory_test_helpers import _runner

    contract = get_skill_contract("dry-walkthrough", load_bundled_manifest())
    assert contract is not None
    ctx = make_context(AutomationConfig(), runner=_runner(), project_dir=tmp_path)
    assert ctx.write_expected_resolver is not None
    spec = ctx.write_expected_resolver("/autoskillit:dry-walkthrough")
    assert spec is not None
    state = _dispatch_state(tmp_path, contract, spec, skill_name="dry-walkthrough")

    _bind_delegated_target(state)

    _assert_no_output_scope(state.write_spec)
    assert state.write_spec.delegated_target is not None


def test_incomplete_baseline_stays_unbound_and_logs(tmp_path: Path, monkeypatch) -> None:
    from autoskillit.core import TreeVanishedError
    from autoskillit.server.tools.tools_execution import _run_skill_session
    from autoskillit.server.tools.tools_execution._run_skill_session import (
        _bind_delegated_target,
    )

    contract, spec = _contract_and_spec(tmp_path)
    state = _dispatch_state(tmp_path, contract, spec)
    state._default_temp.mkdir(parents=True)

    def incomplete_walk(root: Path):
        raise TreeVanishedError("old.md", root)

    monkeypatch.setattr(_run_skill_session, "strict_walk", incomplete_walk, raising=False)
    with capture_logs() as events:
        _bind_delegated_target(state)

    _assert_no_output_scope(state.write_spec)
    assert any(event.get("event") == "delegated_write_target_unbound" for event in events)


def test_delegated_write_output_survives_contract_round_trip(tmp_path: Path) -> None:
    from autoskillit.server.tools._execution_helpers import (
        deserialize_skill_contract,
        serialize_skill_contract,
    )

    contract, _ = _contract_and_spec(tmp_path)
    restored = deserialize_skill_contract(serialize_skill_contract(contract))

    assert restored is not None
    assert getattr(restored, "delegated_write_output", None) == "prep_path"
