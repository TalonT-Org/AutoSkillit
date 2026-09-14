"""Registration-derived atomicity for rules, hooks, guards, and MCP tools."""

from __future__ import annotations

import ast
import re
import shlex
from collections import Counter
from pathlib import Path

import pytest

from autoskillit.hooks import generate_hooks_json
from tests.arch._helpers import SRC_ROOT

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


def _decorator_count(source: str, *, mcp: bool) -> int:
    count = 0
    for node in ast.parse(source).body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            target = decorator.func if isinstance(decorator, ast.Call) else decorator
            if mcp:
                registered = (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "mcp"
                    and target.attr in {"tool", "resource"}
                )
            else:
                registered = (
                    isinstance(target, ast.Name) and target.id in {"semantic_rule", "block_rule"}
                ) or (
                    isinstance(target, ast.Attribute)
                    and target.attr in {"semantic_rule", "block_rule"}
                )
            count += registered
    return count


def _hook_registration_counts(hooks_root: Path, generated: dict) -> Counter[Path]:
    dispatcher = ast.parse((hooks_root / "_dispatch.py").read_text())
    retired = next(
        ast.literal_eval(node.value)
        for node in dispatcher.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "_RETIRED_MAPPING"
    )
    counts: Counter[Path] = Counter()
    unresolved: list[str] = []
    for entries in generated["hooks"].values():
        for entry in entries:
            for hook in entry["hooks"]:
                logical_name = shlex.split(hook["command"])[-1]
                target = hooks_root / f"{logical_name}.py"
                if not target.is_file():
                    mapped = retired.get(logical_name)
                    if mapped is not None:
                        target = hooks_root / f"{mapped}.py"
                if target.is_file():
                    counts[target] += 1
                else:
                    unresolved.append(logical_name)
    assert not unresolved, f"Unmapped generated hook commands: {sorted(unresolved)}"
    return counts


def _atomicity_errors(folder: Path, counts: Counter[Path]) -> list[str]:
    files = {path.name: path for path in folder.glob("*.py")}
    guide = folder / "AGENTS.md"
    if not guide.is_file():
        return [f"{folder}: missing AGENTS.md"]
    lines = guide.read_text().splitlines()
    headings = [index for index, line in enumerate(lines) if line == "## Multi-concern files"]
    if len(headings) != 1:
        return [f"{folder}: expected exactly one ## Multi-concern files heading"]
    section = lines[headings[0] + 1 :]
    section = section[
        : next((i for i, line in enumerate(section) if line.startswith("## ")), len(section))
    ]
    names: list[str] = []
    errors: list[str] = []
    for line in section:
        if not line.startswith("- "):
            continue
        match = re.fullmatch(r"- `([^`]+\.py)` — (\S.*)", line)
        if match is None:
            errors.append(f"{folder}: malformed or empty exception: {line}")
        else:
            names.append(match.group(1))
    documented = Counter(names)
    non_atomic = {name for name, path in files.items() if counts[path] != 1}
    missing = non_atomic - documented.keys()
    stale = documented.keys() - non_atomic
    duplicate = {name for name, count in documented.items() if count != 1}
    if missing or stale or duplicate:
        errors.append(
            f"{folder}: missing={sorted(missing)}, duplicate={sorted(duplicate)}, "
            f"stale={sorted(stale)}"
        )
    return errors


def _assert_folder_atomicity(folder: Path, counts: Counter[Path]) -> None:
    assert not (errors := _atomicity_errors(folder, counts)), "\n".join(errors)


def test_rule_modules_are_atomic_or_documented() -> None:
    folder = SRC_ROOT / "recipe" / "rules"
    counts = Counter(
        {path: _decorator_count(path.read_text(), mcp=False) for path in folder.glob("*.py")}
    )
    _assert_folder_atomicity(folder, counts)


def test_guard_files_are_atomic_or_documented() -> None:
    folder = SRC_ROOT / "hooks" / "guards"
    _assert_folder_atomicity(
        folder, _hook_registration_counts(SRC_ROOT / "hooks", generate_hooks_json())
    )


def test_hook_files_are_atomic_or_documented() -> None:
    folder = SRC_ROOT / "hooks"
    _assert_folder_atomicity(folder, _hook_registration_counts(folder, generate_hooks_json()))


def test_tool_modules_are_atomic_or_documented() -> None:
    folder = SRC_ROOT / "server" / "tools"
    counts = Counter(
        {path: _decorator_count(path.read_text(), mcp=True) for path in folder.glob("*.py")}
    )
    _assert_folder_atomicity(folder, counts)


def test_registration_counters_ignore_text_and_count_both_families(tmp_path: Path) -> None:
    rules = """
# @semantic_rule()
LABEL = "@block_rule()"
@semantic_rule(id="one")
def one(): pass
@block_rule(id="two")
async def two(): pass
"""
    tools = """
# @mcp.tool()
@mcp.resource("thing://one")
async def resource(): pass
@mcp.tool()
def tool(): pass
"""
    assert _decorator_count(rules, mcp=False) == 2
    assert _decorator_count(tools, mcp=True) == 2
    hooks = tmp_path / "hooks"
    (hooks / "guards").mkdir(parents=True)
    (hooks / "_dispatch.py").write_text("_RETIRED_MAPPING: dict[str, str] = {}\n")
    (hooks / "guards" / "guard.py").touch()
    generated = {
        "hooks": {"PreToolUse": [{"hooks": [{"command": "python _dispatch.py guards/guard"}]}]}
    }
    assert _hook_registration_counts(hooks, generated)[hooks / "guards" / "guard.py"] == 1


def test_non_atomic_documentation_rejects_missing_and_stale_entries(tmp_path: Path) -> None:
    for name in ("zero.py", "one.py", "many.py"):
        (tmp_path / name).touch()
    counts = Counter({tmp_path / "one.py": 1, tmp_path / "many.py": 2})
    guide = tmp_path / "AGENTS.md"
    guide.write_text(
        "## Multi-concern files\n\n"
        "- `zero.py` — Support file with no registration.\n"
        "- `many.py` — Groups two registrations.\n"
    )
    assert _atomicity_errors(tmp_path, counts) == []
    guide.write_text("## Multi-concern files\n\n- `one.py` — Stale exception.\n")
    assert "missing=['many.py', 'zero.py']" in _atomicity_errors(tmp_path, counts)[0]
    assert "stale=['one.py']" in _atomicity_errors(tmp_path, counts)[0]
