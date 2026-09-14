"""Guidance requirements for large private folders and IL-0 type shards."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.arch._helpers import SRC_ROOT

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


def _private_folder_guidance_errors(root: Path) -> list[str]:
    errors: list[str] = []
    for folder in sorted(root.rglob("*")):
        if not folder.is_dir() or not folder.name.startswith("_"):
            continue
        if len(list(folder.glob("*.py"))) < 10:
            continue
        relative = folder.relative_to(root)
        guide = folder / "AGENTS.md"
        adapter = folder / "CLAUDE.md"
        if not guide.is_file() or not guide.read_text().strip():
            errors.append(f"{relative}/: missing or empty AGENTS.md")
        if not adapter.is_file() or adapter.read_text() != "@AGENTS.md\n":
            errors.append(f"{relative}/: CLAUDE.md must contain exactly @AGENTS.md\\n")
    return sorted(errors)


def test_private_folder_has_agents_md() -> None:
    assert not (errors := _private_folder_guidance_errors(SRC_ROOT)), "\n".join(errors)


def test_private_folder_guidance_starts_at_ten_files(tmp_path: Path) -> None:
    folder = tmp_path / "_fixture"
    folder.mkdir()
    for index in range(9):
        (folder / f"file_{index}.py").touch()
    assert _private_folder_guidance_errors(tmp_path) == []
    (folder / "file_9.py").touch()
    assert _private_folder_guidance_errors(tmp_path) == [
        "_fixture/: CLAUDE.md must contain exactly @AGENTS.md\\n",
        "_fixture/: missing or empty AGENTS.md",
    ]
