"""Validate CLAUDE.md post-reorganization: @-import structure, Claude-specific content accuracy."""

from __future__ import annotations

import re
from pathlib import Path

CLAUDE_MD = Path(__file__).resolve().parents[2] / "CLAUDE.md"
AGENTS_MD = Path(__file__).resolve().parents[2] / "AGENTS.md"
_SERVER_DIR = CLAUDE_MD.parent / "src" / "autoskillit" / "server"


class TestClaudeMdImportStructure:
    """Tests for CLAUDE.md @-import structure."""

    def test_claude_md_starts_with_agents_import(self) -> None:
        content = CLAUDE_MD.read_text()
        lines = [line for line in content.splitlines() if line.strip()]
        assert lines[0] == "@AGENTS.md", (
            f"First non-blank line of CLAUDE.md must be '@AGENTS.md', got {lines[0]!r}"
        )

    def test_claude_md_under_100_lines(self) -> None:
        content = CLAUDE_MD.read_text()
        line_count = len(content.splitlines())
        assert line_count < 100, f"CLAUDE.md must have under 100 lines, got {line_count}"

    def test_claude_md_no_shared_content_duplicated(self) -> None:
        content = CLAUDE_MD.read_text()
        shared_markers = [
            "Core Project Goal",
            "General Principles",
            "GitHub API Call Discipline",
            "GitHub Issue Body",
            "| Package | IL | Purpose |",
            "| IL-N (single digit)",
            "Session Diagnostics",
        ]
        for marker in shared_markers:
            assert marker not in content, (
                f"CLAUDE.md must not contain shared content marker {marker!r} — "
                f"it lives in AGENTS.md and is imported via @AGENTS.md"
            )


def test_agents_md_architecture_tree_has_subpackages() -> None:
    assert AGENTS_MD.exists(), f"AGENTS.md not found at {AGENTS_MD}"
    content = AGENTS_MD.read_text()
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
        assert subpkg in content, f"AGENTS.md tree missing {subpkg}"


def test_agents_md_dataclass_count_is_27() -> None:
    assert AGENTS_MD.exists(), f"AGENTS.md not found at {AGENTS_MD}"
    content = AGENTS_MD.read_text()
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
    main_content = CLAUDE_MD.read_text()
    guards_claude = CLAUDE_MD.parent / "src" / "autoskillit" / "hooks" / "guards" / "CLAUDE.md"
    assert "write_guard" in main_content or (
        guards_claude.exists() and "write_guard" in guards_claude.read_text()
    )


def test_claude_md_mentions_dispatch_food_truck() -> None:
    main_content = CLAUDE_MD.read_text()
    tools_claude = CLAUDE_MD.parent / "src" / "autoskillit" / "server" / "tools" / "CLAUDE.md"
    assert "dispatch_food_truck" in main_content or (
        tools_claude.exists() and "dispatch_food_truck" in tools_claude.read_text()
    )


def test_agents_md_defines_channel_b() -> None:
    assert AGENTS_MD.exists(), f"AGENTS.md not found at {AGENTS_MD}"
    content = AGENTS_MD.read_text()
    assert "Channel B" in content
    assert re.search(r"Channel B[^.]*JSONL", content), (
        "AGENTS.md references 'Channel B' without an inline definition mentioning JSONL"
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
