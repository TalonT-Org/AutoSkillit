"""Codex-true apply_patch coverage for hooks that inspect edit targets."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from jsonschema.validators import validator_for

from autoskillit.core.paths import pkg_root
from tests._hook_protocol_oracle import (
    CODEX_PROTOCOL_VERSION,
    claude_verdict,
    codex_verdict,
    run_hook,
)

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.medium]

HOOKS_DIR = pkg_root() / "hooks"
SCHEMA_DIR = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "codex_hook_protocol"
    / CODEX_PROTOCOL_VERSION
)
_BACKENDS = [
    pytest.param("codex", id="codex-apply-patch"),
    pytest.param("claude", id="claude-write"),
]


def _codex_payload(
    event_name: str,
    *,
    cwd: Path,
    command: str,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "cwd": str(cwd),
        "hook_event_name": event_name,
        "model": "test-model",
        "permission_mode": "default",
        "session_id": "session-1",
        "tool_input": {"command": command},
        "tool_name": "apply_patch",
        "tool_use_id": "tool-use-1",
        "transcript_path": None,
        "turn_id": "turn-1",
    }
    if event_name == "PostToolUse":
        payload["tool_response"] = "File edit completed."
    schema_slug = {"PreToolUse": "pre-tool-use", "PostToolUse": "post-tool-use"}[event_name]
    schema_name = schema_slug + ".command.input.schema.json"
    schema = json.loads((SCHEMA_DIR / schema_name).read_text(encoding="utf-8"))
    validator_type = validator_for(schema)
    validator_type.check_schema(schema)
    validator_type(schema).validate(payload)
    return payload


def _claude_payload(event_name: str, *, file_path: str) -> dict[str, object]:
    payload: dict[str, object] = {
        "hook_event_name": event_name,
        "tool_name": "Write",
        "tool_input": {"file_path": file_path},
    }
    if event_name == "PostToolUse":
        payload["tool_response"] = "File edit completed."
    return payload


def _event(
    backend: str,
    event_name: str,
    *,
    cwd: Path,
    path: str,
    post_tool_use: bool = False,
) -> dict[str, object]:
    if backend == "codex":
        verb = "PostToolUse" if post_tool_use else "PreToolUse"
        return _codex_payload(
            verb,
            cwd=cwd,
            command=(
                f"*** Begin Patch\n*** Update File: {path}\n@@ -1 +1 @@\n-old\n+new\n*** End Patch"
            ),
        )
    return _claude_payload(event_name, file_path=path)


def _run(
    script_name: str,
    event: dict[str, object],
    *,
    env: dict[str, str] | None = None,
    unset: tuple[str, ...] = (),
    cwd: Path | None = None,
):
    return run_hook(HOOKS_DIR / script_name, event, env=env, unset=unset, cwd=cwd)


def _assert_verdict(
    backend: str,
    event: dict[str, object],
    emission,
    *,
    status: str,
    context_fragment: str | None = None,
) -> None:
    verdict_fn: Callable = codex_verdict if backend == "codex" else claude_verdict
    verdict = verdict_fn(
        event,
        exit_code=emission.exit_code,
        stdout=emission.stdout,
        stderr=emission.stderr,
    )
    assert verdict.status == status
    if context_fragment is not None:
        assert len(verdict.contexts) == 1
        assert context_fragment in verdict.contexts[0]


@pytest.mark.parametrize("backend", _BACKENDS)
def test_generated_file_guard_blocks_settings_edit(tmp_path: Path, backend: str) -> None:
    event = _event(
        backend,
        "PreToolUse",
        cwd=tmp_path,
        path=".claude/settings.json"
        if backend == "codex"
        else str(tmp_path / ".claude/settings.json"),
    )
    emission = _run("guards/generated_file_write_guard.py", event)

    assert emission.exit_code == 0
    _assert_verdict(backend, event, emission, status="blocked")


def test_generated_file_guard_blocks_rename_into_settings(tmp_path: Path) -> None:
    event = _codex_payload(
        "PreToolUse",
        cwd=tmp_path,
        command=(
            "*** Begin Patch\n"
            "*** Update File: project/config.py\n"
            "*** Move to: .claude/settings.json\n"
            "@@ -1 +1 @@\n-old\n+new\n"
            "*** End Patch"
        ),
    )
    emission = _run("guards/generated_file_write_guard.py", event)

    assert emission.exit_code == 0
    _assert_verdict("codex", event, emission, status="blocked")


@pytest.mark.parametrize("backend", _BACKENDS)
def test_planner_result_guard_blocks_noncanonical_edit(tmp_path: Path, backend: str) -> None:
    path = ".autoskillit/planner/work_packages/P1-A1-WP2a_result.json"
    event = _event(
        backend,
        "PreToolUse",
        cwd=tmp_path,
        path=path if backend == "codex" else str(tmp_path / path),
    )
    emission = _run(
        "guards/planner_result_naming_guard.py",
        event,
        env={"AUTOSKILLIT_HEADLESS": "1"},
    )

    assert emission.exit_code == 0
    _assert_verdict(backend, event, emission, status="blocked")


@pytest.mark.parametrize("backend", _BACKENDS)
def test_recipe_write_advisor_completes_with_context(tmp_path: Path, backend: str) -> None:
    path = ".autoskillit/recipes/example.yaml"
    event = _event(
        backend,
        "PreToolUse",
        cwd=tmp_path,
        path=path if backend == "codex" else str(tmp_path / path),
    )
    emission = _run(
        "guards/recipe_write_advisor.py",
        event,
        unset=("AUTOSKILLIT_HEADLESS",),
    )

    assert emission.exit_code == 0
    _assert_verdict(backend, event, emission, status="completed", context_fragment="write-recipe")


@pytest.mark.parametrize("backend", _BACKENDS)
def test_lint_after_edit_completes_with_autofix_context(tmp_path: Path, backend: str) -> None:
    edited = tmp_path / "linted.py"
    edited.write_text("x=1\n", encoding="utf-8")
    path = "linted.py" if backend == "codex" else str(edited)
    event = _event(backend, "PostToolUse", cwd=tmp_path, path=path, post_tool_use=True)
    emission = _run(
        "lint_after_edit_hook.py",
        event,
        env={
            "AUTOSKILLIT_HEADLESS": "1",
            "AUTOSKILLIT_SKILL_NAME": "implement-worktree",
        },
    )

    from autoskillit.hooks.lint_after_edit_hook import LINT_AUTOFIX_TRIGGER

    assert emission.exit_code == 0
    _assert_verdict(
        backend,
        event,
        emission,
        status="completed",
        context_fragment=LINT_AUTOFIX_TRIGGER,
    )


def test_write_guard_resolves_codex_relative_target_against_payload_cwd(
    tmp_path: Path,
) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    event = _codex_payload(
        "PreToolUse",
        cwd=allowed,
        command=(
            "*** Begin Patch\n*** Update File: relative.py\n@@ -1 +1 @@\n-old\n+new\n*** End Patch"
        ),
    )

    emission = _run(
        "guards/write_guard.py",
        event,
        env={
            "AUTOSKILLIT_HEADLESS": "1",
            "AUTOSKILLIT_ALLOWED_WRITE_PREFIX": str(allowed) + "/",
            "AUTOSKILLIT_ALLOWED_WRITE_PREFIXES": "",
            "AUTOSKILLIT_CWD": "",
        },
        cwd=outside,
    )

    assert emission.exit_code == 0
    assert emission.stdout == ""


def test_installation_integrity_guard_blocks_codex_patch_to_installed_hook() -> None:
    package_root = pkg_root()
    event = _codex_payload(
        "PreToolUse",
        cwd=package_root,
        command=(
            "*** Begin Patch\n"
            "*** Update File: hooks/guards/installation_integrity_guard.py\n"
            "@@ -1 +1 @@\n-old\n+new\n"
            "*** End Patch"
        ),
    )
    emission = _run("guards/installation_integrity_guard.py", event)

    assert emission.exit_code == 0
    _assert_verdict("codex", event, emission, status="blocked")
