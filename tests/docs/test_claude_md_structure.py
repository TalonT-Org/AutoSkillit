"""Validate CLAUDE.md post-reorganization content accuracy."""

from __future__ import annotations

import re
from pathlib import Path

CLAUDE_MD = Path(__file__).resolve().parents[2] / "CLAUDE.md"
_SERVER_DIR = CLAUDE_MD.parent / "src" / "autoskillit" / "server"


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


def test_claude_md_dataclass_count_is_27() -> None:
    content = CLAUDE_MD.read_text()
    assert "27 leaf dataclasses" in content
    assert "24 leaf dataclasses" not in content


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
    text = (_SERVER_DIR / "CLAUDE.md").read_text()
    assert "## Tool Gating Architecture" in text
    assert "### Tag-Visibility" in text
    assert "### Application-Gate" in text


def test_server_claude_md_gating_matrix_covers_categories() -> None:
    """server/CLAUDE.md gating matrix includes all five tool categories."""
    text = (_SERVER_DIR / "CLAUDE.md").read_text()
    for category in [
        "Standard kitchen",
        "Fleet tool",
        "Fleet-dispatch tool",
        "Headless-exempt",
        "Free-range",
    ]:
        assert category in text, f"Missing tool category {category!r} in gating matrix"


def test_test_check_has_require_enabled_exception_comment() -> None:
    """test_check has an inline comment explaining why it skips _require_enabled()."""
    ws_file = _SERVER_DIR / "tools" / "tools_workspace.py"
    src = ws_file.read_text()
    lines = src.splitlines()
    for i, line in enumerate(lines):
        if "async def test_check(" in line:
            region = lines[max(0, i - 10) : i]
            comment_lines = [ln for ln in region if ln.lstrip().startswith("#")]
            assert any("_require_enabled" in c for c in comment_lines), (
                "test_check decorator region should contain a comment about _require_enabled"
            )
            return
    raise AssertionError("async def test_check not found in tools_workspace.py")
