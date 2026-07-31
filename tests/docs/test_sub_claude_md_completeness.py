"""Structural tests for tracked AGENTS.md guides and CLAUDE.md adapters."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path, PurePosixPath

import pytest

pytestmark = pytest.mark.medium

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src" / "autoskillit"
_CAPTURE_GUIDE = "src/autoskillit/hooks/_capture/AGENTS.md"
_CAPTURE_ADAPTER = "src/autoskillit/hooks/_capture/CLAUDE.md"


def _load_tracked_guidance_paths(repo_root: Path = REPO_ROOT) -> frozenset[str]:
    result = subprocess.run(
        [
            "git",
            "ls-files",
            "--cached",
            "--",
            ":(glob)**/AGENTS.md",
            ":(glob)**/CLAUDE.md",
        ],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return frozenset(
        path for path in result.stdout.splitlines() if path and (repo_root / path).is_file()
    )


TRACKED_GUIDANCE_PATHS = _load_tracked_guidance_paths()
ROOT_GUIDANCE_PATHS = frozenset(path for path in TRACKED_GUIDANCE_PATHS if "/" not in path)
GITHUB_GUIDANCE_PATHS = frozenset(
    path for path in TRACKED_GUIDANCE_PATHS if path.startswith(".github/")
)
SRC_GUIDANCE_PATHS = frozenset(
    path for path in TRACKED_GUIDANCE_PATHS if path.startswith("src/autoskillit/")
)
TEST_GUIDANCE_PATHS = frozenset(
    path for path in TRACKED_GUIDANCE_PATHS if path.startswith("tests/")
)
ALL_GUIDES = frozenset(
    path for path in TRACKED_GUIDANCE_PATHS if PurePosixPath(path).name == "AGENTS.md"
)
ALL_ADAPTERS = frozenset(
    path for path in TRACKED_GUIDANCE_PATHS if PurePosixPath(path).name == "CLAUDE.md"
)
NON_ROOT_GUIDES = ALL_GUIDES - {"AGENTS.md"}
NON_ROOT_ADAPTERS = ALL_ADAPTERS - {"CLAUDE.md"}

_FENCE_START_RE = re.compile(r"^\s*(?P<fence>`{3,}|~{3,})")
_CATALOG_HEADING_RE = re.compile(r"^\s*##\s+(?:Files|Test Files|Flat Files)\s*$", re.IGNORECASE)
_CATALOG_HEADER_RE = re.compile(r"^\s*\|\s*File\s*\|\s*Purpose\s*\|\s*$", re.IGNORECASE)
_BACKTICKED_CELL_RE = re.compile(r"^(?:~~)?`(?P<value>[^`\n]+)`(?:~~)?$")


def _sibling(path: str, basename: str) -> str:
    return str(PurePosixPath(path).with_name(basename))


def _lines_outside_fences(markdown: str) -> list[tuple[int, str]]:
    visible: list[tuple[int, str]] = []
    active_fence: tuple[str, int] | None = None
    for line_number, line in enumerate(markdown.splitlines(), start=1):
        if active_fence is not None:
            marker, minimum_length = active_fence
            if re.fullmatch(rf"\s*{re.escape(marker)}{{{minimum_length},}}\s*", line):
                active_fence = None
            continue

        if line.startswith(("    ", "\t")):
            continue

        match = _FENCE_START_RE.match(line)
        if match:
            fence = match.group("fence")
            active_fence = (fence[0], len(fence))
            continue
        visible.append((line_number, line))
    return visible


def _two_column_cells(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not (stripped.startswith("|") and stripped.endswith("|")):
        return None
    cells = tuple(cell.strip() for cell in stripped[1:-1].split("|"))
    if len(cells) != 2:
        return None
    return cells


def _is_file_cell(cell: str) -> bool:
    match = _BACKTICKED_CELL_RE.fullmatch(cell)
    if match is not None:
        value = match.group("value").strip()
    else:
        if "`" in cell:
            return False
        value = cell.removeprefix("~~").removesuffix("~~").strip()
    if value.endswith("/"):
        return False
    basename = PurePosixPath(value).name
    return basename not in {"", ".", ".."} and "." in basename


def _catalog_violations(markdown: str) -> list[str]:
    violations: list[str] = []
    file_row_streak: list[int] = []

    def flush_file_rows() -> None:
        if len(file_row_streak) >= 2:
            violations.append(
                "contiguous two-column file rows at lines "
                + ", ".join(str(line) for line in file_row_streak)
            )
        file_row_streak.clear()

    for line_number, line in _lines_outside_fences(markdown):
        if _CATALOG_HEADING_RE.fullmatch(line):
            violations.append(f"catalog heading at line {line_number}")
        if _CATALOG_HEADER_RE.fullmatch(line):
            violations.append(f"catalog header at line {line_number}")

        cells = _two_column_cells(line)
        if cells is not None and _is_file_cell(cells[0]):
            file_row_streak.append(line_number)
        else:
            flush_file_rows()
    flush_file_rows()
    return violations


def test_load_tracked_guidance_paths_excludes_missing_worktree_files(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    subprocess.run(
        ["git", "init"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    (repo_root / "AGENTS.md").write_text("# Guide\n", encoding="utf-8")
    (repo_root / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "AGENTS.md", "CLAUDE.md"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    (repo_root / "CLAUDE.md").unlink()

    assert _load_tracked_guidance_paths(repo_root) == frozenset({"AGENTS.md"})


def test_tracked_guidance_families_partition_all_paths() -> None:
    families = (
        ROOT_GUIDANCE_PATHS,
        GITHUB_GUIDANCE_PATHS,
        SRC_GUIDANCE_PATHS,
        TEST_GUIDANCE_PATHS,
    )
    assert frozenset().union(*families) == TRACKED_GUIDANCE_PATHS
    assert sum(len(family) for family in families) == len(TRACKED_GUIDANCE_PATHS)


def test_guide_adapter_sibling_contract() -> None:
    expected_adapters = {_sibling(guide, "CLAUDE.md") for guide in ALL_GUIDES - {_CAPTURE_GUIDE}}
    expected_guides = {_sibling(adapter, "AGENTS.md") for adapter in ALL_ADAPTERS}

    assert ALL_ADAPTERS == expected_adapters
    assert ALL_GUIDES - {_CAPTURE_GUIDE} == expected_guides
    assert _CAPTURE_GUIDE in ALL_GUIDES
    assert _CAPTURE_ADAPTER not in ALL_ADAPTERS
    assert not (REPO_ROOT / _CAPTURE_ADAPTER).exists()


def test_non_root_adapters_are_exact_thin_shims() -> None:
    failures = []
    for adapter in sorted(NON_ROOT_ADAPTERS):
        content = (REPO_ROOT / adapter).read_text(encoding="utf-8")
        if content != "@AGENTS.md\n":
            failures.append(adapter)
    assert not failures, f"Non-root CLAUDE.md files must be exact @AGENTS.md shims: {failures}"


def test_catalog_detector_rejects_renamed_and_headerless_inventories() -> None:
    renamed = """\
