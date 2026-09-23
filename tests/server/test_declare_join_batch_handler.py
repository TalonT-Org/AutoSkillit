from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from autoskillit.core import PluginLoadMode, SkillExecutionRole
from autoskillit.execution.backends.claude import ClaudeCodeBackend
from autoskillit.hooks._join_ledger import (
    JOIN_LEDGER_SCHEMA_VERSION,
    OUTCOME_FAILURE,
    OUTCOME_SUCCESS,
    claim_assignment,
    declare_batch,
    ledger_paths,
    settle_assignment,
)
from autoskillit.hooks._runtime._hook_settings import DIAGNOSTIC_KEYS
from autoskillit.hooks._session_binding import (
    SESSION_BINDING_SCHEMA_VERSION,
    LoadedSkillEntry,
    SessionBinding,
    resolve_binding_path,
    write_binding,
)
from autoskillit.server.tools.tools_kitchen import _declare_join_batch as declare_module
from autoskillit.workspace import (
    DefaultSkillResolver,
    EffectiveSkillCatalog,
    SkillCatalogEntry,
    project_default_plugin_authority,
)
from tests.conftest import production_interpreter_env

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium]

_NO_BINDING_ERROR = (
    "declare_join_batch requires a session binding written by Skill/PostToolUse "
    "or UserPromptExpansion slash-command invocation"
)


def _entry(
    skill_name: str = "rectify",
    *,
    join_required: bool = True,
    count: int = 1,
) -> LoadedSkillEntry:
    return LoadedSkillEntry(
        skill_name=skill_name,
        ts="2026-08-26T00:00:00+00:00",
        join_required=join_required,
        child_spawn_cardinality={"workers": count},
        semantic_digest="semantic",
        adaptation_digest="adaptation",
        projected_digest="projected",
        canonical_digest="canonical",
        source_artifact_digest="artifact-digest",
        source_artifact_incarnation_id="incarnation",
        binding_valid=True,
        binding_error=None,
    )


def _binding(
    session_id: str,
    *,
    entries: tuple[LoadedSkillEntry, ...] | None = None,
    binding_valid: bool = True,
    artifact_digest: str = "artifact-digest",
    managed_parent_id: str = "top_level",
) -> SessionBinding:
    loaded = entries if entries is not None else (_entry(),)
    return SessionBinding(
        schema_version=SESSION_BINDING_SCHEMA_VERSION,
        session_id=session_id,
        join_required=any(entry.join_required for entry in loaded),
        binding_valid=binding_valid,
        artifact_digest=artifact_digest,
        loaded_skills=loaded,
        managed_parent_id=managed_parent_id,
    )


def _write_session_binding(
    state_root: Path,
    filename_session_id: str,
    binding: SessionBinding,
) -> Path:
    # resolve_channel_dir walks up from state_root looking for the nearest
    # `.autoskillit/` ancestor — without creating it inside state_root, the
    # walk-up lands on a shared `.autoskillit/` at /tmp and the join ledger
    # accumulates stale batches across runs (causing "another wave is already
    # open" failures). Pre-create the directory inside state_root so the
    # walk-up matches there.
    (state_root / ".autoskillit" / "temp").mkdir(parents=True, exist_ok=True)
    path = resolve_binding_path(str(state_root), filename_session_id)
    write_binding(path, binding)
    return path


def _capable_backend() -> SimpleNamespace:
    return SimpleNamespace(capabilities=SimpleNamespace(fixed_set_join_capable=True))


