"""The doc-count hook runs when any count authority changes."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / ".pre-commit-config.yaml"

_COUNT_SOURCES = (
    "src/autoskillit/skills/",
    "src/autoskillit/skills_extended/",
    "src/autoskillit/recipes/",
    "src/autoskillit/core/types/_type_constants_registries.py",
)


def _doc_counts_files_pattern() -> re.Pattern[str]:
    config = CONFIG_PATH.read_text(encoding="utf-8")
    hook = re.search(
        r"(?ms)^      - id: doc-counts\n(?P<body>.*?)(?=^      - id:|\Z)",
        config,
    )
    assert hook is not None, "doc-counts hook is missing"
    files = re.search(r"(?m)^        files: (?P<pattern>.+)$", hook.group("body"))
    assert files is not None, "doc-counts hook must declare a files pattern"
    return re.compile(files.group("pattern"))


def test_doc_counts_hook_covers_every_count_source() -> None:
    pattern = _doc_counts_files_pattern()
    uncovered = [source for source in _COUNT_SOURCES if pattern.search(source) is None]

    assert not uncovered, f"doc-counts hook misses count sources: {uncovered}"


def test_doc_counts_source_alternatives_exist() -> None:
    missing = [source for source in _COUNT_SOURCES if not (PROJECT_ROOT / source).exists()]

    assert not missing, f"doc-counts source paths are stale: {missing}"