## Components
| Module | Role |
|---|---|
| `alpha.py` | Alpha |
| `beta.py` | Beta |
"""
    headerless = """\
Architecture notes.
| `alpha.py` | Alpha |
| `nested/beta.py` | Beta |
"""
    unquoted = """\
Architecture notes.
| alpha.py | Alpha |
| nested/beta.py | Beta |
"""
    assert _catalog_violations(renamed)
    assert _catalog_violations(headerless)
    assert _catalog_violations(unquoted)


@pytest.mark.parametrize(
    "markdown",
    [
        """\
| Package | Purpose |
|---|---|
| `core/` | Foundation |
| `server/` | MCP server |
""",
        """\
| Package | IL | Purpose |
|---|---|---|
| `core/` | IL-0 | Foundation |
| `server/` | IL-3 | MCP server |
""",
        """\
| Guard | Fail-closed condition | Rationale |
|---|---|---|
| `open_kitchen_guard.py` | Unknown tier | Deny |
| `background_exec_guard.py` | Unknown tier | Deny |
""",
        """\
| Category | Tag(s) | Hidden? | Gated? | Example |
|---|---|---|---|---|
| Standard kitchen | `kitchen` | Yes | Yes | `run_cmd` |
""",
        """\
| Marker | Intended maximum |
|---|---|
| `small` | under one second |
| `medium` | under ten seconds |
""",
        """\
```markdown
## Files
| File | Purpose |
|---|---|
| `example.py` | Fenced example |
| `other.py` | Fenced example |
```
""",
        """\
    ## Files
    | File | Purpose |
    |---|---|
    | `example.py` | Indented example |
    | `other.py` | Indented example |
