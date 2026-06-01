"""Architectural invariant: skills with prose write restrictions have runtime enforcement.

Tests:
  - Skills whose SKILL.md NEVER block prohibits source modification have runtime
    enforcement via read_only: true, output_dir in all recipe invocations, or an
    explicit allowlist entry.
  - Every run_skill step in planner.yaml declares output_dir.
  - Skills matching audit-*, validate-*, review-*, rectify, dry-walkthrough,
    design-guards have runtime write restriction backing.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from autoskillit.core.io import load_yaml

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

# Skills that genuinely need unrestricted write access — no output_dir restriction applied.
# Adding a skill here requires explicit justification: why does it need unrestricted write?
UNRESTRICTED_WRITE_SKILLS: frozenset[str] = frozenset(
    {
        "sous-chef",  # orchestrator: delegates all writes to child skills
        "open-kitchen",  # server lifecycle only
        "close-kitchen",  # server lifecycle only
        "init",  # project initialization: writes config
        "update-config",  # config mutation by design
        "build-execution-map",  # bem-wrapper.yaml step has no CWD anchor for output_dir
    }
)

# Patterns that indicate a NEVER block prohibits writing to source/code files.
# Checked case-insensitively against the full NEVER block text.
_WRITE_RESTRICTION_PATTERNS: list[str] = [
    r"modify.*source",
    r"modify.*code",
    r"read.only.*anal",  # "read-only analysis"
    r"read.only audit",
    r"do not.*write",
    r"do not.*edit",
    r"do not.*modify",
    r"must never.*write",
    r"must never.*edit",
    r"must never.*modify",
    r"no.*write.*source",
    r"write.*outside.*autoskillit",  # "Write output outside .autoskillit/temp/"
    r"modify any.*source",
    r"modify any.*code",
]

_SRC_ROOT = Path(__file__).parent.parent.parent / "src" / "autoskillit"
_SKILLS_ROOT = _SRC_ROOT / "skills_extended"
_CONTRACTS_PATH = _SRC_ROOT / "recipe" / "skill_contracts.yaml"
_RECIPES_DIR = _SRC_ROOT / "recipes"


def _extract_never_block(skill_md: str) -> str:
    """Return the text of the first NEVER block (from 'NEVER:' to the next '**ALWAYS'/'## ')."""
    upper = skill_md.upper()
    never_pos = upper.find("\n**NEVER")
    if never_pos == -1:
        never_pos = upper.find("\n## CRITICAL CONSTRAINTS")
    if never_pos == -1:
        return ""
    always_pos = upper.find("\n**ALWAYS", never_pos + 1)
    section_pos = upper.find("\n## ", never_pos + 1)
    # End at the earlier of ALWAYS or next section header
    end_pos = len(skill_md)
    if always_pos != -1:
        end_pos = min(end_pos, always_pos)
    if section_pos != -1:
        end_pos = min(end_pos, section_pos)
    return skill_md[never_pos:end_pos]


def _has_write_restriction_prose(skill_md: str) -> bool:
    """Return True if the NEVER block contains write restriction patterns."""
    never_block = _extract_never_block(skill_md).lower()
    if not never_block:
        return False
    return any(re.search(p, never_block) for p in _WRITE_RESTRICTION_PATTERNS)


def _load_contracts() -> dict:
    return load_yaml(_CONTRACTS_PATH)


def _skill_is_read_only(skill_name: str, contracts: dict) -> bool:
    skills = contracts.get("skills", {})
    entry = skills.get(skill_name, {})
    return bool(entry.get("read_only"))


def _skill_name_from_command(command: str) -> str | None:
    """Extract bare skill name from /autoskillit:name or /name command strings."""
    m = re.search(r"/(?:autoskillit:)?([a-z][a-z0-9-]+)", command)
    return m.group(1) if m else None


def _load_parsed_recipes() -> list[dict]:
    """Parse all recipe YAML files once and return their data dicts."""
    results: list[dict] = []
    for recipe_path in sorted(_RECIPES_DIR.glob("*.yaml")):
        data = load_yaml(recipe_path)
        if isinstance(data, dict):
            results.append(data)
    return results


def _recipe_invocations_have_output_dir(skill_name: str, parsed_recipes: list[dict]) -> bool:
    """Return True if the skill appears in recipes and all its invocations have output_dir."""
    invocation_count = 0
    covered_count = 0

    for data in parsed_recipes:
        steps = data.get("steps", {})
        if not isinstance(steps, dict):
            continue

        for step in steps.values():
            if not isinstance(step, dict):
                continue
            if step.get("tool") != "run_skill":
                continue
            with_block = step.get("with", {}) or {}
            skill_cmd = with_block.get("skill_command", "")
            if not isinstance(skill_cmd, str):
                continue
            name = _skill_name_from_command(skill_cmd)
            if name != skill_name:
                continue

            invocation_count += 1
            has_output_dir_in_with = bool(with_block.get("output_dir"))

            note = step.get("note", "") or ""
            note_invocations = re.findall(
                r'run_skill\([^)]*skill_command=["\'][^"\']*'
                + re.escape(skill_name)
                + r'[^"\']*["\'][^)]*\)',
                note,
                re.DOTALL,
            )
            note_covered = (
                all("output_dir=" in inv for inv in note_invocations) if note_invocations else True
            )

            if has_output_dir_in_with or (
                not has_output_dir_in_with and note_invocations and note_covered
            ):
                covered_count += 1

    if invocation_count == 0:
        return True
    return covered_count == invocation_count


def test_never_modify_source_skills_have_write_prefix() -> None:
    """Skills with NEVER blocks prohibiting source modification have runtime enforcement.

    Enforcement is satisfied by one of:
    1. read_only: true in skill_contracts.yaml
    2. Every recipe invocation (structured with: or note: prose) provides output_dir
    3. Explicit UNRESTRICTED_WRITE_SKILLS entry (with documented justification)
    """
    contracts = _load_contracts()
    parsed_recipes = _load_parsed_recipes()

    violations: list[str] = []
    for skill_dir in sorted(_SKILLS_ROOT.iterdir()):
        skill_md_path = skill_dir / "SKILL.md"
        if not skill_md_path.exists():
            continue
        skill_name = skill_dir.name

        skill_md = skill_md_path.read_text()
        if not _has_write_restriction_prose(skill_md):
            continue

        if skill_name in UNRESTRICTED_WRITE_SKILLS:
            continue
        if _skill_is_read_only(skill_name, contracts):
            continue
        if _recipe_invocations_have_output_dir(skill_name, parsed_recipes):
            continue

        violations.append(skill_name)

    assert not violations, (
        f"Skills with prose write restrictions but no runtime enforcement: {sorted(violations)}. "
        "Each must have read_only: true in skill_contracts.yaml, output_dir in every recipe "
        "invocation, or an entry in UNRESTRICTED_WRITE_SKILLS with justification."
    )


def test_planner_skills_always_have_output_dir() -> None:
    """Every run_skill step in planner.yaml must declare output_dir.

    This ensures Part A's decoupling provides write scope coverage for all
    planner skill sessions — no planner skill runs without an allowed_write_prefix.
    """
    planner_yaml = _RECIPES_DIR / "planner.yaml"
    data = load_yaml(planner_yaml)
    steps = data.get("steps", {})

    violations: list[str] = []
    for step_name, step in steps.items():
        if not isinstance(step, dict):
            continue
        if step.get("tool") != "run_skill":
            continue
        with_block = step.get("with", {}) or {}
        if not with_block.get("output_dir"):
            violations.append(step_name)

    assert not violations, (
        f"planner.yaml run_skill steps missing output_dir: {violations}. "
        "Every planner skill invocation must declare output_dir so Part A's "
        "decoupling sets the allowed_write_prefix before session launch."
    )


def test_audit_skills_have_output_dir_or_read_only() -> None:
    """Audit/validate/review/rectify/dry-walkthrough/design-guards have runtime write backing.

    These skills have NEVER blocks prohibiting source modification. After Part B,
    all their recipe invocations provide output_dir (either via structured YAML
    or via prose note instructions to the orchestrator).
    """
    target_patterns = [
        r"^audit-",
        r"^validate-",
        r"^review-",
        r"^rectify$",
        r"^dry-walkthrough$",
        r"^design-guards$",
    ]

    contracts = _load_contracts()
    parsed_recipes = _load_parsed_recipes()

    violations: list[str] = []
    for skill_dir in sorted(_SKILLS_ROOT.iterdir()):
        skill_name = skill_dir.name
        if not any(re.match(p, skill_name) for p in target_patterns):
            continue
        if not (skill_dir / "SKILL.md").exists():
            continue

        if skill_name in UNRESTRICTED_WRITE_SKILLS:
            continue
        if _skill_is_read_only(skill_name, contracts):
            continue
        if _recipe_invocations_have_output_dir(skill_name, parsed_recipes):
            continue

        violations.append(skill_name)

    assert not violations, (
        f"Audit/validate/review skills without runtime write backing: {sorted(violations)}. "
        "Each must have read_only: true, output_dir in every recipe invocation, "
        "or an entry in UNRESTRICTED_WRITE_SKILLS."
    )
