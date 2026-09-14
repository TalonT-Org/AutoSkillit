"""Guidance requirements for large private folders and IL-0 type shards."""

from __future__ import annotations

import re
from collections import Counter
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


def _type_shard_concern_map_errors(core_root: Path) -> list[str]:
    errors: list[str] = []
    for package in sorted(core_root.rglob("*")):
        if not package.is_dir() or not (package / "__init__.py").is_file():
            continue
        files = {path.name for path in package.glob("*.py")}
        if len(files) < 20:
            continue
        relative = package.relative_to(core_root)
        guide = package / "AGENTS.md"
        if not guide.is_file() or not guide.read_text().strip():
            errors.append(f"{relative}/: missing or empty AGENTS.md")
            continue
        lines = guide.read_text().splitlines()
        headings = [i for i, line in enumerate(lines) if line == "## Concern map"]
        if len(headings) != 1:
            errors.append(f"{relative}/: expected exactly one ## Concern map heading")
            continue
        section = lines[headings[0] + 1 :]
        section = section[
            : next((i for i, line in enumerate(section) if line.startswith("## ")), len(section))
        ]
        names: list[str] = []
        for line in section:
            if not line.startswith("- "):
                continue
            match = re.fullmatch(r"- `([^`]+\.py)` — (\S.*)", line)
            if match is None:
                errors.append(f"{relative}/: malformed or empty concern-map bullet: {line}")
            else:
                names.append(match.group(1))
        counts = Counter(names)
        missing = files - counts.keys()
        stale = counts.keys() - files
        duplicate = {name for name, count in counts.items() if count != 1}
        if missing or stale or duplicate:
            errors.append(
                f"{relative}/: missing={sorted(missing)}, duplicate={sorted(duplicate)}, "
                f"stale={sorted(stale)}"
            )
    return sorted(errors)


def test_type_shards_have_agents_md_concern_map() -> None:
    assert not (errors := _type_shard_concern_map_errors(SRC_ROOT / "core")), "\n".join(errors)


def test_type_shard_concern_map_starts_at_twenty_files(tmp_path: Path) -> None:
    core_root = tmp_path / "core"
    package = core_root / "fixture"
    package.mkdir(parents=True)
    (package / "__init__.py").touch()
    for index in range(18):
        (package / f"file_{index}.py").touch()
    assert _type_shard_concern_map_errors(core_root) == []
    (package / "file_18.py").touch()
    assert _type_shard_concern_map_errors(core_root) == ["fixture/: missing or empty AGENTS.md"]
