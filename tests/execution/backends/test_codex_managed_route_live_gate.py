"""Credentialed end-to-end gate for the interactive managed Codex route."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from autoskillit.core import SemanticAdaptationContext, SkillExecutionRole
from autoskillit.hooks._join import OUTCOME_PENDING
from autoskillit.hooks._join_ledger import active_batch
from autoskillit.hooks._session_binding import resolve_channel_dir
from autoskillit.server._managed_join_prelaunch import prepare_managed_join_context
from autoskillit.workspace import (
    DefaultSkillResolver,
    EffectiveSkillCatalog,
    SkillCatalogEntry,
    SkillProjectionContext,
    materialize_agent_skill_tree,
)
from tests.conftest import production_interpreter_env
from tests.execution.backends._live_codex_parent import (
    prepare_live_codex_parent,
    run_live_codex_parent,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.large, pytest.mark.timeout(1200)]

_LIVE_ENV = "AUTOSKILLIT_CODEX_MANAGED_ROUTE_LIVE"
_AUTH_ENV_NAMES = ("CODEX_API_KEY", "OPENAI_API_KEY")
_SOURCE_AUTH = Path("~/.codex/auth.json").expanduser()
_PARENT_MODEL = "gpt-5.6-luna"
_MAX_CAPTURE_BYTES = 4 * 1024 * 1024

_skip_unless_live_gate = pytest.mark.skipif(
    not os.environ.get(_LIVE_ENV)
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


def _tool_name(payload: dict[str, Any]) -> str:
    name = str(payload.get("name", ""))
    namespace = str(payload.get("namespace", ""))
    return f"{namespace}__{name}" if namespace else name


def _calls(events: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    return [
        payload
        for event in events
        if event.get("type") == "response_item"
        and isinstance(payload := event.get("payload"), dict)
        and payload.get("type") in {"function_call", "custom_tool_call"}
        and _tool_name(payload) == f"mcp__autoskillit__{name}"
    ]


def _outputs(events: list[dict[str, Any]]) -> dict[str, str]:
    return {
        str(payload.get("call_id", "")): str(payload.get("output", ""))
        for event in events
        if event.get("type") == "response_item"
        and isinstance(payload := event.get("payload"), dict)
        and payload.get("type") in {"function_call_output", "custom_tool_call_output"}
    }


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


def _tool_result(output: str, *, required_keys: frozenset[str]) -> dict[str, Any]:
    matches = [
        value
        for value in _json_values(output)
        if isinstance(value, dict)
        and value.get("success") is True
        and required_keys.issubset(value)
    ]
    assert len(matches) == 1, output[-4_000:]
    return matches[0]


def _write_bundled_models_cache(profile_codex_home: Path, env: dict[str, str]) -> None:
    completed = subprocess.run(  # noqa: S603
        ["codex", "debug", "models", "--bundled"],
        env=env,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr[-4_000:].decode("utf-8", errors="replace")
    profile_codex_home.joinpath("models_cache.json").write_bytes(completed.stdout)


def _stop_guard_env(env: dict[str, str]) -> dict[str, str]:
    """Combine the live parent environment with production interpreter settings."""
    isolated = production_interpreter_env()
    isolated.update(env)
    return isolated


def _stop_guard(
    *, repository: Path, env: dict[str, str], session_id: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [
            sys.executable,
            str(Path(__file__).parents[3] / "src/autoskillit/hooks/guards/join_stop_guard.py"),
        ],
        cwd=repository,
        env=_stop_guard_env(env),
        input=json.dumps({"session_id": session_id, "cwd": str(repository)}),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _parent_prompt(run_id: str) -> str:
    return f"""
