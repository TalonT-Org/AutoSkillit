"""Pin the standalone collector's identities, tools and hook policy to the product."""

import json
from pathlib import Path

import pytest

from autoskillit.core import DIRECT_PREFIX, PLUGIN_PREFIX, ClaudeFlags, pkg_root
from autoskillit.core.types import GATED_TOOLS, UNGATED_TOOLS
from autoskillit.execution.backends import claude_resolvable_agent_names
from tests._hook_protocol_oracle import claude_matcher_admits
from tests._script_loader import load_check_script

pytestmark = pytest.mark.medium

sessions = load_check_script(
    "_autoskillit_e2e_sessions_contract",
    Path(__file__).resolve().parents[2] / "scripts" / "e2e" / "e2e_sessions.py",
)


def test_server_identities_match_canonical_prefixes() -> None:
    assert sessions._AUTOSKILLIT_SERVERS == frozenset(
        tuple(prefix.split("__")[:-1]) for prefix in (DIRECT_PREFIX, PLUGIN_PREFIX)
    )


def test_tracked_tools_are_registered_product_tools() -> None:
    assert sessions._TRACKED_TOOLS
    assert {
        "claim_and_resolve_issue",
        "create_and_publish_branch",
        "run_skill",
        "complete_run_skill_result",
        "recover_run_skill_result",
    } <= sessions._TRACKED_TOOLS
    assert sessions._TRACKED_TOOLS <= GATED_TOOLS | UNGATED_TOOLS


def test_agent_hooks_admit_every_resolvable_agent_and_reject_an_unnamed_one() -> None:
    names = claude_resolvable_agent_names()
    assert names
    for event in sessions._AGENT_EVENTS:
        matcher = sessions.LIFECYCLE_HOOK_MATCHERS[event]
        assert matcher is not None
        assert not claude_matcher_admits(matcher, "")
        assert all(claude_matcher_admits(matcher, name) for name in names)


def test_session_hooks_stay_unfiltered() -> None:
    matchers = sessions.LIFECYCLE_HOOK_MATCHERS
    assert sessions._SESSION_EVENTS == frozenset({"SessionStart", "SessionEnd"})
    assert set(matchers) == sessions._LIFECYCLE_EVENTS
    assert all(matchers[event] is None for event in sessions._SESSION_EVENTS)


def test_collector_consumes_production_session_provenance(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from autoskillit.core.types.foundation._type_execution_identity import ExecutionIdentity
    from autoskillit.fleet import DispatchCompleted, DispatchEffectProvenance, DispatchStatus
    from tests.execution.conftest import _flush

    log_root = tmp_path / "logs"
    dispatch_id = "dispatch-contract"
    root_id = "root-contract-session"
    child_id = "child-contract-session"
    child_identity = ExecutionIdentity(parent_session_id=root_id)

    def write_session(session_id: str, parent_session_id: str, caller_session_id: str) -> None:
        transcript = tmp_path / "transcripts" / f"{session_id}.jsonl"
        transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript.write_text("", encoding="utf-8")
        locator = SimpleNamespace(session_log_path=lambda _cwd, _session_id: transcript)
        _flush(
            log_root,
            session_id=session_id,
            dispatch_id=dispatch_id,
            caller_session_id=caller_session_id,
            execution_identity=ExecutionIdentity(parent_session_id=parent_session_id),
            session_locator=locator,
        )

    write_session(root_id, "", "")
    write_session(child_id, root_id, root_id)

    payload = {"result": "completed"}
    envelope = json.loads(
        DispatchCompleted(
            dispatch_status=DispatchStatus.SUCCESS,
            dispatch_id=dispatch_id,
            dispatched_session_id=root_id,
            reason="",
            effect_provenance=DispatchEffectProvenance(operation_id=dispatch_id),
            l3_payload=payload,
        ).to_envelope()
    )
    assert envelope["l3_payload"] == payload

    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text(
        "".join(
            json.dumps({"event": "SessionStart", "timestamp": index, "session_id": session_id})
            + "\n"
            for index, session_id in enumerate((root_id, child_id), start=1)
        ),
        encoding="utf-8",
    )
    discovered = sessions._discover_owned(trace_path, envelope, log_root)

    assert discovered["root_id"] == root_id
    assert discovered["owned"][root_id]["row"]["dispatch_id"] == dispatch_id
    assert set(discovered["owned"]) == {root_id, child_id}
    assert (
        discovered["owned"][child_id]["summary"]["execution_identity"] == child_identity.to_dict()
    )
    assert not discovered["violations"]


def test_product_selects_no_main_agent() -> None:
    # Internal-agent callbacks are unnamed only while no main agent is set.
    assert {"--agent", "--settings"}.isdisjoint(flag.value for flag in ClaudeFlags)
    root = pkg_root()
    manifest = json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert "agent" not in manifest.get("settings", {})
    plugin_settings = root / "settings.json"
    assert not plugin_settings.exists() or "agent" not in json.loads(
        plugin_settings.read_text(encoding="utf-8")
    )
