"""Credentialed end-to-end gate for the interactive managed Codex route."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import threading
import tomllib
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from autoskillit.core import (
    CODEX_HOME_ENV_VAR,
    LAUNCH_ID_ENV_VAR,
    MANAGED_JOIN_PARENT_ID_ENV_VAR,
    SemanticAdaptationContext,
    SkillExecutionRole,
    write_registry_entry,
    write_versioned_json,
)
from autoskillit.hooks._join import OUTCOME_SUCCESS
from autoskillit.hooks._join_ledger import (
    active_batch,
    admit_assignment,
    declare_batch,
    settle_assignment,
)
from autoskillit.hooks._runtime._hook_settings import session_managed_scope
from autoskillit.hooks._session_binding import (
    read_binding,
    resolve_binding_path,
    resolve_channel_dir,
)
from autoskillit.server._managed_join_attestation import _write_managed_parent_binding
from autoskillit.server.managed_join_prelaunch import prepare_managed_join_context
from autoskillit.workspace import (
    DefaultSkillResolver,
    EffectiveSkillCatalog,
    SkillCatalogEntry,
    SkillProjectionContext,
    materialize_agent_skill_tree,
)
from autoskillit.workspace._projected_artifact._publication import (
    _projection_skills_manifest,
)
from tests.execution.backends._live_codex_parent import (
    prepare_live_codex_parent,
    run_live_codex_parent_bounded,
)

pytestmark = [
    pytest.mark.layer("execution"),
    pytest.mark.large,
    pytest.mark.smoke,
    pytest.mark.timeout(1200),
    pytest.mark.ambient_env("CODEX_API_KEY", "OPENAI_API_KEY"),
]

_LIVE_ENV = "AUTOSKILLIT_CODEX_MANAGED_ROUTE_LIVE"
_AUTH_ENV_NAMES = ("CODEX_API_KEY", "OPENAI_API_KEY")
_SOURCE_AUTH = Path("~/.codex/auth.json").expanduser()
_PARENT_MODEL = "gpt-5.6-luna"
_MAX_CAPTURE_BYTES = 4 * 1024 * 1024

_skip_unless_live_gate = pytest.mark.skipif(
    os.environ.get(_LIVE_ENV) != "1"
    or not shutil.which("codex")
    or not any(os.environ.get(name) for name in _AUTH_ENV_NAMES)
    and not _SOURCE_AUTH.is_file(),
    reason=f"Set {_LIVE_ENV}=1 and provide Codex authentication for this live gate",
)


def _bounded_events(path: Path) -> list[dict[str, Any]]:
    assert path.stat().st_size <= _MAX_CAPTURE_BYTES, f"oversized live artifact: {path}"
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _calls(events: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    return [
        item
        for event in events
        if event.get("type") == "item.completed"
        and isinstance(item := event.get("item"), dict)
        and item.get("type") == "mcp_tool_call"
        and item.get("server") == "autoskillit"
        and item.get("tool") == name
    ]


def _json_values(value: object) -> list[object]:
    values = [value]
    if isinstance(value, str):
        try:
            values.extend(_json_values(json.loads(value)))
        except json.JSONDecodeError:
            pass
    elif isinstance(value, dict):
        for child in value.values():
            values.extend(_json_values(child))
    elif isinstance(value, list):
        for child in value:
            values.extend(_json_values(child))
    return values


def _parent_prompt(run_id: str) -> str:
    return f"""
