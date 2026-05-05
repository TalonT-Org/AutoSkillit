"""Structural tests for per-subfolder CLAUDE.md documentation files."""

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.small

SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "autoskillit"

EXPECTED_SUB_CLAUDE_MDS = [
    # existing 15 (unchanged order)
    "core/types/CLAUDE.md",
    "core/runtime/CLAUDE.md",
    "execution/headless/CLAUDE.md",
    "execution/process/CLAUDE.md",
    "execution/session/CLAUDE.md",
    "execution/merge_queue/CLAUDE.md",
    "recipe/rules/CLAUDE.md",
    "server/tools/CLAUDE.md",
    "cli/doctor/CLAUDE.md",
    "cli/fleet/CLAUDE.md",
    "cli/session/CLAUDE.md",
    "cli/ui/CLAUDE.md",
    "cli/update/CLAUDE.md",
    "hooks/guards/CLAUDE.md",
    "hooks/formatters/CLAUDE.md",
    # 12 new entries
    "CLAUDE.md",  # src/autoskillit/ package root
    "core/CLAUDE.md",
    "config/CLAUDE.md",
    "pipeline/CLAUDE.md",
    "execution/CLAUDE.md",
    "workspace/CLAUDE.md",
    "planner/CLAUDE.md",
    "recipe/CLAUDE.md",
    "migration/CLAUDE.md",
    "fleet/CLAUDE.md",
    "cli/CLAUDE.md",
    "hooks/CLAUDE.md",
]


def test_all_27_sub_claude_md_files_exist():
    missing = [p for p in EXPECTED_SUB_CLAUDE_MDS if not (SRC_ROOT / p).is_file()]
    assert not missing, f"Missing sub-CLAUDE.md files: {missing}"


def test_sub_claude_md_covers_all_py_files():
    failures = []
    for rel_path in EXPECTED_SUB_CLAUDE_MDS:
        claude_md = SRC_ROOT / rel_path
        if not claude_md.is_file():
            failures.append(f"{rel_path}: file does not exist")
            continue
        content = claude_md.read_text()
        directory = claude_md.parent
        py_files = sorted(f.name for f in directory.glob("*.py"))
        for py_file in py_files:
            if py_file == "__init__.py":
                if "`__init__.py`" not in content:
                    failures.append(f"{rel_path}: missing `__init__.py` in file table")
            else:
                if py_file not in content:
                    failures.append(f"{rel_path}: missing {py_file}")
    assert not failures, "Sub-CLAUDE.md coverage gaps:\n" + "\n".join(failures)


def test_sub_claude_md_has_file_table():
    failures = []
    for rel_path in EXPECTED_SUB_CLAUDE_MDS:
        claude_md = SRC_ROOT / rel_path
        if not claude_md.is_file():
            failures.append(f"{rel_path}: file does not exist")
            continue
        content = claude_md.read_text()
        if "| File | Purpose |" not in content:
            failures.append(f"{rel_path}: missing '| File | Purpose |' table header")
    assert not failures, "Sub-CLAUDE.md template violations:\n" + "\n".join(failures)


def test_channel_b_defined_in_process_claude_md():
    process_md = SRC_ROOT / "execution" / "process" / "CLAUDE.md"
    assert process_md.is_file(), "execution/process/CLAUDE.md does not exist"
    content = process_md.read_text()
    assert "Channel B" in content, "execution/process/CLAUDE.md must define Channel B"
    assert "JSONL" in content, "Channel B definition must mention JSONL"


def test_sub_claude_md_no_main_claude_md_duplication():
    numbered_section_re = re.compile(r"^## \*{0,2}\d+\.", re.MULTILINE)
    failures = []
    for rel_path in EXPECTED_SUB_CLAUDE_MDS:
        claude_md = SRC_ROOT / rel_path
        if not claude_md.is_file():
            continue
        content = claude_md.read_text()
        match = numbered_section_re.search(content)
        if match:
            failures.append(f"{rel_path}: contains '{match.group()}' (main CLAUDE.md section)")
    assert not failures, "Sub-CLAUDE.md files duplicate main sections:\n" + "\n".join(failures)