Use the projected $dry-walkthrough skill for live managed-route gate {run_id}. Call
open_kitchen with no arguments. Then call the autoskillit MCP tool run_fixed_batch exactly once
with skill_name "dry-walkthrough", idempotency_key "managed-route-{run_id}", and exactly one
assignment: role "delegated-worker", label "live-worker", and task_prompt instructing the
worker to run `sleep 20` once before returning a concise completion result. Do not call any other
batch tool. When run_fixed_batch succeeds, call read_fixed_batch_result exactly once using its
batch_id and result_reference, skill_name "dry-walkthrough", assignment_id "", offset 0, and
page_size 8192. Do not describe tool results in place of calling the tools.
""".strip()


@_skip_unless_live_gate
@pytest.mark.smoke
def test_live_codex_interactive_managed_route_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise real MCP results and real Stop decisions under one join identity."""
    from autoskillit.execution.backends.codex import CodexBackend

    repository = tmp_path / "repository"
    repository.mkdir()
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
    _write_bundled_models_cache(prepared.profile_codex_home, prepared.env)
    backend = CodexBackend()
    parent_id = uuid4().hex
    issuance = prepare_managed_join_context(
        backend=backend,
        configured_model=_PARENT_MODEL,
        state_root=repository,
        parent_id=parent_id,
        launch_context="interactive",
    )
    assert isinstance(issuance, SemanticAdaptationContext), issuance
    attestation = issuance.managed_join_attestation
    assert attestation is not None

    source = DefaultSkillResolver().resolve("dry-walkthrough")
    assert source is not None and source.semantic_plan is not None
    catalog = EffectiveSkillCatalog(
        skills=(SkillCatalogEntry.from_skill_info(source),),
        execution_role=SkillExecutionRole.SESSION,
    )
    materialize_agent_skill_tree(
        prepared.session_home / "add-dir" / "skills",
        catalog,
        SkillProjectionContext(
            cwd=repository,
            catalog=catalog,
            backend=backend,
            adaptation_context=issuance,
            managed_codex_route="interactive-parent",
            parent_sandbox_mode="workspace-write",
        ),
    )
    backend.configure_managed_session_dir(
        prepared.session_home,
        attestation=attestation,
        route="interactive-parent",
    )
    catalog_digest = hashlib.sha256(
        (prepared.session_home / "models_cache.json").read_bytes()
    ).hexdigest()
    prepared.env.update(
        {
            "AUTOSKILLIT_AGENT_BACKEND": "codex",
            "AUTOSKILLIT_AGENT_BACKEND__BACKEND": "codex",
            "AUTOSKILLIT_MANAGED_JOIN_PARENT_ID": parent_id,
            "AUTOSKILLIT_PROJECT_DIR": str(repository),
            "AUTOSKILLIT_SESSION_TYPE": "orchestrator",
        }
    )

    run_id = uuid4().hex
    stdout_path = tmp_path / "codex.stdout.jsonl"
    stderr_path = tmp_path / "codex.stderr.txt"
    completed: dict[str, subprocess.CompletedProcess[Any]] = {}
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        parent = threading.Thread(
            target=lambda: completed.setdefault(
                "result",
                run_live_codex_parent(
                    env=prepared.env,
                    cwd=repository,
                    model=_PARENT_MODEL,
                    prompt=_parent_prompt(run_id),
                    timeout=int(os.environ.get("AUTOSKILLIT_CODEX_MANAGED_ROUTE_TIMEOUT", "900")),
                    stdout=stdout,
                    stderr=stderr,
                    text=False,
                    sandbox="workspace-write",
                ),
            )
        )
        parent.start()
        flag_dir = resolve_channel_dir(repository)
        deadline = time.monotonic() + 180
        pending_batch: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            batch = active_batch(flag_dir, session_id=parent_id, top_level_parent=parent_id)
            if isinstance(batch, dict) and batch.get("wave_outcome") == OUTCOME_PENDING:
                pending_batch = batch
                break
            if not parent.is_alive():
                break
            time.sleep(0.25)
        assert pending_batch is not None, stderr_path.read_text(
            encoding="utf-8", errors="replace"
        )[-4_000:]
        blocked = _stop_guard(repository=repository, env=prepared.env, session_id=parent_id)
        assert blocked.returncode == 2
        assert json.loads(blocked.stdout)["decision"] == "block"
        parent.join(timeout=900)
    assert not parent.is_alive(), "live managed parent did not finish"
    result = completed["result"]
    assert result.returncode == 0, stderr_path.read_text(encoding="utf-8", errors="replace")[
        -4_000:
    ]

    released = _stop_guard(repository=repository, env=prepared.env, session_id=parent_id)
    assert released.returncode == 0, released.stdout
    assert (
        hashlib.sha256((prepared.session_home / "models_cache.json").read_bytes()).hexdigest()
        == catalog_digest
    )

    events = _bounded_events(stdout_path)
    run_calls = _calls(events, "run_fixed_batch")
    read_calls = _calls(events, "read_fixed_batch_result")
    assert len(run_calls) == len(read_calls) == 1
    outputs = _outputs(events)
    run_result = _tool_result(
        outputs[str(run_calls[0].get("call_id", ""))],
        required_keys=frozenset({"batch_id", "result_reference"}),
    )
    read_result = _tool_result(
        outputs[str(read_calls[0].get("call_id", ""))],
        required_keys=frozenset({"content"}),
    )
    assignments: list[dict[str, Any]] | None = None
    for value in _json_values(read_result["content"]):
        candidate = value.get("assignments") if isinstance(value, dict) else None
        if isinstance(candidate, list) and all(isinstance(item, dict) for item in candidate):
            assignments = candidate
            break
    assert assignments is not None
    assert [item["assignment_id"] for item in assignments] == [f"{run_result['batch_id']}:0"]
    assert len(assignments) == 1 and assignments[0]["outcome"] != OUTCOME_PENDING
