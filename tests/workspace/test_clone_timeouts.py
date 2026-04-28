"""Static analysis: git network commands in clone.py must have timeouts."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.small]

_GIT_NETWORK_SUBCOMMANDS = {"push", "clone", "fetch", "pull", "ls-remote"}


def test_git_network_commands_have_timeout() -> None:
    """All subprocess.run() calls with git network commands must have timeout=."""
    src = (Path(__file__).parent.parent.parent / "src/autoskillit/workspace/clone.py").read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "run"
        ):
            continue
        # Check if the first arg is a list containing a git network command
        if not node.args:
            continue
        first_arg = node.args[0]
        if not isinstance(first_arg, ast.List):
            continue
        strs = [
            elt.value
            for elt in first_arg.elts
            if isinstance(elt, (ast.Constant,)) and isinstance(elt.value, str)
        ]
        if len(strs) < 2 or strs[0] != "git":
            continue
        if strs[1] not in _GIT_NETWORK_SUBCOMMANDS:
            continue
        kw_names = {kw.arg for kw in node.keywords}
        assert "timeout" in kw_names, (
            f"subprocess.run(['git', '{strs[1]}', ...]) at line {node.lineno} "
            f"in clone.py missing timeout="
        )
