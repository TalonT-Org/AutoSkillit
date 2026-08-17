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
# REQ-ARCH-ANNOTATION-E1: open_kitchen and declare_join_batch are the only
# effectful tools; the former opens the kitchen session and the latter opens
# a join batch ledger entry. Every other tool must remain readOnlyHint=True.
READ_ONLY_EXCEPTIONS = {"open_kitchen": False, "declare_join_batch": False}


def check() -> list[str]:
    violations = []
    paths = sorted(TOOLS_DIR.glob("tools_*.py"))
    if not paths:
        return [f"{TOOLS_DIR}: no tool modules discovered"]
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if not (
                    isinstance(dec, ast.Call)
                    and isinstance(dec.func, ast.Attribute)
                    and dec.func.attr == "tool"
                    and isinstance(dec.func.value, ast.Name)
                    and dec.func.value.id == "mcp"
                ):
                    continue
                ann_kw = None
                for kw in dec.keywords:
                    if kw.arg == "annotations" and isinstance(kw.value, ast.Dict):
                        ann_kw = kw
                        break
                if ann_kw is None:
                    violations.append(
                        f"{path.name}:{dec.lineno}: {node.name} missing annotations= keyword"
                    )
                    continue
                key_names = [k.value for k in ann_kw.value.keys if isinstance(k, ast.Constant)]
                if "readOnlyHint" not in key_names:
                    violations.append(
                        f"{path.name}:{dec.lineno}: {node.name} "
                        "annotations dict missing readOnlyHint key"
                    )
                    continue
                expected = READ_ONLY_EXCEPTIONS.get(node.name, True)
                for key, val in zip(ann_kw.value.keys, ann_kw.value.values, strict=True):
                    if isinstance(key, ast.Constant) and key.value == "readOnlyHint":
                        actual = val.value if isinstance(val, ast.Constant) else None
                        if actual is expected:
                            continue
                        violations.append(
                            f"{path.name}:{dec.lineno}: {node.name} "
                            f"has readOnlyHint={actual!r} (must be {expected!r})"
                        )
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
