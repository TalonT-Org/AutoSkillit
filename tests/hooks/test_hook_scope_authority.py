"""Generated hook session-scope authority tests."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import autoskillit.hooks  # noqa: F401  (populates the lazy hook registry)
from autoskillit.hook_registry import (
    HOOK_REGISTRY,
    HOOKS_DIR,
    HookDef,
    hook_applies_to_backend,
    render_hook_scope_table,
)
from autoskillit.hooks._runtime import _hook_scope_table, _hook_settings
from autoskillit.workspace._projected_artifact._publication import write_generated_hooks_json
from tests.conftest import production_interpreter_env

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.medium]


def _registry_scopes() -> dict[str, str]:
    return {
        script: hook_def.session_scope for hook_def in HOOK_REGISTRY for script in hook_def.scripts
    }


def _run_copied_guard(
    tmp_path: Path,
    script: str,
    *,
    table_content: str | None,
) -> subprocess.CompletedProcess[str]:
    copied_hooks = tmp_path / "hooks"
    shutil.copytree(HOOKS_DIR, copied_hooks)
    table_path = copied_hooks / "_runtime" / "_hook_scope_table.py"
    if table_content is None:
        table_path.unlink()
    else:
        table_path.write_text(table_content, encoding="utf-8")
    return subprocess.run(
        [sys.executable, "-B", str(copied_hooks / script)],
        input=json.dumps({"tool_name": "AskUserQuestion", "tool_input": {}}),
        capture_output=True,
        text=True,
        env=production_interpreter_env(),
        timeout=10,
    )


def test_committed_scope_table_matches_registry_renderer() -> None:
    assert _hook_scope_table.HOOK_SCOPE_BY_SCRIPT == _registry_scopes()
    assert (HOOKS_DIR / "_runtime" / "_hook_scope_table.py").read_text(
        encoding="utf-8"
    ) == render_hook_scope_table()


@pytest.mark.parametrize("table_content", [None, "HOOK_SCOPE_BY_SCRIPT = {}\n"])
@pytest.mark.parametrize(
    "script",
    ["guards/ask_user_question_guard.py", "guards/mcp_health_advisor.py"],
)
def test_missing_scope_table_fails_closed(
    tmp_path: Path,
    script: str,
    table_content: str | None,
) -> None:
    result = _run_copied_guard(tmp_path, script, table_content=table_content)

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert (
        "scope authority is unavailable"
        in payload["hookSpecificOutput"]["permissionDecisionReason"]
    )


@pytest.mark.parametrize("scope", ["any", "headless_only", "interactive_only"])
@pytest.mark.parametrize("headless", [False, True])
def test_runtime_scope_mapping_matches_registry(
    monkeypatch: pytest.MonkeyPatch,
    scope: str,
    headless: bool,
) -> None:
    script = "guards/test_scope_authority.py"
    monkeypatch.setattr(_hook_scope_table, "HOOK_SCOPE_BY_SCRIPT", {script: scope})
    if headless:
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
    else:
        monkeypatch.delenv("AUTOSKILLIT_HEADLESS", raising=False)

    hook_def = HookDef(matcher="Bash", session_scope=scope)  # type: ignore[arg-type]
    assert _hook_settings.enforce_session_scope(script) is hook_applies_to_backend(
        hook_def,
        backend="claude_code",
        session_scope="headless" if headless else "interactive",
    )


def test_hook_def_rejects_unknown_session_scope() -> None:
    with pytest.raises(ValueError, match="HookDef.session_scope"):
        HookDef(matcher="Bash", session_scope="unrecognized")  # type: ignore[arg-type]


def test_projection_publication_writes_generated_scope_table(tmp_path: Path) -> None:
    plugin_root = tmp_path / "plugin"
    (plugin_root / "hooks").mkdir(parents=True)

    write_generated_hooks_json(plugin_root)

    assert (plugin_root / "hooks" / "_runtime" / "_hook_scope_table.py").read_text(
        encoding="utf-8"
    ) == render_hook_scope_table()
