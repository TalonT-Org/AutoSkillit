#!/usr/bin/env python3
"""Verify all MCP tool decorators use readOnlyHint: True.

AST-scans src/autoskillit/server/tools_*.py for @mcp.tool() decorators
and rejects any with readOnlyHint set to a non-True value.

Exit 0 if all annotations are correct. Exit 1 with details on violations.
"""

import ast
import sys
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent.parent / "src" / "autoskillit" / "server"


def check() -> list[str]:
    violations = []
    for path in sorted(SERVER_DIR.glob("tools_*.py")):
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
                for kw in dec.keywords:
                    if kw.arg != "annotations" or not isinstance(kw.value, ast.Dict):
                        continue
                    for key, val in zip(kw.value.keys, kw.value.values):
                        if (
                            isinstance(key, ast.Constant)
                            and key.value == "readOnlyHint"
                            and isinstance(val, ast.Constant)
                            and val.value is not True
                        ):
                            violations.append(
                                f"{path.name}:{dec.lineno}: {node.name} "
                                f"has readOnlyHint={val.value!r} (must be True)"
                            )
    return violations


def main() -> int:
    violations = check()
    if violations:
        print("readOnlyHint violations found:\n")
        for v in violations:
            print(f"  {v}")
        print(
            f"\nAll tools must have readOnlyHint=True. "
            f"See server/CLAUDE.md for rationale."
        )
        return 1
    print("All tool annotations correct: readOnlyHint=True.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
