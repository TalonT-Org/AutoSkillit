"""MCP prefix literals must be confined to the single canonical definition module."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from autoskillit.core import paths

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_CONFINED_LITERALS = frozenset(
    {
        "mcp__autoskillit__",
        "mcp__plugin_autoskillit_autoskillit__",
    }
)

_CANONICAL_MODULE = "core/plugins/_plugin_ids.py"


def _string_literals_in_module(path: Path) -> list[tuple[int, str]]:
    """Return (lineno, value) for every string constant in *path*."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError:
        return []
    results: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            results.append((node.lineno, node.value))
        elif isinstance(node, ast.JoinedStr):
            for part in node.values:
                if isinstance(part, ast.Constant) and isinstance(part.value, str):
                    results.append((part.lineno, part.value))
    return results


def test_mcp_prefix_literals_confined_to_canonical_module() -> None:
    """The string literals for both MCP prefix forms appear only in _plugin_ids.py."""
    src_root = paths.pkg_root()
    violations: list[str] = []
    for py_file in sorted(src_root.rglob("*.py")):
        rel = py_file.relative_to(src_root).as_posix()
        if rel == _CANONICAL_MODULE:
            continue
        for lineno, value in _string_literals_in_module(py_file):
            for literal in _CONFINED_LITERALS:
                if literal in value:
                    violations.append(f"{rel}:{lineno}: contains {literal!r} in {value!r}")
    assert not violations, (
        "MCP prefix literals must appear only in "
        f"{_CANONICAL_MODULE}. Violations:\n" + "\n".join(violations)
    )


_QUALIFIED_AUTOSKILLIT_TOOL = re.compile(r"mcp__[A-Za-z0-9_-]*autoskillit[A-Za-z0-9_-]*__")


def _agent_body(text: str) -> str:
    """Return the text after the closing frontmatter delimiter."""
    lines = text.splitlines(keepends=True)
    delimiters = [index for index, line in enumerate(lines) if line.rstrip("\r\n") == "---"]
    if len(delimiters) < 2 or delimiters[0] != 0:
        return text
    return "".join(lines[delimiters[1] + 1 :])


def test_no_qualified_autoskillit_tool_names_in_model_visible_text() -> None:
    """Agent bodies, skills, and recipes name AutoSkillit tools by short name only.

    A qualified name is corridor-specific (Claude plugin, marketplace, Codex), so a
    qualified literal in shared model-visible prose is wrong in at least one corridor.
    Agent frontmatter stays DIRECT-canonical by design and is not scanned.
    """
    src_root = paths.pkg_root()
    documents: list[tuple[Path, str]] = []
    for agent_md in sorted((src_root / "agents").glob("*.md")):
        if agent_md.name in {"AGENTS.md", "CLAUDE.md"}:
            continue
        documents.append((agent_md, _agent_body(agent_md.read_text(encoding="utf-8"))))
    for skills_dir in ("skills", "skills_extended"):
        for skill_md in sorted((src_root / skills_dir).rglob("*.md")):
            documents.append((skill_md, skill_md.read_text(encoding="utf-8")))
    for recipe in sorted((src_root / "recipes").rglob("*.yaml")):
        documents.append((recipe, recipe.read_text(encoding="utf-8")))

    violations: list[tuple[str, str]] = [
        (path.relative_to(src_root).as_posix(), match)
        for path, text in documents
        for match in _QUALIFIED_AUTOSKILLIT_TOOL.findall(text)
    ]
    assert not violations, (
        "Qualified AutoSkillit MCP tool names in model-visible text; use the short name:\n"
        + "\n".join(f"  {path}: {match}" for path, match in violations)
    )
