"""Subprocess harness for interactive write-guard and join-guard composition tests.

Builds a projected plugin (a copy of the installed hooks beside a projection
manifest), binds skills through the production ``classify_invoked_skill`` and
``merge_binding`` path, and runs real guard scripts in an interactive environment.
Manifest scope fields are encoded by the production ``encode_write_scope``; bundled
skill entries are derived from the real bundled SKILL.md declarations.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import NamedTuple

from autoskillit.hook_registry import HOOKS_DIR
from autoskillit.hooks._session_binding import (
    PROJECTION_MANIFEST_SCHEMA_VERSION,
    SessionBinding,
    classify_invoked_skill,
    merge_binding,
    resolve_binding_path,
    write_binding,
)
from autoskillit.hooks._write_scope import WriteScope, encode_write_scope
from autoskillit.workspace import DefaultSkillResolver
from tests.conftest import production_interpreter_env

ARTIFACT_DIGEST = "harness-artifact"
SESSION_ID = "interactive"
_TS = "2026-09-26T00:00:00+00:00"


class GuardRuntime(NamedTuple):
    project: Path
    plugin: Path
    manifest_path: Path


class GuardOutcome(NamedTuple):
    decision: str
    reason_code: str
    message: str


def manifest_entry(write_scope: WriteScope | object, *, join_required: bool = False) -> dict:
    """One manifest entry; a non-``WriteScope`` value is written verbatim (malformed cases)."""
    return {
        "join_required": join_required,
        "child_spawn_cardinality": {},
        "semantic_digest": "semantic",
        "adaptation_digest": "adaptation",
        "projected_digest": "projected",
        "canonical_digest": "canonical",
        "write_scope": (
            encode_write_scope(write_scope) if isinstance(write_scope, WriteScope) else write_scope
        ),
    }


def bundled_entry(name: str) -> dict:
    """A manifest entry carrying the real bundled skill's declaration and join flag."""
    info = DefaultSkillResolver().resolve(name)
    assert info is not None, name
    assert info.write_scope is not None, name
    plan = info.semantic_plan
    return manifest_entry(
        info.write_scope,
        join_required=bool(plan is not None and plan.join is not None and plan.join.required),
    )


def bundled_entries(*names: str) -> dict[str, dict]:
    return {name: bundled_entry(name) for name in names}


def make_runtime(tmp_path: Path, entries: Mapping[str, Mapping[str, object]]) -> GuardRuntime:
    project = tmp_path / "project"
    (project / ".autoskillit" / "temp").mkdir(parents=True)
    plugin = tmp_path / "plugin"
    shutil.copytree(HOOKS_DIR, plugin / "hooks")
    manifest_path = tmp_path / ".plugin.autoskillit-projection.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": PROJECTION_MANIFEST_SCHEMA_VERSION,
                "artifact_digest": ARTIFACT_DIGEST,
                "incarnation_id": "harness-incarnation",
                "skills": dict(entries),
            }
        ),
        encoding="utf-8",
    )
    return GuardRuntime(project, plugin, manifest_path)


def build_binding(
    runtime: GuardRuntime, names: Sequence[str], *, session_id: str = SESSION_ID
) -> SessionBinding:
    manifest = json.loads(runtime.manifest_path.read_text(encoding="utf-8"))
    binding: SessionBinding | None = None
    for name in names:
        binding = merge_binding(
            binding,
            session_id=session_id,
            new_entry=classify_invoked_skill(manifest, name, _TS),
            artifact_digest=ARTIFACT_DIGEST,
        )
    assert binding is not None
    return binding


def bind(runtime: GuardRuntime, names: Sequence[str], *, session_id: str = SESSION_ID) -> Path:
    """Bind ``names`` in order under ``session_id`` and return the flag path."""
    flag = resolve_binding_path(str(runtime.project), session_id)
    write_binding(flag, build_binding(runtime, names, session_id=session_id))
    return flag


def interactive_env(runtime: GuardRuntime, **overrides: str) -> dict[str, str]:
    """A non-cook interactive Claude environment rooted at the runtime's project."""
    env = production_interpreter_env()
    env.pop("AUTOSKILLIT_LAUNCH_ID", None)
    env.update(
        {
            "AUTOSKILLIT_HEADLESS": "",
            "AUTOSKILLIT_AGENT_BACKEND": "claude-code",
            "AUTOSKILLIT_ALLOWED_WRITE_PREFIX": "",
            "AUTOSKILLIT_ALLOWED_WRITE_PREFIXES": "",
            "AUTOSKILLIT_STATE_ROOT": str(runtime.project),
        }
    )
    env.update(overrides)
    return env


def run_guard(
    runtime: GuardRuntime,
    script: str,
    payload: Mapping[str, object],
    *,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(runtime.plugin / "hooks" / "guards" / script)],
        input=json.dumps(dict(payload)),
        capture_output=True,
        text=True,
        env=dict(env) if env is not None else interactive_env(runtime),
        cwd=runtime.project,
        timeout=10,
        check=False,
    )


def _last_decision(runtime: GuardRuntime) -> dict:
    lines = (
        (runtime.project / ".autoskillit" / "temp" / "guard_decisions.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    return json.loads(lines[-1])


def write_guard(
    runtime: GuardRuntime,
    target: Path | None = None,
    *,
    session_id: str = SESSION_ID,
    tool_name: str = "Write",
    tool_input: Mapping[str, object] | None = None,
    env: Mapping[str, str] | None = None,
) -> GuardOutcome:
    """Run the real write guard and return its decision, reason code, and message."""
    payload = {
        "tool_name": tool_name,
        "session_id": session_id,
        "cwd": str(runtime.project),
        "tool_input": dict(tool_input) if tool_input is not None else {"file_path": str(target)},
    }
    result = run_guard(runtime, "write_guard.py", payload, env=env)
    assert result.returncode == 0, result.stderr
    record = _last_decision(runtime)
    if result.stdout:
        output = json.loads(result.stdout)["hookSpecificOutput"]
        return GuardOutcome(
            output["permissionDecision"], record["reason"], output["permissionDecisionReason"]
        )
    return GuardOutcome(record["decision"], record["reason"], "")


def temp_target(runtime: GuardRuntime, relative: str) -> Path:
    return runtime.project / ".autoskillit" / "temp" / relative
