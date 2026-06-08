"""Structural tests for per-subfolder CLAUDE.md documentation files."""

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.small

SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "autoskillit"

EXPECTED_SUB_CLAUDE_MDS = [
    "core/types/AGENTS.md",
    "core/runtime/AGENTS.md",
    "execution/headless/AGENTS.md",
    "execution/process/AGENTS.md",
    "execution/session/AGENTS.md",
    "execution/merge_queue/AGENTS.md",
    "recipe/rules/AGENTS.md",
    "recipe/rules/campaign/AGENTS.md",
    "recipe/rules/ci/AGENTS.md",
    "recipe/rules/dataflow/AGENTS.md",
    "recipe/rules/graph/AGENTS.md",
    "server/tools/AGENTS.md",
    "cli/doctor/AGENTS.md",
    "cli/fleet/AGENTS.md",
    "cli/session/AGENTS.md",
    "cli/ui/AGENTS.md",
    "cli/update/AGENTS.md",
    "hooks/guards/AGENTS.md",
    "hooks/formatters/AGENTS.md",
    "AGENTS.md",  # src/autoskillit/ package root
    "core/AGENTS.md",
    "config/AGENTS.md",
    "pipeline/AGENTS.md",
    "execution/AGENTS.md",
    "workspace/AGENTS.md",
    "planner/AGENTS.md",
    "recipe/AGENTS.md",
    "migration/AGENTS.md",
    "fleet/AGENTS.md",
    "cli/AGENTS.md",
    "hooks/AGENTS.md",
    "agents/AGENTS.md",
]


def test_all_32_sub_claude_md_files_exist():
    assert len(EXPECTED_SUB_CLAUDE_MDS) == 32, (
        f"Expected 32 entries, got {len(EXPECTED_SUB_CLAUDE_MDS)}"
    )
    assert len(EXPECTED_SUB_CLAUDE_MDS) == len(set(EXPECTED_SUB_CLAUDE_MDS)), (
        "Duplicate entries in EXPECTED_SUB_CLAUDE_MDS"
    )
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


def test_channel_b_defined_in_process_agents_md():
    process_md = SRC_ROOT / "execution" / "process" / "AGENTS.md"
    assert process_md.is_file(), "execution/process/AGENTS.md does not exist"
    content = process_md.read_text()
    assert "Channel B" in content, "execution/process/AGENTS.md must define Channel B"
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