For live managed-route gate {run_id}, call open_kitchen with no arguments once. Then call
AutoSkillit's run_python with callable "textwrap.dedent" and args {{"text":"LIVE_FOLLOWUP_TOOL"}}
exactly once, then reply with LIVE_COOK_OK. Do not call any batch or agent tool.
""".strip()


def _run_denial_then_release(
    *,
    repository: Path,
    env: dict[str, str],
    launch_id: str,
    artifact_digest: str,
    log_dir: Path,
    label: str,
    evidence_dir: Path,
    extra_overrides: tuple[str, ...] = (),
) -> str:
    flag_dir = resolve_channel_dir(repository)
    batch = declare_batch(
        flag_dir,
        session_id=launch_id,
        top_level_parent=launch_id,
        skill_name="dry-walkthrough",
        artifact_digest=artifact_digest,
        assignments=(label,),
    )
    assignment = batch["assignments"][0]
    tool_use_id = f"live-{label}-{uuid4().hex}"
    attempt_id = f"attempt-{uuid4().hex}"
    run_id = f"run-{uuid4().hex}"
    admit_assignment(
        flag_dir,
        batch_id=str(batch["join_batch_id"]),
        assignment_id=str(assignment["assignment_id"]),
        attempt_id=attempt_id,
        run_id=run_id,
    )
    diagnostic_path = log_dir / "join_diagnostics.jsonl"
    baseline_blocks = (
        sum(
            row.get("gate") == "join_stop_guard" and row.get("status") == "block"
            for row in _bounded_events(diagnostic_path)
        )
        if diagnostic_path.is_file()
        else 0
    )
    baseline_followup_blocks = (
        sum(
            row.get("gate") == "join_followup_guard" and row.get("status") == "block"
            for row in _bounded_events(diagnostic_path)
        )
        if diagnostic_path.is_file()
        else 0
    )

    finished = threading.Event()
    observed: set[str] = set()
    watcher_errors: list[BaseException] = []

    def _release_after_denial() -> None:
        try:
            while not finished.wait(0.1):
                if not diagnostic_path.is_file():
                    continue
                rows = _bounded_events(diagnostic_path)
                for gate, baseline in (
                    ("join_stop_guard", baseline_blocks),
                    ("join_followup_guard", baseline_followup_blocks),
                ):
                    if (
                        sum(
                            row.get("gate") == gate and row.get("status") == "block"
                            for row in rows
                        )
                        > baseline
                    ):
                        observed.add(gate)
                if len(observed) == 2:
                    settle_assignment(
                        flag_dir,
                        session_id=launch_id,
                        top_level_parent=launch_id,
                        tool_use_id=tool_use_id,
                        outcome=OUTCOME_SUCCESS,
                        batch_id=str(batch["join_batch_id"]),
                        assignment_id=str(assignment["assignment_id"]),
                        attempt_id=attempt_id,
                        run_id=run_id,
                    )
                    return
        except BaseException as exc:
            watcher_errors.append(exc)

    watcher = threading.Thread(target=_release_after_denial)
    watcher.start()
    try:
        completed = run_live_codex_parent_bounded(
            env=env,
            cwd=repository,
            model=_PARENT_MODEL,
            prompt=(
                "Call open_kitchen with no arguments, then call run_python with callable "
                f'textwrap.dedent and args {{"text":"LIVE_RELEASE_{label.upper()}"}}. '
                "If denied, attempt to finish with PENDING_STOP_PROBE. When continued, "
                "retry the call and return its result."
            ),
            timeout=180,
            max_output_bytes=_MAX_CAPTURE_BYTES,
            capture_dir=evidence_dir / label,
            extra_overrides=extra_overrides,
            trust_generated_hooks=True,
            sandbox="workspace-write",
        )
    finally:
        finished.set()
        watcher.join(timeout=10)
    assert not watcher.is_alive(), "pending-wave watcher did not finish"
    if watcher_errors:
        raise watcher_errors[0]
    assert "join_stop_guard" in observed, "native Codex Stop did not observe the pending wave"
    assert "join_followup_guard" in observed, "native Codex follow-up did not observe the wave"
    assert completed.returncode == 0, completed.stderr[-4_000:].decode("utf-8", errors="replace")
    events = [
        json.loads(line)
        for line in completed.stdout.decode("utf-8", errors="replace").splitlines()
        if line.strip().startswith("{")
    ]
    thread_ids = {
        str(event["thread_id"])
        for event in events
        if event.get("type") == "thread.started" and event.get("thread_id")
    }
    assert len(thread_ids) == 1
    thread_id = thread_ids.pop()
    allow_before_resume = sum(
        row.get("gate") == "join_stop_guard" and row.get("status") == "allow"
        for row in _bounded_events(diagnostic_path)
    )
    resumed = run_live_codex_parent_bounded(
        env=env,
        cwd=repository,
        model=_PARENT_MODEL,
        prompt=f"Reply with LIVE_RESUMED_{label.upper()} and do not call any tool.",
        timeout=int(os.environ.get("AUTOSKILLIT_CODEX_MANAGED_ROUTE_TIMEOUT", "900")),
        max_output_bytes=_MAX_CAPTURE_BYTES,
        capture_dir=evidence_dir / f"{label}-resumed",
        extra_overrides=extra_overrides,
        trust_generated_hooks=True,
        resume_thread_id=thread_id,
        sandbox="workspace-write",
    )
    assert resumed.returncode == 0, resumed.stderr[-4_000:].decode("utf-8", errors="replace")
    resumed_ids = {
        str(event["thread_id"])
        for line in resumed.stdout.decode("utf-8", errors="replace").splitlines()
        if line.strip().startswith("{")
        and isinstance((event := json.loads(line)), dict)
        and event.get("type") == "thread.started"
        and event.get("thread_id")
    }
    assert resumed_ids == {thread_id}
    assert (
        sum(
            row.get("gate") == "join_stop_guard" and row.get("status") == "allow"
            for row in _bounded_events(diagnostic_path)
        )
        > allow_before_resume
    )
    return thread_id


@_skip_unless_live_gate
@pytest.mark.smoke
@pytest.mark.parametrize("scenario", ("cook", "batch"))
def test_live_codex_interactive_managed_route_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
    native_join_evidence: Path,
    scenario: str,
) -> None:
    """Exercise real MCP results and real Stop decisions under one join identity."""
    from autoskillit.execution.backends.codex import CodexBackend

    repository = Path.cwd() / ".autoskillit" / "temp" / f"codex-managed-live-{uuid4().hex}"
    repository.mkdir(parents=True)
    request.addfinalizer(lambda: shutil.rmtree(repository, ignore_errors=True))
    (repository / ".autoskillit" / "temp").mkdir(parents=True)
    (repository / "README.md").write_text("managed route live gate\n", encoding="utf-8")
    source_auth = _SOURCE_AUTH if _SOURCE_AUTH.is_file() else tmp_path / "missing-auth.json"
    prepared = prepare_live_codex_parent(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        source_auth=source_auth,
        agent_defs=(),
        parent_sandbox_mode="workspace-write",
        copy_source_auth=True,
    )

    def preserve_parent_transcripts() -> None:
        for transcript in (prepared.session_home / "sessions").rglob("*.jsonl"):
            assert transcript.stat().st_size <= _MAX_CAPTURE_BYTES
            shutil.copyfile(transcript, native_join_evidence / transcript.name)

    request.addfinalizer(preserve_parent_transcripts)
    backend = CodexBackend()
    launch_id = uuid4().hex[:16]
    issuance = prepare_managed_join_context(
        backend=backend,
        configured_model=_PARENT_MODEL,
        state_root=repository,
        parent_id=launch_id,
        launch_context="interactive",
    )
    assert isinstance(issuance, SemanticAdaptationContext), issuance
    attestation = issuance.managed_join_attestation
    assert attestation is not None

    skill_name = "analyze-pipeline-health" if scenario == "batch" else "dry-walkthrough"
    source = DefaultSkillResolver().resolve(skill_name)
    assert source is not None and source.semantic_plan is not None
    catalog = EffectiveSkillCatalog(
        skills=(SkillCatalogEntry.from_skill_info(source),),
        execution_role=SkillExecutionRole.SESSION,
    )
    documents = materialize_agent_skill_tree(
        prepared.session_home / "add-dir" / "skills",
        catalog,
        SkillProjectionContext(
            cwd=repository,
            catalog=catalog,
            substitutions={"{{AUTOSKILLIT_TEMP}}": str(repository / ".autoskillit" / "temp")},
            gating=False,
            backend=backend,
            adaptation_context=issuance,
            managed_codex_route="interactive-parent",
            parent_sandbox_mode="workspace-write",
        ),
    )
    write_versioned_json(
        backend.projected_manifest_path(prepared.session_home),
        {
            "schema_version": 2,
            "artifact_digest": source.canonical_digest,
            "incarnation_id": f"live-{uuid4().hex}",
            "skills": _projection_skills_manifest(tuple(catalog.skills), documents),
        },
        schema_version=2,
    )
    backend.configure_managed_session_dir(
        prepared.session_home,
        adaptation_context=issuance,
        route="interactive-parent",
    )
    catalog_digest = hashlib.sha256(
        backend.read_managed_session_catalog(prepared.session_home)
    ).hexdigest()
    assert catalog_digest == attestation.codex_catalog_digest
    monkeypatch.setenv(CODEX_HOME_ENV_VAR, str(prepared.session_home))
    binding_path = resolve_binding_path(str(repository), launch_id)
    _write_managed_parent_binding(
        binding_path=binding_path,
        binding_session_id=launch_id,
        normalized_skill_name=skill_name,
        backend=backend,
        attestation=attestation,
    )
    binding = read_binding(binding_path)
    assert binding is not None
    assert binding.managed_parent_id == launch_id
    assert binding.managed_leaf_id == ""
    assert session_managed_scope(str(repository), launch_id) == (launch_id, "")
    write_registry_entry(repository, launch_id, "cook", None)

    log_dir = native_join_evidence / "hook-logs"
    prepared.env.update(
        {
            "AUTOSKILLIT_AGENT_BACKEND": "codex",
            "AUTOSKILLIT_AGENT_BACKEND__BACKEND": "codex",
            LAUNCH_ID_ENV_VAR: launch_id,
            MANAGED_JOIN_PARENT_ID_ENV_VAR: launch_id,
            "AUTOSKILLIT_LOG_DIR": str(log_dir),
            "AUTOSKILLIT_PROJECT_DIR": str(repository),
            "AUTOSKILLIT_SESSION_TYPE": "orchestrator",
        }
    )

    config_text = (prepared.session_home / "config.toml").read_text(encoding="utf-8")
    server = tomllib.loads(config_text)["mcp_servers"]["autoskillit"]
    server_command = shlex.join([server["command"], *server.get("args", [])])
    server_command += f" 2>>{shlex.quote(str(native_join_evidence / 'mcp.stderr.txt'))}"
    (native_join_evidence / "config.toml").write_text(config_text, encoding="utf-8")
    assert "join_followup_guard" in config_text
    assert "join_stop_guard" in config_text
    assert "join_claim_guard" not in config_text
    assert "join_settle_guard" not in config_text

    run_id = uuid4().hex
    prompt = _parent_prompt(run_id)
    if scenario == "batch":
        prompt = f"""
