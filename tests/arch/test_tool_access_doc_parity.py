"""Keep the documented tool access map aligned with decorators and tier registries."""

from __future__ import annotations

import ast
from collections import Counter, defaultdict

import pytest

from tests.arch._helpers import SRC_ROOT, _is_mcp_tool_decorator, _tool_module_paths
from tests.arch.test_transforms_hygiene import _iter_literal_string_tag_values

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_DOC_PATH = SRC_ROOT.parent.parent / "docs" / "execution" / "tool-access.md"


def _heading_lines(lines: list[str], heading: str) -> list[str]:
    start = lines.index(heading) + 1
    end = next(
        (index for index in range(start, len(lines)) if lines[index].startswith("## ")),
        len(lines),
    )
    return lines[start:end]


def _documented_access() -> tuple[dict[str, str], list[tuple[str, str, tuple[str, ...], str]]]:
    lines = _DOC_PATH.read_text().splitlines()
    glossary_lines = _heading_lines(lines, "## FastMCP Tag Glossary")
    header = "| Tag | Abbrev | Meaning |"
    assert header in glossary_lines, f"{_DOC_PATH}: missing glossary header {header}"
    abbreviations: dict[str, str] = {}
    for line in glossary_lines[glossary_lines.index(header) + 1 :]:
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 3 or cells[0].startswith("-"):
            continue
        tag, abbreviation = cells[0].strip("`"), cells[1].strip("`")
        assert abbreviation not in abbreviations, (
            f"duplicate glossary abbreviation: {abbreviation}"
        )
        abbreviations[abbreviation] = tag

    map_lines = _heading_lines(lines, "## Complete MCP Tool Access Control Map")
    rows: list[tuple[str, str, tuple[str, ...], str]] = []
    section = ""
    for line in map_lines:
        if line.startswith("### "):
            section = line.removeprefix("### ").strip()
        elif line.startswith("| `"):
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            assert section and len(cells) >= 3, f"access-map row outside a section: {line}"
            assert cells[0].startswith("`") and cells[0].endswith("`"), line
            assert cells[2].startswith("`") and cells[2].endswith("`"), line
            tool = cells[0].strip("`")
            tags = tuple(tag.strip() for tag in cells[1].split(","))
            source = cells[2].strip("`")
            rows.append((section, tool, tags, source))
    return abbreviations, rows


def _decorated_tools() -> dict[str, tuple[set[str], str]]:
    decorated: dict[str, tuple[set[str], str]] = {}
    for path in _tool_module_paths(SRC_ROOT / "server" / "tools"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not _is_mcp_tool_decorator(decorator):
                    continue
                assert isinstance(decorator, ast.Call), f"{path}:{node.lineno} missing tool tags"
                tags_value = next(
                    (keyword.value for keyword in decorator.keywords if keyword.arg == "tags"),
                    None,
                )
                assert isinstance(tags_value, ast.Set), f"{path}:{node.lineno} nonliteral tags"
                tags = list(_iter_literal_string_tag_values(tags_value))
                assert len(tags) == len(tags_value.elts), f"{path}:{node.lineno} nonliteral tags"
                assert node.name not in decorated, f"duplicate tool decorator: {node.name}"
                decorated[node.name] = (
                    set(tags),
                    path.relative_to(SRC_ROOT).as_posix(),
                )
    return decorated


def test_access_map_matches_decorators() -> None:
    abbreviations, rows = _documented_access()
    decorated = _decorated_tools()
    counts = Counter(tool for _section, tool, _tags, _source in rows)
    assert counts == Counter({tool: 1 for tool in decorated}), (
        f"access-map tool count mismatch: missing={sorted(decorated.keys() - counts.keys())}, "
        f"extra={sorted(counts.keys() - decorated.keys())}, "
        f"duplicates={sorted(tool for tool, count in counts.items() if count > 1)}"
    )
    for _section, tool, tag_abbreviations, source in rows:
        unknown = set(tag_abbreviations) - abbreviations.keys()
        assert not unknown, f"{tool}: unknown tag abbreviations {sorted(unknown)}"
        expected_tags, expected_source = decorated[tool]
        assert {
            abbreviations[abbreviation] for abbreviation in tag_abbreviations
        } == expected_tags, f"{tool}: documented tags disagree with decorator"
        assert source == expected_source, f"{tool}: source is {source}, expected {expected_source}"


def test_access_map_sections_match_tiers() -> None:
    from autoskillit.core import (
        EVIDENCE_READER_TOOLS,
        EXPLORATION_TOOLS,
        FLEET_TOOLS,
        FREE_RANGE_TOOLS,
        GATED_TOOLS,
        HEADLESS_TOOLS,
    )

    _abbreviations, rows = _documented_access()
    counts = Counter(tool for _section, tool, _tags, _source in rows)
    assert all(count == 1 for count in counts.values()), "each tool must occur in one section"
    sections: dict[str, set[str]] = defaultdict(set)
    for section, tool, _tags, _source in rows:
        sections[section].add(tool)

    expected = {
        "FREE RANGE": FREE_RANGE_TOOLS,
        "HEADLESS-TAGGED": HEADLESS_TOOLS,
        "AUTHENTICATED EVIDENCE READER": EVIDENCE_READER_TOOLS,
        "EXPLORATION BROKERS": EXPLORATION_TOOLS,
        "KITCHEN — Fleet": FLEET_TOOLS,
    }
    kitchen_sections = {
        section
        for section in sections
        if section.startswith("KITCHEN — ") and section != "KITCHEN — Fleet"
    }
    assert not (set(sections) - expected.keys() - kitchen_sections), "unknown access-map section"
    for section, tools in expected.items():
        assert sections.get(section, set()) == tools, (
            f"{section}: documented tier differs from registry"
        )
    kitchen_tools = set().union(*(sections[section] for section in kitchen_sections))
    assert kitchen_tools == (
        GATED_TOOLS - FLEET_TOOLS - EVIDENCE_READER_TOOLS - EXPLORATION_TOOLS
    ), "KITCHEN sections differ from the gated tier"