def _run_join_guard(
    state_root: Path,
    project_root: Path,
    session_id: str,
    *,
    tool_name: str | None = None,
) -> subprocess.CompletedProcess[str]:
    env = production_interpreter_env()
    for name in (
        "AUTOSKILLIT_HEADLESS",
        "AUTOSKILLIT_LAUNCH_ID",
        "AUTOSKILLIT_MANAGED_JOIN_PARENT_ID",
    ):
        env.pop(name, None)
    env.update(
        {
            "AUTOSKILLIT_AGENT_BACKEND": "claude-code",
            "AUTOSKILLIT_LOG_DIR": str(state_root / "logs"),
            "AUTOSKILLIT_STATE_ROOT": str(state_root),
            "AUTOSKILLIT_SESSION_TYPE": "skill",
        }
    )
    guard = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "autoskillit"
        / "hooks"
        / "guards"
        / ("join_followup_guard.py" if tool_name else "join_stop_guard.py")
    )
    payload: dict[str, object] = {"session_id": session_id, "cwd": str(project_root)}
    if tool_name:
        payload.update(
            tool_name=tool_name,
            tool_use_id=f"refused-replacement-{tool_name}",
            tool_input=(
                {"command": "printf SHOULD_NOT_RUN"}
                if tool_name == "Bash"
                else {"file_path": str(project_root / "blocked.txt"), "content": "blocked"}
            ),
        )
    return subprocess.run(
        [sys.executable, str(guard)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
        cwd=project_root,
        env=env,
        timeout=10,
    )


def test_handler_rejects_session_id_path_traversal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTOSKILLIT_STATE_ROOT", str(tmp_path))

    result = declare_module._declare_join_batch_handler(
        "rectify", ["assignment"], "../escape", tmp_path
    )

    assert result["success"] is False
    assert "path separators" in str(result["error"])
    assert not list((tmp_path / ".autoskillit" / "temp").rglob("skill_guard_*.flag"))


def test_end_to_end_real_projection_real_hook_real_handler(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tool_ctx,
) -> None:
    home = tmp_path / "home"
    state_root = tmp_path / "state-root"
    isolated_cwd = tmp_path / "isolated-project"
    home.mkdir()
    state_root.mkdir()
    isolated_cwd.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("AUTOSKILLIT_STATE_ROOT", str(state_root))
    monkeypatch.setenv("AUTOSKILLIT_AGENT_BACKEND", "claude-code")

    skill = DefaultSkillResolver().resolve("rectify")
    assert skill is not None
    catalog = EffectiveSkillCatalog(
        skills=(SkillCatalogEntry.from_skill_info(skill),),
        execution_role=SkillExecutionRole.SESSION,
    )
    authority = project_default_plugin_authority(
        cwd=tool_ctx.project_dir,
        base_branch="develop",
        catalog=catalog,
    )

    with authority.acquire_launch_binding(
        backend=ClaudeCodeBackend(),
        load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
    ) as launch_binding:
        assert launch_binding.plugin_dir is not None
        hook_path = launch_binding.plugin_dir / "hooks" / "skill_load_post_hook.py"
        env = production_interpreter_env()
        env.update(
            {
                "AUTOSKILLIT_AGENT_BACKEND": "claude-code",
                "AUTOSKILLIT_STATE_ROOT": str(state_root),
            }
        )
        env.pop("AUTOSKILLIT_PROJECTION_MANIFEST_PATH", None)
        completed = subprocess.run(
            [sys.executable, str(hook_path)],
            input=json.dumps(
                {
                    "hook_event_name": "PostToolUse",
                    "tool_name": "Skill",
                    "tool_input": {"skill": "autoskillit:rectify"},
                    "session_id": "session-e2e",
                    "cwd": str(isolated_cwd),
                }
            ),
            text=True,
            capture_output=True,
            cwd=isolated_cwd,
            env=env,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        additional_context = json.loads(completed.stdout)["additionalContext"]
        delivered = re.search(
            r'skill_name="([^"]+)".*session_id="([^"]+)"',
            additional_context,
        )
        assert delivered is not None
        delivered_skill_name, delivered_session_id = delivered.groups()
        assert delivered_skill_name == "rectify"
        assert delivered_session_id == "session-e2e"

        result = declare_module._declare_join_batch_handler(
            skill_name=delivered_skill_name,
            assignments=["foundation", "interface", "registry"],
            session_id=delivered_session_id,
            project_root=tool_ctx.project_dir,
        )

    assert launch_binding.closed
    assert result["success"] is True
    assert result["join_batch_id"]


@pytest.mark.parametrize(
    ("case", "expected_error"),
    [
        ("invalid_binding", "requires a valid session binding"),
        ("selected_not_join_bearing", "is not join-bearing"),
        ("skill_not_loaded", "is not loaded in this session"),
        ("backend_not_capable", "does not attest fixed_set_join_capable"),
        ("assignment_count", "declares count=2; received 1 assignments"),
        ("empty_top_level_digest", "non-empty top-level artifact_digest"),
        ("empty_managed_parent", "non-empty binding managed_parent_id"),
        ("wrong_session", "requested 'requested-session', recorded 'recorded-session'"),
    ],
)
def test_each_refusal_names_a_distinct_cause(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    expected_error: str,
) -> None:
    state_root = tmp_path / "state-root"
    state_root.mkdir()
    monkeypatch.setenv("AUTOSKILLIT_STATE_ROOT", str(state_root))
    monkeypatch.setattr(declare_module, "get_backend", lambda _name: _capable_backend())
    requested_session_id = "requested-session"
    binding = _binding(requested_session_id)

    if case == "invalid_binding":
        binding = _binding(requested_session_id, binding_valid=False)
    elif case == "selected_not_join_bearing":
        binding = _binding(
            requested_session_id,
            entries=(_entry(join_required=False), _entry("other", join_required=True)),
        )
    elif case == "skill_not_loaded":
        binding = _binding(requested_session_id, entries=(_entry("other"),))
    elif case == "backend_not_capable":
        monkeypatch.setattr(
            declare_module,
            "get_backend",
            lambda _name: SimpleNamespace(
                capabilities=SimpleNamespace(fixed_set_join_capable=False)
            ),
        )
    elif case == "assignment_count":
        binding = _binding(requested_session_id, entries=(_entry(count=2),))
    elif case == "empty_top_level_digest":
        binding = _binding(requested_session_id, artifact_digest="")
    elif case == "empty_managed_parent":
        binding = _binding(requested_session_id, managed_parent_id="")
    elif case == "wrong_session":
        binding = _binding("recorded-session")
        _write_session_binding(state_root, "recorded-session", binding)
        result = declare_module._declare_join_batch_handler(
            "rectify", ["assignment"], requested_session_id, tmp_path
        )
        assert result["success"] is False
        assert expected_error in str(result["error"])
        return

    binding_path = _write_session_binding(state_root, requested_session_id, binding)
    if case == "empty_top_level_digest":
        raw = json.loads(binding_path.read_text(encoding="utf-8"))
        raw["loaded_skills"][0]["artifact_digest"] = "legacy-per-skill-digest"
        binding_path.write_text(json.dumps(raw), encoding="utf-8")

    result = declare_module._declare_join_batch_handler(
        "rectify", ["assignment"], requested_session_id, tmp_path
    )

    assert result["success"] is False
    assert expected_error in str(result["error"])


@pytest.mark.parametrize("requested_skill_name", ("rectify", "autoskillit:rectify"))
def test_skill_name_matches_in_both_namespaced_and_bare_form(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    requested_skill_name: str,
) -> None:
    state_root = tmp_path / "state-root"
    state_root.mkdir()
    monkeypatch.setenv("AUTOSKILLIT_STATE_ROOT", str(state_root))
    monkeypatch.setattr(declare_module, "get_backend", lambda _name: _capable_backend())
    session_id = f"session-{requested_skill_name.replace(':', '-')}"
    _write_session_binding(state_root, session_id, _binding(session_id))

    result = declare_module._declare_join_batch_handler(
        requested_skill_name,
        ["assignment"],
        session_id,
        tmp_path,
    )
    cardinality_violation = declare_module._declare_join_batch_handler(
        "autoskillit:rectify",
        ["one", "two"],
        session_id,
        tmp_path,
    )

    assert result["success"] is True
    assert result["wave"]["skill_name"] == "rectify"
    assert cardinality_violation["success"] is False
    assert "declares count=1; received 2 assignments" in str(cardinality_violation["error"])


@pytest.mark.parametrize(
    ("candidate_count", "expected_truncated"),
    [(20, False), (21, True)],
    ids=["within_limit", "exceeds_limit"],
)
def test_binding_candidate_enumeration_reports_truncation_boundary(
    tmp_path: Path,
    candidate_count: int,
    expected_truncated: bool,
) -> None:
    channel_dir = tmp_path / "channel"
    channel_dir.mkdir()
    expected_paths = [
        channel_dir / f"skill_guard_candidate-{index:02d}.flag" for index in range(candidate_count)
    ]
    for path in reversed(expected_paths):
        path.write_text("{}", encoding="utf-8")

    paths, truncated = declare_module.enumerate_binding_paths(channel_dir)

    assert paths == tuple(expected_paths[:20])
    assert truncated is expected_truncated


def test_binding_candidate_enumeration_oserror_preserves_generic_refusal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_root = tmp_path / "state-root"
    state_root.mkdir()
    monkeypatch.setenv("AUTOSKILLIT_STATE_ROOT", str(state_root))
    requested_path = resolve_binding_path(str(state_root), "requested")
    channel_dir = requested_path.parent
    channel_dir.mkdir(parents=True, exist_ok=True)
    _write_session_binding(state_root, "recorded", _binding("recorded"))
    diagnostics: list[dict[str, object]] = []
    monkeypatch.setattr(declare_module, "_emit_join_diagnostic", diagnostics.append)

    def raising_stub(_path: Path, _pattern: str) -> None:
        raise OSError("candidate enumeration failed")

    with monkeypatch.context() as scoped:
        scoped.setattr(Path, "glob", raising_stub)
        assert declare_module.enumerate_binding_paths(channel_dir) == ((), False)

        result = declare_module._declare_join_batch_handler(
            "rectify", ["assignment"], "requested", tmp_path
        )

        assert result == {
            "success": False,
            "error": _NO_BINDING_ERROR,
        }
        assert diagnostics == []


@pytest.mark.parametrize(
    ("candidate_count", "recorded_session_id", "expected_status"),
    [
        (20, "recorded", "wrong_session_id"),
        (21, "recorded", "ambiguous_session_bindings"),
        (21, "requested", None),
    ],
    ids=[
        "single_foreign_within_limit",
        "single_foreign_truncated",
        "all_match_truncated",
    ],
)
def test_handler_limits_binding_reads_and_respects_scan_completeness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    candidate_count: int,
    recorded_session_id: str,
    expected_status: str | None,
) -> None:
    state_root = tmp_path / "state-root"
    state_root.mkdir()
    monkeypatch.setenv("AUTOSKILLIT_STATE_ROOT", str(state_root))
    candidate_paths = [
        _write_session_binding(
            state_root,
            f"candidate-{index:02d}",
            _binding(recorded_session_id),
        )
        for index in reversed(range(candidate_count))
    ]
    candidate_paths.sort()
    diagnostics: list[dict[str, object]] = []
    read_paths: list[Path] = []
    real_read_binding = declare_module.read_binding

    def recording_read_binding(path: Path) -> SessionBinding | None:
        read_paths.append(path)
        return real_read_binding(path)

    monkeypatch.setattr(declare_module, "_emit_join_diagnostic", diagnostics.append)
    monkeypatch.setattr(declare_module, "read_binding", recording_read_binding)

    result = declare_module._declare_join_batch_handler(
        "rectify", ["assignment"], "requested", tmp_path
    )

    assert result["success"] is False
    assert read_paths == candidate_paths[:20]
    if expected_status is None:
        assert result == {
            "success": False,
            "error": _NO_BINDING_ERROR,
        }
        assert diagnostics == []
    else:
        assert diagnostics == [
            {
                "gate": "declare_join_batch",
                "session_id": "requested",
                "status": expected_status,
            }
        ]
        if expected_status == "wrong_session_id":
            assert result["error"] == (
                "declare_join_batch session mismatch: requested 'requested', recorded 'recorded'"
            )
        else:
            assert "requested 'requested'" in str(result["error"])
            assert "incomplete" in str(result["error"])
            assert "recorded 'recorded'" not in str(result["error"])


@pytest.mark.parametrize(
    ("case", "expected_status"),
    [
        ("single_candidate", "wrong_session_id"),
        ("typed_mismatch", "wrong_session_id"),
        ("ambiguous", "ambiguous_session_bindings"),
    ],
)
def test_wrong_session_id_is_reported_as_such(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    expected_status: str,
) -> None:
    state_root = tmp_path / "state-root"
    state_root.mkdir()
    monkeypatch.setenv("AUTOSKILLIT_STATE_ROOT", str(state_root))
    diagnostics: list[dict[str, object]] = []
    monkeypatch.setattr(declare_module, "_emit_join_diagnostic", diagnostics.append)

    if case == "single_candidate":
        _write_session_binding(state_root, "recorded", _binding("recorded"))
    elif case == "typed_mismatch":
        _write_session_binding(state_root, "requested", _binding("recorded"))
    else:
        _write_session_binding(state_root, "recorded-a", _binding("recorded-a"))
        _write_session_binding(state_root, "recorded-b", _binding("recorded-b"))

    result = declare_module._declare_join_batch_handler(
        "rectify", ["assignment"], "requested", tmp_path
    )

    assert result["success"] is False
    assert "requested" in str(result["error"])
    if case == "ambiguous":
        assert "ambiguous" in str(result["error"])
    else:
        assert "recorded" in str(result["error"])
    assert diagnostics
    assert set(diagnostics[-1]) <= DIAGNOSTIC_KEYS
    assert diagnostics[-1]["status"] == expected_status


def test_malformed_requested_binding_is_not_reported_as_wrong_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_root = tmp_path / "state-root"
    state_root.mkdir()
    monkeypatch.setenv("AUTOSKILLIT_STATE_ROOT", str(state_root))
    # resolve_channel_dir walks up from state_root looking for the nearest
    # `.autoskillit/` — without pre-creating it inside state_root, the
    # walk-up lands on the shared `.autoskillit/` at /tmp and the test's
    # malformed-binding file ends up there instead of in the per-test dir.
    (state_root / ".autoskillit" / "temp").mkdir(parents=True, exist_ok=True)
    requested_path = resolve_binding_path(str(state_root), "requested")
    requested_path.parent.mkdir(parents=True, exist_ok=True)
    requested_path.write_text("{}", encoding="utf-8")
    _write_session_binding(state_root, "recorded", _binding("recorded"))

    result = declare_module._declare_join_batch_handler(
        "rectify", ["assignment"], "requested", tmp_path
    )

    assert result == {
        "success": False,
        "error": "unsupported session-binding schema_version: None",
    }


def test_ledger_and_binding_share_the_state_root_aware_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_root = tmp_path / "external-state-root"
    state_root.mkdir()
    monkeypatch.setenv("AUTOSKILLIT_STATE_ROOT", str(state_root))
    _write_session_binding(state_root, "session", _binding("session"))
    resolved_paths: list[Path] = []
    ledger_dirs: list[Path] = []
    original_resolver = declare_module.resolve_binding_path

    def resolve_spy(payload_cwd: str, session_id: str) -> Path:
        path = original_resolver(payload_cwd, session_id)
        resolved_paths.append(path)
        return path

    def declare_spy(channel_dir: Path, **kwargs):
        ledger_dirs.append(channel_dir)
        return {"join_batch_id": "batch-id", **kwargs}

    monkeypatch.setattr(declare_module, "resolve_binding_path", resolve_spy)
    monkeypatch.setattr(declare_module, "declare_batch", declare_spy)
    monkeypatch.setattr(declare_module, "get_backend", lambda _name: _capable_backend())

    result = declare_module._declare_join_batch_handler(
        "rectify", ["assignment"], "session", tmp_path
    )

    assert result["success"] is True
    assert resolved_paths[0].parent == ledger_dirs[0]
    assert ledger_dirs[0] == state_root / ".autoskillit" / "temp"


def test_replacement_lifecycle_rejects_mismatches_and_retains_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_root = tmp_path / "state-root"
    project_root = tmp_path / "project"
    state_root.mkdir()
    project_root.mkdir()
    session_id = "recovery-session"
    parent = "managed-parent"
    binding = _binding(
        session_id,
        entries=(_entry("rectify"), _entry("other")),
        managed_parent_id=parent,
    )
    binding_path = _write_session_binding(state_root, session_id, binding)
    channel_dir = binding_path.parent
    monkeypatch.setenv("AUTOSKILLIT_STATE_ROOT", str(state_root))
    monkeypatch.setenv("AUTOSKILLIT_AGENT_BACKEND", "claude-code")
    monkeypatch.setattr(declare_module, "get_backend", lambda _name: _capable_backend())
    diagnostics: list[dict[str, object]] = []
    monkeypatch.setattr(declare_module, "_emit_join_diagnostic", diagnostics.append)

    original = declare_module._declare_join_batch_handler(
        "rectify", ["original"], session_id, project_root
    )
    assert original["success"] is True
    original_id = str(original["join_batch_id"])
    claim_assignment(
        channel_dir,
        session_id=session_id,
        top_level_parent=parent,
        tool_use_id="original-agent",
    )
    settle_assignment(
        channel_dir,
        session_id=session_id,
        top_level_parent=parent,
        tool_use_id="original-agent",
        outcome=OUTCOME_FAILURE,
    )
    ledger_path, _lock_path = ledger_paths(channel_dir)
    rejected_baseline = ledger_path.read_bytes()

    invalid_cardinality = declare_module._declare_join_batch_handler(
        "rectify", ["one", "two"], session_id, project_root
    )
    wrong_parent = declare_module._declare_join_batch_handler(
        "rectify", ["replacement"], session_id, project_root, top_level_parent="other-parent"
    )
    wrong_skill = declare_module._declare_join_batch_handler(
        "other", ["replacement"], session_id, project_root
    )
    write_binding(binding_path, binding._replace(artifact_digest="different-artifact"))
    wrong_artifact = declare_module._declare_join_batch_handler(
        "rectify", ["replacement"], session_id, project_root
    )
    write_binding(binding_path, binding)

    assert invalid_cardinality["success"] is False
    assert "declares count=1" in str(invalid_cardinality["error"])
    assert wrong_parent["success"] is False
    assert "top_level_parent mismatch" in str(wrong_parent["error"])
    assert wrong_skill["success"] is False
    assert "predecessor skill mismatch" in str(wrong_skill["error"])
    assert wrong_artifact["success"] is False
    assert "artifact digest mismatch" in str(wrong_artifact["error"])
    assert ledger_path.read_bytes() == rejected_baseline
    rejected_state = json.loads(rejected_baseline)
    assert len(rejected_state["batches"]) == 1
    assert len(rejected_state["declaration_index"]) == 1
    assert (
        rejected_state["sessions"][session_id]["managed_parents"][parent]["active_batch_id"]
        == original_id
    )
    for tool_name in ("Bash", "Write"):
        denied = _run_join_guard(state_root, project_root, session_id, tool_name=tool_name)
        assert denied.returncode == 2
        assert json.loads(denied.stdout)["decision"] == "block"
    failed_stop = _run_join_guard(state_root, project_root, session_id)
    assert failed_stop.returncode == 2
    assert json.loads(failed_stop.stdout)["decision"] == "block"

    replacement = declare_module._declare_join_batch_handler(
        "autoskillit:rectify", ["replacement"], session_id, project_root
    )
    assert replacement["success"] is True
    replacement_id = str(replacement["join_batch_id"])
    assert replacement_id != original_id
    replacement_diagnostic = diagnostics[-1]
    assert replacement_diagnostic["status"] == "replacement_batch"
    assert replacement_diagnostic["original_join_batch_id"] == original_id
    assert replacement_diagnostic["replacement_join_batch_id"] == replacement_id
    assert replacement_diagnostic["session_id"] == session_id
    assert replacement_diagnostic["top_level_parent"] == parent
    assert replacement_diagnostic["skill_name"] == "rectify"
    replacement_state = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert len(replacement_state["batches"]) == 2
    assert len(replacement_state["declaration_index"]) == 2
    assert replacement_state["batches"][replacement_id]["wave_outcome"] == "pending"
    assert (
        replacement_state["sessions"][session_id]["managed_parents"][parent]["active_batch_id"]
        == replacement_id
    )

    pending_stop = _run_join_guard(state_root, project_root, session_id)
    assert pending_stop.returncode == 2
    assert json.loads(pending_stop.stdout)["decision"] == "block"

    claim_assignment(
        channel_dir,
        session_id=session_id,
        top_level_parent=parent,
        tool_use_id="replacement-agent",
    )
    settle_assignment(
        channel_dir,
        session_id=session_id,
        top_level_parent=parent,
        tool_use_id="replacement-agent",
        outcome=OUTCOME_SUCCESS,
    )
    final_stop = _run_join_guard(state_root, project_root, session_id)
    assert final_stop.returncode == 0, final_stop.stderr
    assert final_stop.stdout == ""

    retained = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert retained["schema_version"] == JOIN_LEDGER_SCHEMA_VERSION == 2
    assert len(retained["batches"]) == 2
    assert len(retained["declaration_index"]) == 2
    assert retained["batches"][original_id]["wave_outcome"] == "failure"
    assert retained["batches"][replacement_id]["wave_outcome"] == "complete"
    assert retained["batches"][original_id]["canonical_declaration"]
    assert retained["batches"][replacement_id]["canonical_declaration"]
    assert (
        retained["sessions"][session_id]["managed_parents"][parent]["active_batch_id"]
        == replacement_id
    )

    third = declare_module._declare_join_batch_handler(
        "rectify", ["third"], session_id, project_root
    )
    assert third["success"] is True
    third_id = str(third["join_batch_id"])
    assert diagnostics[-1]["status"] == "declared"
    assert "original_join_batch_id" not in diagnostics[-1]
    final_ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert len(final_ledger["batches"]) == 3
    assert len(final_ledger["declaration_index"]) == 3
    assert (
        final_ledger["sessions"][session_id]["managed_parents"][parent]["active_batch_id"]
        == third_id
    )
    assert final_ledger["batches"][original_id]["wave_outcome"] == "failure"
    assert final_ledger["batches"][replacement_id]["wave_outcome"] == "complete"


def test_recovery_declaration_rejects_a_stale_active_predecessor_atomically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_root = tmp_path / "state-root"
    project_root = tmp_path / "project"
    state_root.mkdir()
    project_root.mkdir()
    session_id = "stale-recovery"
    parent = "managed-parent"
    binding_path = _write_session_binding(
        state_root,
        session_id,
        _binding(session_id, managed_parent_id=parent),
    )
    channel_dir = binding_path.parent
    monkeypatch.setenv("AUTOSKILLIT_STATE_ROOT", str(state_root))
    monkeypatch.setattr(declare_module, "get_backend", lambda _name: _capable_backend())

    original = declare_module._declare_join_batch_handler(
        "rectify", ["original"], session_id, project_root
    )
    assert original["success"] is True
    original_id = str(original["join_batch_id"])
    claim_assignment(
        channel_dir,
        session_id=session_id,
        top_level_parent=parent,
        tool_use_id="original-agent",
    )
    settle_assignment(
        channel_dir,
        session_id=session_id,
        top_level_parent=parent,
        tool_use_id="original-agent",
        outcome=OUTCOME_FAILURE,
    )
    original_wave = original["wave"]
    assert isinstance(original_wave, dict)
    stale_predecessor = {**original_wave, "wave_outcome": "failure"}

    intervening = declare_batch(
        channel_dir,
        session_id=session_id,
        top_level_parent=parent,
        skill_name="rectify",
        artifact_digest="artifact-digest",
        assignments=("intervening",),
        expected_active_predecessor_id=original_id,
    )
    claim_assignment(
        channel_dir,
        session_id=session_id,
        top_level_parent=parent,
        tool_use_id="intervening-agent",
    )
    settle_assignment(
        channel_dir,
        session_id=session_id,
        top_level_parent=parent,
        tool_use_id="intervening-agent",
        outcome=OUTCOME_FAILURE,
    )
    ledger_path, _lock_path = ledger_paths(channel_dir)
    before = ledger_path.read_bytes()
    monkeypatch.setattr(
        declare_module,
        "active_batch",
        lambda *_args, **_kwargs: stale_predecessor,
    )

    result = declare_module._declare_join_batch_handler(
        "rectify", ["stale"], session_id, project_root
    )

    assert result["success"] is False
    assert "active recovery predecessor changed" in str(result["error"])
    assert original_id in str(result["error"])
    assert str(intervening["join_batch_id"]) in str(result["error"])
    assert ledger_path.read_bytes() == before