Call open_kitchen with no arguments, then run_fixed_batch exactly once with
skill_name "{skill_name}", idempotency_key "live-{run_id}", and one assignment:
role "autoskillit:session-log-reader", label "live-worker", runtime_key "live-packet",
task_prompt "Use only this supplied evidence packet: the native gate reached its worker.
Return LIVE_BATCH_CHILD followed by ---pipeline-health-result---.
This is the complete leaf assignment; no filesystem access is needed."
After success, call read_fixed_batch_result with the returned batch_id and result_reference,
skill_name "{skill_name}", assignment_id "", offset 0, page_size 8192.
Use these real tools and finish only after reading the completed batch result.
""".strip()
    stdout_path = native_join_evidence / scenario / "stdout.txt"
    overrides = (
        'mcp_servers.autoskillit.command="/bin/sh"',
        f"mcp_servers.autoskillit.args={json.dumps(['-c', 'exec ' + server_command])}",
        'mcp_servers.autoskillit.tools.open_kitchen.approval_mode="approve"',
        'mcp_servers.autoskillit.tools.run_python.approval_mode="approve"',
        'mcp_servers.autoskillit.tools.run_fixed_batch.approval_mode="approve"',
        'mcp_servers.autoskillit.tools.read_fixed_batch_result.approval_mode="approve"',
        'mcp_servers.autoskillit.env.AUTOSKILLIT_FEATURES__EXPERIMENTAL_ENABLED="true"',
        f"mcp_servers.autoskillit.env.AUTOSKILLIT_LOG_DIR={json.dumps(str(log_dir))}",
        f"log_dir={json.dumps(str(native_join_evidence / 'codex-logs'))}",
    )
    result = run_live_codex_parent_bounded(
        env=prepared.env,
        cwd=repository,
        model=_PARENT_MODEL,
        prompt=prompt,
        timeout=int(os.environ.get("AUTOSKILLIT_CODEX_MANAGED_ROUTE_TIMEOUT", "900")),
        max_output_bytes=_MAX_CAPTURE_BYTES,
        capture_dir=stdout_path.parent,
        extra_overrides=overrides,
        trust_generated_hooks=True,
        sandbox="workspace-write",
    )
    assert result.returncode == 0, result.stderr[-4_000:].decode("utf-8", errors="replace")
    events = _bounded_events(stdout_path)
    thread_ids = {
        str(event["thread_id"])
        for event in events
        if event.get("type") == "thread.started" and event.get("thread_id")
    }
    assert len(thread_ids) == 1
    assert launch_id not in thread_ids
    assert (
        hashlib.sha256(backend.read_managed_session_catalog(prepared.session_home)).hexdigest()
        == catalog_digest
    )
    assert (
        backend.verify_managed_session_dir(
            prepared.session_home, attestation, "interactive-parent"
        )
        == []
    )
    if scenario == "batch":
        run_calls = _calls(events, "run_fixed_batch")
        read_calls = _calls(events, "read_fixed_batch_result")
        assert len(run_calls) == len(read_calls) == 1, json.dumps(events)[-8_000:]
        run_result = next(
            value
            for value in _json_values(run_calls[0].get("result"))
            if isinstance(value, dict) and {"batch_id", "result_reference"} <= value.keys()
        )
        assert run_result["success"] is True
        assignments = next(
            value["assignments"]
            for value in _json_values(read_calls[0].get("result"))
            if isinstance(value, dict) and isinstance(value.get("assignments"), list)
        )
        assert len(assignments) == 1
        assert assignments[0]["assignment_id"] == f"{run_result['batch_id']}:0"
        assert assignments[0]["outcome"] == OUTCOME_SUCCESS
        return

    followup = _calls(events, "run_python")
    assert len(followup) == 1
    assert followup[0].get("error") is None
    assert "LIVE_FOLLOWUP_TOOL" in _json_values(followup[0].get("result"))
    assert not _calls(events, "run_fixed_batch")

    diagnostics = _bounded_events(log_dir / "join_diagnostics.jsonl")
    bypass_gates = {row.get("gate") for row in diagnostics if row.get("status") == "cook_bypass"}
    assert {"join_followup_guard", "join_stop_guard"}.issubset(bypass_gates)
    assert (
        active_batch(
            resolve_channel_dir(repository),
            session_id=launch_id,
            top_level_parent=launch_id,
        )
        is None
    )

    assert binding.artifact_digest
    write_registry_entry(repository, launch_id, "order", None)
    noncook_thread = _run_denial_then_release(
        repository=repository,
        env=prepared.env,
        launch_id=launch_id,
        artifact_digest=binding.artifact_digest,
        log_dir=log_dir,
        label="noncook",
        evidence_dir=native_join_evidence,
        extra_overrides=overrides,
    )
    headless_env = {
        **prepared.env,
        "AUTOSKILLIT_HEADLESS": "1",
        "AUTOSKILLIT_SESSION_TYPE": "skill",
    }
    write_registry_entry(repository, launch_id, "cook", None)
    headless_thread = _run_denial_then_release(
        repository=repository,
        env=headless_env,
        launch_id=launch_id,
        artifact_digest=binding.artifact_digest,
        log_dir=log_dir,
        label="headless",
        evidence_dir=native_join_evidence,
        extra_overrides=overrides,
    )
    assert len({*thread_ids, noncook_thread, headless_thread}) == 3
    stop_rows = [
        row
        for row in _bounded_events(log_dir / "join_diagnostics.jsonl")
        if row.get("gate") == "join_stop_guard"
    ]
    assert sum(row.get("status") == "block" for row in stop_rows) >= 2
    assert sum(row.get("status") == "allow" for row in stop_rows) >= 2
    assert all(row.get("session_id") == launch_id for row in stop_rows)
    assert all(
        row.get("managed_parent_id", row.get("top_level_parent")) == launch_id for row in stop_rows
    )
    followup_rows = [
        row
        for row in _bounded_events(log_dir / "join_diagnostics.jsonl")
        if row.get("gate") == "join_followup_guard"
    ]
    assert sum(row.get("status") == "block" for row in followup_rows) >= 2
