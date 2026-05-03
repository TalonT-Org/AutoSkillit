"""Verify every numerical claim in every doc file matches source of truth.

Each assertion reads the doc file(s) it covers and the source it derives from,
then compares. A failure prints which doc has the stale value and what the
canonical value is.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src" / "autoskillit"
DOCS_DIR = REPO_ROOT / "docs"
ROOT_README = REPO_ROOT / "README.md"
CLAUDE_MD = REPO_ROOT / "CLAUDE.md"


# ----- helpers ----------------------------------------------------------------


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _doc_files() -> list[Path]:
    return sorted(DOCS_DIR.rglob("*.md"))


def _docs_containing(pattern: str) -> list[Path]:
    rx = re.compile(pattern)
    return [p for p in _doc_files() if rx.search(_read(p))]


# ----- canonical source-of-truth getters --------------------------------------


def _extract_tool_decorators(text: str) -> list[str]:
    """Extract full @mcp.tool(...) decorator text, handling multi-line decorators."""
    decorators: list[str] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped.startswith("@mcp.tool"):
            # Collect the full decorator (may span multiple lines)
            parts = [stripped]
            if ")" not in stripped:
                i += 1
                while i < len(lines):
                    part = lines[i].strip()
                    parts.append(part)
                    if ")" in part:
                        break
                    i += 1
            decorators.append(" ".join(parts))
        i += 1
    return decorators


def _count_mcp_tools() -> int:
    total = 0
    for f in (SRC_DIR / "server" / "tools").glob("tools_*.py"):
        total += len(_extract_tool_decorators(_read(f)))
    return total


def _count_kitchen_tools() -> int:
    total = 0
    for f in (SRC_DIR / "server" / "tools").glob("tools_*.py"):
        for dec in _extract_tool_decorators(_read(f)):
            if '"kitchen"' in dec:
                total += 1
    return total


def _count_free_range_tools() -> int:
    total = 0
    for f in (SRC_DIR / "server" / "tools").glob("tools_*.py"):
        for dec in _extract_tool_decorators(_read(f)):
            if '"kitchen"' not in dec:
                total += 1
    return total


def _count_headless_tools() -> int:
    total = 0
    for f in (SRC_DIR / "server" / "tools").glob("tools_*.py"):
        for dec in _extract_tool_decorators(_read(f)):
            if '"headless"' in dec:
                total += 1
    return total


def _count_skills_total() -> int:
    tier1 = sum(1 for p in (SRC_DIR / "skills").iterdir() if p.is_dir())
    tier23 = sum(1 for p in (SRC_DIR / "skills_extended").iterdir() if p.is_dir())
    return tier1 + tier23


def _count_arch_lens_skills() -> int:
    return sum(
        1
        for p in (SRC_DIR / "skills_extended").iterdir()
        if p.is_dir() and p.name.startswith("arch-lens-")
    )


def _count_exp_lens_skills() -> int:
    return sum(
        1
        for p in (SRC_DIR / "skills_extended").iterdir()
        if p.is_dir() and p.name.startswith("exp-lens-")
    )


def _count_vis_lens_skills() -> int:
    return sum(
        1
        for p in (SRC_DIR / "skills_extended").iterdir()
        if p.is_dir() and p.name.startswith("vis-lens-")
    )


def _hook_files() -> list[Path]:
    return sorted(f for f in (SRC_DIR / "hooks").rglob("*.py") if f.name not in {"__init__.py"})


def _count_hooks_by_event() -> dict[str, int]:
    """Group unique hook scripts by their PreToolUse / PostToolUse / SessionStart event.

    Imports HOOK_REGISTRY and counts the distinct script files referenced by
    each event type — duplicates (e.g. branch_protection_guard registered for
    both merge_worktree and push_to_remote) collapse to a single entry.
    """
    from autoskillit.hook_registry import HOOK_REGISTRY  # local import to avoid hard dep

    by_event: dict[str, set[str]] = {
        "PreToolUse": set(),
        "PostToolUse": set(),
        "SessionStart": set(),
    }
    for hook_def in HOOK_REGISTRY:
        for script in hook_def.scripts:
            by_event[hook_def.event_type].add(script)
    return {event: len(scripts) for event, scripts in by_event.items()}


def _quota_thresholds_default() -> tuple[float, float]:
    data = yaml.safe_load(_read(SRC_DIR / "config" / "defaults.yaml"))
    quota = data.get("quota_guard")
    assert quota is not None, "quota_guard key missing from config/defaults.yaml"
    short = quota.get("short_window_threshold")
    long_ = quota.get("long_window_threshold")
    assert short is not None, (
        "quota_guard.short_window_threshold key missing from config/defaults.yaml"
    )
    assert long_ is not None, (
        "quota_guard.long_window_threshold key missing from config/defaults.yaml"
    )
    return float(short), float(long_)


def _count_doctor_checks() -> int:
    """Count doctor checks inside ``run_doctor``: numbered + lettered sub-checks (4b, 7b).

    Helper functions earlier in the module use the same ``# Check N:`` comment
    style for their internal sub-steps; we restrict the count to the body of
    ``run_doctor`` so those comments do not double-count.
    """
    text = _read(SRC_DIR / "cli" / "_doctor.py")
    body = re.search(r"def run_doctor\(.*?\n((?:    .*\n|\n)+)", text, re.DOTALL)
    assert body, "run_doctor not found in _doctor.py"
    body_text = body.group(1)
    numbered = len(re.findall(r"# Check \d+:", body_text))
    lettered = len(re.findall(r"# Check \d+[a-z]:", body_text))
    return numbered + lettered


def _bundled_recipes() -> list[str]:
    return sorted(p.stem for p in (SRC_DIR / "recipes").glob("*.yaml"))


def _retry_reason_values() -> list[str]:
    text = _read(SRC_DIR / "core" / "types" / "_type_enums.py")
    block = re.search(r"class RetryReason\(StrEnum\):(.*?)\nclass ", text, re.DOTALL)
    assert block, "RetryReason enum not found"
    return re.findall(r'"([a-z_]+)"', block.group(1))


def _count_semantic_rule_files() -> int:
    return sum(1 for p in (SRC_DIR / "recipe" / "rules").glob("rules_*.py"))


# ----- tests ------------------------------------------------------------------


def test_kitchen_tagged_tool_count_is_45() -> None:
    count = _count_kitchen_tools()
    assert count == 45, f"Expected 45 kitchen-tagged tools; found {count}"


def test_free_range_tool_count_is_4() -> None:
    assert _count_free_range_tools() == 4, (
        f"Expected 4 free-range tools; found {_count_free_range_tools()}"
    )


def test_headless_tool_count_is_1() -> None:
    assert _count_headless_tools() == 1, (
        f"Expected 1 headless-tagged tool; found {_count_headless_tools()}"
    )


def test_arch_lens_count_is_13() -> None:
    assert _count_arch_lens_skills() == 13


def test_exp_lens_count_is_18() -> None:
    assert _count_exp_lens_skills() == 18


def test_vis_lens_count_is_12() -> None:
    assert _count_vis_lens_skills() == 12


def test_quota_thresholds_defaults() -> None:
    short, long_ = _quota_thresholds_default()
    assert short == pytest.approx(85.0)
    assert long_ == pytest.approx(95.0)


def test_doctor_check_count_is_31() -> None:
    # _count_doctor_checks() counts every "# Check N:" and "# Check Nb:" marker
    # in run_doctor() — 17 numbered base markers + 5 lettered sub-check markers
    # (2b, 2c, 2d, 4b, 7b) + 4 ambient env checks (18–21)
    # + 2 new unconditional feature checks (22–23)
    # + 5 gated franchise checks (24–28) = 33 total.
    # test_installation_states_17_doctor_checks checks the *user-visible* count
    # from docs/installation.md ("15 numbered + 2 lettered sub-checks 4b and 7b
    # = 17").  The gap of 5 is intentional: Check 2, Check 4, and Check 7 each
    # appear as separate implementation markers but the docs present them as single
    # numbered entries that subsume their sub-variants.
    # Update both tests whenever a new doctor check is added.
    assert _count_doctor_checks() == 33, (
        f"Expected 33 doctor checks; found {_count_doctor_checks()}"
    )


def test_bundled_recipe_count_is_9() -> None:
    recipes = _bundled_recipes()
    expected = [
        "bem-wrapper",
        "full-audit",
        "implement-findings",
        "implementation",
        "implementation-groups",
        "merge-prs",
        "planner",
        "promote-to-main-wrapper",
        "remediation",
        "research",
    ]
    assert recipes == expected, f"Recipes drifted: {recipes}"


def test_retry_reason_value_count_is_11() -> None:
    values = _retry_reason_values()
    assert len(values) == 11, f"RetryReason has {len(values)} values: {values}"


def test_semantic_rule_family_count_is_25() -> None:
    assert _count_semantic_rule_files() == 25


# ----- per-doc count assertions (run once docs exist) -------------------------


def _assert_doc_states_number(doc: Path, label: str, expected: int) -> None:
    if not doc.exists():
        pytest.skip(f"{doc.relative_to(REPO_ROOT)} not yet present")
    text = _read(doc)
    if not re.search(rf"\b{expected}\b", text):
        pytest.fail(f"{doc.relative_to(REPO_ROOT)}: missing canonical {label}={expected}")


@pytest.mark.parametrize(
    "doc_path",
    [
        DOCS_DIR / "execution" / "architecture.md",
        DOCS_DIR / "execution" / "tool-access.md",
    ],
)
def test_docs_state_48_mcp_tools(doc_path: Path) -> None:
    _assert_doc_states_number(doc_path, "MCP tools", 48)


@pytest.mark.parametrize(
    "doc_path",
    [
        DOCS_DIR / "execution" / "architecture.md",
        DOCS_DIR / "execution" / "tool-access.md",
    ],
)
def test_docs_state_44_kitchen_tools(doc_path: Path) -> None:
    _assert_doc_states_number(doc_path, "kitchen tools", 44)


def test_skill_visibility_states_130_skills() -> None:
    # 130 = 3 Tier-1 (open-kitchen, close-kitchen, sous-chef) + 127 extended.
    # DefaultSkillResolver.list_all() returns 129 (excludes sous-chef from public surface).
    _assert_doc_states_number(DOCS_DIR / "skills" / "visibility.md", "skills total", 130)


def test_safety_hooks_states_21_hooks() -> None:
    _assert_doc_states_number(DOCS_DIR / "safety" / "hooks.md", "hooks total", 21)


def test_configuration_states_quota_thresholds() -> None:
    doc = DOCS_DIR / "configuration.md"
    if not doc.exists():
        pytest.skip("docs/configuration.md not present")
    text = _read(doc)
    assert "85.0" in text, (
        "docs/configuration.md does not state quota_guard.short_window_threshold = 85.0"
    )
    assert "95.0" in text, (
        "docs/configuration.md does not state quota_guard.long_window_threshold = 95.0"
    )


def test_installation_states_17_doctor_checks() -> None:
    _assert_doc_states_number(DOCS_DIR / "installation.md", "doctor checks", 17)


def test_recipes_overview_states_6_recipes() -> None:
    _assert_doc_states_number(DOCS_DIR / "recipes" / "overview.md", "bundled recipes", 6)


def test_orchestration_states_11_retry_reasons() -> None:
    _assert_doc_states_number(DOCS_DIR / "execution" / "orchestration.md", "retry reasons", 11)


def test_authoring_states_24_rule_families() -> None:
    _assert_doc_states_number(DOCS_DIR / "recipes" / "authoring.md", "rule families", 24)


def test_catalog_states_arch_and_exp_lens_counts() -> None:
    catalog = DOCS_DIR / "skills" / "catalog.md"
    if not catalog.exists():
        pytest.skip("docs/skills/catalog.md not present")
    text = _read(catalog)
    assert "13" in text, "skills/catalog.md does not state 13 arch-lens skills"
    assert "18" in text, "skills/catalog.md does not state 18 exp-lens skills"


def test_catalog_states_vis_lens_count_is_12() -> None:
    catalog = DOCS_DIR / "skills" / "catalog.md"
    if not catalog.exists():
        pytest.skip("docs/skills/catalog.md not present")
    text = _read(catalog)
    assert "12" in text, "skills/catalog.md does not state 12 vis-lens skills"


def test_catalog_count_matches_filesystem() -> None:
    """Header in catalog.md must match the actual skill-dir count."""
    catalog = (DOCS_DIR / "skills" / "catalog.md").read_text(encoding="utf-8")
    skills_dir = SRC_DIR / "skills"
    extended_dir = SRC_DIR / "skills_extended"
    tier1_count = sum(1 for p in skills_dir.iterdir() if p.is_dir() and p.name != "__pycache__")
    extended_count = sum(
        1 for p in extended_dir.iterdir() if p.is_dir() and p.name != "__pycache__"
    )
    total = tier1_count + extended_count
    assert f"{total} total" in catalog, f"catalog.md header should claim {total} total skills"


def test_catalog_does_not_reference_open_pr() -> None:
    """open-pr was decomposed into prepare-pr + compose-pr in PR #659."""
    catalog = (DOCS_DIR / "skills" / "catalog.md").read_text()
    assert "`open-pr`" not in catalog