""",
    ],
)
def test_catalog_detector_preserves_non_catalog_structures(markdown: str) -> None:
    assert _catalog_violations(markdown) == []


def test_all_guides_are_catalog_free() -> None:
    failures = {}
    for guide in sorted(ALL_GUIDES):
        violations = _catalog_violations((REPO_ROOT / guide).read_text(encoding="utf-8"))
        if violations:
            failures[guide] = violations
    assert not failures, f"Per-file AGENTS.md catalogs are forbidden: {failures}"


def test_channel_b_defined_in_process_agents_md() -> None:
    process_md = SRC_ROOT / "execution" / "process" / "AGENTS.md"
    assert process_md.is_file(), "execution/process/AGENTS.md does not exist"
    content = process_md.read_text(encoding="utf-8")
    for marker in (
        "session JSONL monitor",
        "`assistant`-type record",
        "JSONL filename stem",
        "race concurrently",
        "Channel A takes precedence",
    ):
        assert marker in content, f"Channel B contract must retain {marker!r}"


def test_capture_guide_retains_isolated_import_contract() -> None:
    capture_guide = REPO_ROOT / _CAPTURE_GUIDE
    assert capture_guide.is_file()
    content = capture_guide.read_text(encoding="utf-8")
    for marker in ("stdlib-only", "hooks directory alone", "`sys.path`"):
        assert marker in content, f"_capture guide must retain {marker!r}"


@pytest.mark.parametrize(
    ("guide", "markers"),
    [
        (
            "src/autoskillit/cli/session/AGENTS.md",
            ("sole cook-attempt `Popen` owner",),
        ),
        (
            "src/autoskillit/cli/ui/AGENTS.md",
            ("every CLI `input()`", "`timed_prompt()`"),
        ),
        (
            "src/autoskillit/execution/backends/AGENTS.md",
            ("sole composed prelaunch transaction",),
        ),
        (
            "src/autoskillit/server/AGENTS.md",
            ("`make_context()`", "sole legal instantiation point", "dispatch_food_truck"),
        ),
        (
            "src/autoskillit/hooks/guards/AGENTS.md",
            (
                "write-scoped sessions",
                "allowed prefix",
                "L3-to-L3 recursion",
                "commit --amend",
                "push --force",
                "reset --hard",
                "clean -f",
                "checkout .",
                "hooks.json",
                "settings.json",
                "contracts/",
            ),
        ),
        (
            "src/autoskillit/server/tools/AGENTS.md",
            ("`serve_recipe()`", "only legal caller", "`load_and_validate`"),
        ),
        (
            "tests/cli/AGENTS.md",
            ("`legacy_home`", "pre-existing"),
        ),
        (
            "tests/contracts/AGENTS.md",
            ("`_anti_fab_helpers.py`", "production anti-fabrication guard"),
        ),
        (
            "tests/contracts/AGENTS.md",
            ("`_projection_helpers.py`", "stale snapshots"),
        ),
        (
            "tests/core/AGENTS.md",
            ("autouse fixture", "`collect_version_snapshot`"),
        ),
        (
            "tests/hooks/AGENTS.md",
            ("autouse fixture", "project-root CWD"),
        ),
        (
            "tests/infra/AGENTS.md",
            ("`FormatterCoverageDef`", "`_FORMATTER_COVERAGE_REGISTRY`"),
        ),
        (
            "tests/server/AGENTS.md",
            ("`_pipeline_test_helpers.py`", "shared pipeline"),
        ),
        (
            "tests/server/AGENTS.md",
            ("`_type_coercion_fixtures.py`", "type-coercion fixtures"),
        ),
        (
            "tests/workspace/AGENTS.md",
            ("`_helpers.py`", "`_CODEX_CAPABILITIES`"),
        ),
        (
            "tests/execution/AGENTS.md",
            ("`_make_watcher`", "`_queue_state`"),
        ),
        (
            "tests/execution/AGENTS.md",
            ("exact-identity launch binding", "pytest-free"),
        ),
        (
            "tests/execution/AGENTS.md",
            ("installed-CLI parse gate", "`--update-fixtures`"),
        ),
    ],
)
def test_catalog_carried_behavior_is_preserved(guide: str, markers: tuple[str, ...]) -> None:
    content = " ".join((REPO_ROOT / guide).read_text(encoding="utf-8").split()).casefold()
    for marker in markers:
        assert marker.casefold() in content, f"{guide} must retain {marker!r}"


def test_sub_claude_md_no_main_claude_md_duplication() -> None:
    numbered_section_re = re.compile(r"^## \*{0,2}\d+\.", re.MULTILINE)
    failures = []
    for guide in sorted(NON_ROOT_GUIDES):
        content = (REPO_ROOT / guide).read_text(encoding="utf-8")
        match = numbered_section_re.search(content)
        if match:
            failures.append(f"{guide}: contains '{match.group()}' (root AGENTS.md section)")
    assert not failures, "Sub-AGENTS.md files duplicate root sections:\n" + "\n".join(failures)
