#!/usr/bin/env python3
"""Verify MCP tool decorators use their exact readOnlyHint contract.

AST-scans src/autoskillit/server/tools/tools_*.py for @mcp.tool() decorators.
The effectful ``open_kitchen`` transition is False; every other tool is True.

Exit 0 if all annotations are correct. Exit 1 with details on violations.
"""

import ast
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent.parent / "src" / "autoskillit" / "server" / "tools"
# REQ-ARCH-ANNOTATION-E1: these tools own process-local kitchen or durable
# batch transitions. Every other tool must remain readOnlyHint=True.
READ_ONLY_EXCEPTIONS = {
    "open_kitchen": False,
    "declare_join_batch": False,
    "run_fixed_batch": False,
}


def _collect_tool_paths() -> list[Path]:
    paths = list(TOOLS_DIR.glob("tools_*.py"))
    for pkg_dir in TOOLS_DIR.iterdir():
        if not pkg_dir.is_dir():
            continue
        if not pkg_dir.name.startswith("tools_"):
            continue
        for submodule in pkg_dir.glob("*.py"):
            if submodule.name == "__init__.py":
                continue
            paths.append(submodule)
    return sorted(paths)


def _decorator_violations(
    path: Path,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    decorator: ast.expr,
) -> list[str]:
    if not (
        isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Attribute)
        and decorator.func.attr == "tool"
        and isinstance(decorator.func.value, ast.Name)
        and decorator.func.value.id == "mcp"
    ):
        return []

    ann_dict: ast.Dict | None = None
    for keyword in decorator.keywords:
        if keyword.arg == "annotations" and isinstance(keyword.value, ast.Dict):
            ann_dict = keyword.value
            break
    if ann_dict is None:
        return [f"{path.name}:{decorator.lineno}: {node.name} missing annotations= keyword"]

    key_names = [key.value for key in ann_dict.keys if isinstance(key, ast.Constant)]
    if "readOnlyHint" not in key_names:
        return [
            f"{path.name}:{decorator.lineno}: {node.name} "
            "annotations dict missing readOnlyHint key"
        ]

    expected = READ_ONLY_EXCEPTIONS.get(node.name, True)
    violations: list[str] = []
    for key, value in zip(ann_dict.keys, ann_dict.values, strict=True):
        if isinstance(key, ast.Constant) and key.value == "readOnlyHint":
            actual = value.value if isinstance(value, ast.Constant) else None
            if actual is expected:
                continue
            violations.append(
                f"{path.name}:{decorator.lineno}: {node.name} "
                f"has readOnlyHint={actual!r} (must be {expected!r})"
            )
    return violations


def check() -> list[str]:
    violations = []
    paths = _collect_tool_paths()
    if not paths:
        return [f"{TOOLS_DIR}: no tool modules discovered"]
    for path in paths:
        try:
            source = path.read_text(encoding="utf-8")
        except (FileNotFoundError, NotADirectoryError):
            continue
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                violations.extend(_decorator_violations(path, node, dec))
    return violations


def main() -> int:
    violations = check()
    if violations:
        print("readOnlyHint violations found:\n")
        for v in violations:
            print(f"  {v}")
        print("\nSee server/AGENTS.md for the readOnlyHint contract.")
        return 1
    print("All tool annotations match the readOnlyHint contract.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