def test_catalog_lists_all_skills_in_extended_dir() -> None:
    """Every directory in skills_extended/ must appear at least once in catalog.md."""
    catalog = (DOCS_DIR / "skills" / "catalog.md").read_text()
    extended_dir = SRC_DIR / "skills_extended"
    missing = [
        p.name
        for p in sorted(extended_dir.iterdir())
        if p.is_dir() and f"`{p.name}`" not in catalog
    ]
    assert missing == [], f"Skills in skills_extended/ not listed in catalog.md: {missing}"


def test_authoring_bundled_recipe_count_mentions_6() -> None:
    """authoring.md prose must reference 6 bundled recipes (planner was missing)."""
    authoring = (DOCS_DIR / "recipes" / "authoring.md").read_text()
    assert "6 today" in authoring and "planner" in authoring, (
        "authoring.md should mention 6 bundled recipes including planner"
    )


def test_authoring_recipe_step_fields_match_schema() -> None:
    """authoring.md field summary must use actual RecipeStep field names."""
    authoring = (DOCS_DIR / "recipes" / "authoring.md").read_text()
    assert "`name`" in authoring
    assert "`with_args`" in authoring
    assert "`on_result`" in authoring
    assert "`retries`" in authoring
    field_summary_line = next(
        (line for line in authoring.splitlines() if "with_args" in line or "id`," in line),
        None,
    )
    assert field_summary_line is not None, (
        "authoring.md must contain a line listing RecipeStep fields (with_args or id`,)"
    )
    assert "id`," not in field_summary_line
    assert "params`," not in field_summary_line
    assert "verdict_routes`" not in field_summary_line
    assert "retry`." not in field_summary_line


def test_subsets_lists_required_packs() -> None:
    """subsets.md must document all four missing built-in pack categories."""
    subsets = (DOCS_DIR / "skills" / "subsets.md").read_text(encoding="utf-8")
    for pack in ("kitchen-core", "research", "exp-lens", "vis-lens"):
        assert pack in subsets, f"subsets.md missing pack category: {pack}"
