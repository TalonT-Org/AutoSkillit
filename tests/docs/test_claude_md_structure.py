"""Validate CLAUDE.md post-reorganization content accuracy."""

from __future__ import annotations

import re
from pathlib import Path

CLAUDE_MD = Path(__file__).resolve().parents[2] / "CLAUDE.md"


def test_claude_md_architecture_tree_has_subpackages() -> None:
    content = CLAUDE_MD.read_text()
    for subpkg in [
        "types/",
        "runtime/",
        "headless/",
        "process/",
        "merge_queue/",
        "rules/",
        "tools/",
        "doctor/",
        "fleet/",
        "session/",
        "ui/",
        "update/",
        "guards/",
        "formatters/",
    ]:
        assert subpkg in content, f"CLAUDE.md tree missing {subpkg}"


def test_claude_md_dataclass_count_is_24() -> None:
    content = CLAUDE_MD.read_text()
    assert "24 leaf dataclasses" in content
    assert "22 leaf dataclasses" not in content


def test_claude_md_has_lsp_section() -> None:
    content = CLAUDE_MD.read_text()
    assert "Pyright LSP" in content or "LSP" in content
    assert "goToDefinition" in content
    assert "findReferences" in content


def test_claude_md_no_precommit_install_line() -> None:
    content = CLAUDE_MD.read_text()
    assert "Install hooks after cloning: `pre-commit install`" not in content


def test_claude_md_def_spec_location_qualified() -> None:
    content = CLAUDE_MD.read_text()
    lines = content.splitlines()
    for line in lines:
        if "*Def*" in line or "`*Def`" in line:
            if "Lives in `core/`" in line and "Typically" not in line:
                raise AssertionError(
                    "CLAUDE.md *Def rule has unqualified 'Lives in core/' — "
                    "should say 'Typically lives in core/'"
                )


def test_claude_md_mentions_write_guard() -> None:
    # write_guard is documented in hooks/guards/CLAUDE.md — accept either location
    main_content = CLAUDE_MD.read_text()
    guards_claude = CLAUDE_MD.parent / "src" / "autoskillit" / "hooks" / "guards" / "CLAUDE.md"
    assert "write_guard" in main_content or (
        guards_claude.exists() and "write_guard" in guards_claude.read_text()
    )


def test_claude_md_mentions_dispatch_food_truck() -> None:
    # dispatch_food_truck is documented in server/tools/CLAUDE.md — accept either location
    main_content = CLAUDE_MD.read_text()
    tools_claude = CLAUDE_MD.parent / "src" / "autoskillit" / "server" / "tools" / "CLAUDE.md"
    assert "dispatch_food_truck" in main_content or (
        tools_claude.exists() and "dispatch_food_truck" in tools_claude.read_text()
    )


def test_claude_md_defines_channel_b() -> None:
    content = CLAUDE_MD.read_text()
    assert "Channel B" in content
    assert re.search(r"Channel B[^.]*JSONL", content), (
        "CLAUDE.md references 'Channel B' without an inline definition mentioning JSONL"
    )


def test_server_claude_md_has_tool_gating_section() -> None:
    """server/CLAUDE.md documents the two-layer tool gating architecture."""
    server_claude = CLAUDE_MD.parent / "src" / "autoskillit" / "server" / "CLAUDE.md"
    text = server_claude.read_text()
    assert "## Tool Gating Architecture" in text
    assert "### Tag-Visibility" in text
    assert "### Application-Gate" in text


def test_server_claude_md_gating_matrix_covers_categories() -> None:
    """server/CLAUDE.md gating matrix includes all five tool categories."""
    server_claude = CLAUDE_MD.parent / "src" / "autoskillit" / "server" / "CLAUDE.md"
    text = server_claude.read_text()
    for category in [
        "Standard kitchen",
        "Fleet tool",
        "Fleet-dispatch tool",
        "Headless-exempt",
        "Free-range",
    ]:
        assert category in text, f"Missing tool category {category!r} in gating matrix"


def test_server_init_docstring_no_stale_tool_count() -> None:
    """server/__init__.py docstring must not contain hardcoded tool counts."""
    init_file = CLAUDE_MD.parent / "src" / "autoskillit" / "server" / "__init__.py"
    # Read only the module docstring (first triple-quoted block)
    src = init_file.read_text()
    docstring_match = re.match(r'^"""(.*?)"""', src, re.DOTALL)
    assert docstring_match, "server/__init__.py should start with a module docstring"
    docstring = docstring_match.group(1)
    assert not re.search(r"\d+ kitchen-tagged", docstring), (
        "Stale tool count in server/__init__.py docstring — "
        "remove hardcoded counts, they rot as tools are added"
    )


def test_test_check_has_require_enabled_exception_comment() -> None:
    """test_check has an inline comment explaining why it skips _require_enabled()."""
    ws_file = CLAUDE_MD.parent / "src" / "autoskillit" / "server" / "tools" / "tools_workspace.py"
    src = ws_file.read_text()
    lines = src.splitlines()
    # Find the line defining test_check and scan backwards to its decorator block
    for i, line in enumerate(lines):
        if "async def test_check(" in line:
            decorator_region = "\n".join(lines[max(0, i - 10) : i])
            assert "_require_enabled" in decorator_region, (
                "test_check decorator region should contain a comment about _require_enabled"
            )
            return
    raise AssertionError("async def test_check not found in tools_workspace.py")
