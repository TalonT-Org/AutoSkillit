"""Architecture guards for descriptor-anchored shell capture."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC = _REPO_ROOT / "src" / "autoskillit"
_REDIRECTION_RE = re.compile(
    r"(?:^|[\s;|&])(?:\d*>>?|&>>?)\s*(?P<target>\"[^\"]+\"|'[^']+'|[^\s;|&]+)"
)
_CAPTURE_PATH_TERM_RE = re.compile(
    r"capture(?:_root|_path|_file)?|artifact|shell_(?:capture|output)|__as_f",
    re.IGNORECASE,
)

_PROJECT_TEMP_CLEANUP_DEBT = {
    "core/io.py": {
        "owner": "core IO",
        "reason": "atomic temp writes still use mkdir/mkstemp/os.replace by pathname",
        "tracking_issue": "#4319",
    },
    "workspace/worktree.py": {
        "owner": "workspace worktree lifecycle",
        "reason": "worktree sidecars are still removed with pathname-based rmtree",
        "tracking_issue": "#4319",
    },
    "workspace/clone_registry.py": {
        "owner": "workspace clone registry",
        "reason": "registry-provided clone paths still reach removal callbacks",
        "tracking_issue": "#4319",
    },
    "workspace/clone.py": {
        "owner": "workspace clone lifecycle",
        "reason": "clone cleanup still uses pathname-based unlink/rmtree",
        "tracking_issue": "#4319",
    },
    "scripts/recipe/create_worktree.sh": {
        "owner": "workspace worktree lifecycle",
        "reason": "recipe-side worktree sidecars are still written and removed by pathname",
        "tracking_issue": "#4319",
    },
}


def _source(relative: str) -> str:
    return (_SRC / relative).read_text(encoding="utf-8")


def _render_string_node(node: ast.Constant | ast.JoinedStr) -> str:
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else ""
    parts: list[str] = []
    for value in node.values:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            parts.append(value.value)
        elif isinstance(value, ast.FormattedValue):
            parts.append(f"{{{ast.unparse(value.value)}}}")
    return "".join(parts)


def _called_operation_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and isinstance(node.args[1].value, str)
    ):
        return node.args[1].value
    return None


def _pathname_operation_calls(source: str) -> list[str]:
    tree = ast.parse(source)
    imported_aliases = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module in {"os", "pathlib", "shutil"}
        for alias in node.names
        if alias.name in {"unlink", "rmtree"}
    }
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        operation = _called_operation_name(node.func)
        if operation in {"unlink", "rmtree"} or operation in imported_aliases:
            violations.append(ast.unparse(node.func))
    return violations


def _shell_mkdir_calls(source: str) -> list[str]:
    return [
        node.value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and re.search(r"(?:^|[\s;&|])mkdir\s+-p(?:\s|$)", node.value)
    ]


def _imports_symbol(tree: ast.Module, module: str, symbol: str) -> bool:
    return any(
        isinstance(node, ast.ImportFrom)
        and node.module == module
        and any(alias.name == symbol for alias in node.names)
        for node in tree.body
    )


def _capture_path_redirections(source: str) -> list[str]:
    tree = ast.parse(source)
    capture_path_names = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and _CAPTURE_PATH_TERM_RE.search(node.id)
    }
    assignments: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        value = ast.unparse(node.value) if node.value is not None else ""
        for target in targets:
            if isinstance(target, ast.Name):
                assignments[target.id] = value
    changed = True
    while changed:
        changed = False
        for name, value in assignments.items():
            if name in capture_path_names:
                continue
            if _CAPTURE_PATH_TERM_RE.search(value) or any(
                re.search(rf"\b{re.escape(known)}\b", value) for known in capture_path_names
            ):
                capture_path_names.add(name)
                changed = True

    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Constant, ast.JoinedStr)):
            continue
        rendered = _render_string_node(node)
        for match in _REDIRECTION_RE.finditer(rendered):
            target = match.group("target")
            if _CAPTURE_PATH_TERM_RE.search(target) or any(
                re.search(rf"\b{re.escape(name)}\b", target) for name in capture_path_names
            ):
                violations.append(match.group(0).strip())
    return violations


def test_resume_reminder_does_not_own_capture_lifecycle() -> None:
    source = _source("hooks/session_start_hook.py")
    tree = ast.parse(source)
    assert not _imports_symbol(tree, "_capture_artifacts", "classify_stale_captures")
    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            imported_modules.add(node.module)
        elif isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
    assert not any("capture" in module for module in imported_modules)


def test_shell_capture_code_has_no_pathname_harness_or_cleanup() -> None:
    sources = {
        relative: _source(relative)
        for relative in (
            "hooks/shell_capture_hook.py",
            "hooks/_capture_artifacts.py",
        )
    }
    violations = {}
    for relative, source in sources.items():
        detected = _pathname_operation_calls(source) + _shell_mkdir_calls(source)
        if detected:
            violations[relative] = detected
    assert not violations, f"pathname capture operation reintroduced: {violations}"
    redirection_violations = {}
    for relative, source in sources.items():
        detected = _capture_path_redirections(source)
        if detected:
            redirection_violations[relative] = detected
    assert not redirection_violations, (
        f"capture-root pathname redirection reintroduced: {redirection_violations}"
    )


def test_capture_deletion_is_confined_to_lifecycle_transactions() -> None:
    trees = {
        relative: ast.parse(_source(relative))
        for relative in (
            "hooks/_capture_lifecycle.py",
            "hooks/_capture/_sweep.py",
        )
    }
    functions_with_unlink = {
        relative: {
            function.name
            for function in ast.walk(tree)
            if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
            and any(
                isinstance(node, ast.Call) and _called_operation_name(node.func) == "unlink"
                for node in ast.walk(function)
            )
        }
        for relative, tree in trees.items()
    }
    assert functions_with_unlink == {
        "hooks/_capture_lifecycle.py": {
            "_compact_locked",
            "create_artifact",
        },
        "hooks/_capture/_sweep.py": {
            "normalize_abandoned",
            "quarantine_delete",
            "unlink_quarantine",
        },
    }

    quarantine = next(
        node
        for node in ast.walk(trees["hooks/_capture/_sweep.py"])
        if isinstance(node, ast.FunctionDef) and node.name == "quarantine_delete"
    )
    assert (
        sum(
            isinstance(node, ast.Call) and _called_operation_name(node.func) == "unlink"
            for node in ast.walk(quarantine)
        )
        == 2
    )


@pytest.mark.parametrize(
    "source",
    [
        "import os as filesystem\nfilesystem.unlink('artifact')",
        "from os import unlink as remove\nremove('artifact')",
        "from pathlib import Path\ngetattr(Path('artifact'), 'unlink')()",
        "import shutil as cleanup\ncleanup.rmtree('capture-root')",
    ],
)
def test_pathname_operation_guard_rejects_aliases_and_getattr(source: str) -> None:
    assert _pathname_operation_calls(source)


@pytest.mark.parametrize(
    "harness",
    [
        'cmd > "$capture_path"',
        'cmd 2>>"${artifact_file}"',
        "cmd > /tmp/.autoskillit/temp/shell_capture/shell_0123456789abcdef.log",
        'cmd &> "$shell_output"',
    ],
)
def test_capture_path_redirection_guard_rejects_broad_targets(harness: str) -> None:
    assert _capture_path_redirections(f"harness = {harness!r}")


def test_capture_path_redirection_guard_tracks_derived_path_names() -> None:
    source = (
        "capture_root = CAPTURE_PATH_COMPONENTS\n"
        "output_path = capture_root\n"
        'harness = f"cmd > {output_path}/shell.log"\n'
    )
    assert _capture_path_redirections(source)
    assert not _capture_path_redirections('harness = "cmd > ordinary.log"')


def test_project_temp_cleanup_debt_entries_exist_and_are_owned() -> None:
    for relative, debt in _PROJECT_TEMP_CLEANUP_DEBT.items():
        path = _REPO_ROOT / relative if relative.startswith("scripts/") else _SRC / relative
        assert path.is_file()
        assert debt["owner"]
        assert debt["reason"]
        assert debt["tracking_issue"] == "#4319"
