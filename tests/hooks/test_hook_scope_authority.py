"""Generated hook session-scope authority tests."""

from __future__ import annotations

import ast
import json
import re
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
from autoskillit.hooks._runtime import _hook_scope_table, _session_scope_authority
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
    reason = payload["hookSpecificOutput"]["permissionDecisionReason"]
    # Denial reason uses render_provenance_prefix + PolicyEvent; the
    # canonical reason_code surfaces as ``code=scope_authority_unavailable``.
    assert "code=scope_authority_unavailable" in reason


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

    hook_def = HookDef(matcher="Bash", session_scope=scope)  # type: ignore[arg-type]
    assert _session_scope_authority.enforce_script_session_scope(
        script
    ) is hook_applies_to_backend(
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


_SESSION_CLASS_KEYS = frozenset({"AUTOSKILLIT_HEADLESS", "AUTOSKILLIT_SESSION_TYPE"})
_NON_SCOPE_DYNAMIC_KEYS = frozenset(
    {
        "NATIVE_SHELL_CAPTURE_MODE_ENV_VAR",
        "MANAGED_LAUNCH_ID_ENV_VAR",
        "MANAGED_ATTEMPT_ID_ENV_VAR",
        "MANAGED_LINEAGE_DIGEST_ENV_VAR",
        "MANAGED_LINEAGE_REF_ENV_VAR",
        "DISPATCH_ID_ENV_VAR",
    }
)
_EXPECTED_SCOPE_READS = {
    ("_runtime/_hook_settings.py", "AUTOSKILLIT_HEADLESS"),
    ("_runtime/_hook_settings.py", "AUTOSKILLIT_SESSION_TYPE"),
}


def _env_read_argument(node: ast.AST) -> ast.AST | None:
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.args:
        owner = node.func.value
        if isinstance(owner, ast.Name) and owner.id == "os" and node.func.attr == "getenv":
            return node.args[0]
        if (
            isinstance(owner, ast.Attribute)
            and isinstance(owner.value, ast.Name)
            and owner.value.id == "os"
            and owner.attr == "environ"
            and node.func.attr in {"get", "pop"}
        ):
            return node.args[0]
    if isinstance(node, ast.Subscript):
        owner = node.value
        if (
            isinstance(owner, ast.Attribute)
            and isinstance(owner.value, ast.Name)
            and owner.value.id == "os"
            and owner.attr == "environ"
        ):
            return node.slice
    return None


def _scope_read_sites(source: str, filename: str) -> set[tuple[str, str]]:
    sites: set[tuple[str, str]] = set()
    for node in ast.walk(ast.parse(source, filename=filename)):
        argument = _env_read_argument(node)
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
            if argument.value in _SESSION_CLASS_KEYS:
                sites.add((filename, argument.value))
        elif isinstance(argument, ast.Name) and argument.id in _NON_SCOPE_DYNAMIC_KEYS:
            continue
        elif argument is not None:
            sites.add((filename, "<unresolved>"))
    return sites


def test_no_guard_reads_session_class_env_directly() -> None:
    violations = {
        site
        for path in HOOKS_DIR.rglob("*.py")
        if path != HOOKS_DIR / "_runtime" / "_hook_settings.py"
        for site in _scope_read_sites(
            path.read_text(encoding="utf-8"), str(path.relative_to(HOOKS_DIR))
        )
    }
    assert not violations, f"session-class reads must use _hook_settings: {sorted(violations)}"


def test_session_class_env_read_inventory_is_complete() -> None:
    helper = HOOKS_DIR / "_runtime" / "_hook_settings.py"
    actual = {
        site
        for site in _scope_read_sites(
            helper.read_text(encoding="utf-8"), "_runtime/_hook_settings.py"
        )
        if site[1] != "<unresolved>"
    }
    assert actual == _EXPECTED_SCOPE_READS


def test_scope_read_sites_marks_dynamic_env_key_as_unresolved() -> None:
    assert _scope_read_sites("os.environ.get(dynamic_key)", "guards/canary.py") == {
        ("guards/canary.py", "<unresolved>")
    }


def test_adr_scope_claims_match_registry() -> None:
    docs_root = Path(__file__).resolve().parents[2] / "docs" / "decisions"
    claim_owners = {
        "0001-prohibit-background-subagent-execution.md": "guards/background_exec_guard.py",
        "0006-output-containment.md": "capture_lifecycle_hook.py",
    }
    claims = {
        path.name: match.group(1)
        for path in docs_root.glob("*.md")
        for match in re.finditer(
            r'session_scope="(any|headless_only|interactive_only)"',
            path.read_text(encoding="utf-8"),
        )
    }
    assert set(claims) == set(claim_owners)
    scopes = {script: hook.session_scope for hook in HOOK_REGISTRY for script in hook.scripts}
    assert all(claims[doc] == scopes[script] for doc, script in claim_owners.items())
