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
