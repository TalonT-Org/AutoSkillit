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


def test_session_start_calls_canonical_capture_classification() -> None:
    source = _source("hooks/session_start_hook.py")
    assert "from _capture_artifacts import" in source
    assert "classify_stale_captures(Path.cwd())" in source
    assert "shell_capture" not in source


def test_shell_capture_code_has_no_pathname_harness_or_cleanup() -> None:
    sources = {
        relative: _source(relative)
        for relative in (
            "hooks/shell_capture_hook.py",
            "hooks/_capture_artifacts.py",
        )
    }
    combined = "\n".join(sources.values())
    for forbidden in (
        "mkdir -p",
        ".unlink(",
        "os.unlink(",
        "shutil.rmtree(",
    ):
        assert forbidden not in combined, f"pathname capture operation reintroduced: {forbidden}"
    violations = {}
    for relative, source in sources.items():
        detected = _capture_path_redirections(source)
        if detected:
            violations[relative] = detected
    assert not violations, f"capture-root pathname redirection reintroduced: {violations}"


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


def test_project_temp_cleanup_debt_is_narrow_and_owned() -> None:
    assert set(_PROJECT_TEMP_CLEANUP_DEBT) == {
        "core/io.py",
        "workspace/worktree.py",
        "workspace/clone_registry.py",
        "workspace/clone.py",
        "scripts/recipe/create_worktree.sh",
    }
    for relative, debt in _PROJECT_TEMP_CLEANUP_DEBT.items():
        path = _REPO_ROOT / relative if relative.startswith("scripts/") else _SRC / relative
        assert path.is_file()
        assert debt["owner"]
        assert debt["reason"]
        assert debt["tracking_issue"] == "#4319"
