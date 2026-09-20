"""Skills that restrict writes in prose must declare the runtime boundary."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import autoskillit.hooks  # noqa: F401  (populates the deferred registry)
from autoskillit.core import load_yaml
from autoskillit.hook_registry import HOOK_REGISTRY, hook_applies_to_backend
from autoskillit.recipe._skill_placeholder_parser import extract_never_block
from autoskillit.workspace.skills._format import parse_frontmatter_content

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

UNRESTRICTED_WRITE_SKILLS: frozenset[str] = frozenset(
    {
        "sous-chef",  # orchestrator delegates writes to child skills
        "open-kitchen",  # server lifecycle only
        "close-kitchen",  # server lifecycle only
        "init",  # initializes project configuration
        "update-config",  # edits configuration by design
        "build-execution-map",  # worktree output follows a dynamic caller path
        "download-data",  # downloads into the caller's research worktree
        "stage-data",  # creates directories in the caller's research worktree
        "setup-environment",  # installs into the caller's research worktree
        "generate-report",  # commits the report inside the research worktree
        "implement-experiment",  # edits a newly created research worktree
        "troubleshoot-experiment",  # repairs the research worktree
        "make-campaign",  # publishes a recipe under .autoskillit/recipes/campaigns
        "merge-pr",  # resolves conflicts in a caller-owned integration worktree
        "retry-worktree",  # resumes edits in a caller-owned worktree
        "validate-audit",  # writes to an operator-supplied audit run directory
        "dry-walkthrough",  # updates the caller's plan path, which may be outside temp
    }
)

_WRITE_RESTRICTION_PATTERNS = (
    r"modify.*source",
    r"modify.*code",
    r"read.only.*anal",
    r"read.only audit",
    r"do not.*write",
    r"do not.*edit",
    r"do not.*modify",
    r"must never.*write",
    r"must never.*edit",
    r"must never.*modify",
    r"no.*write.*source",
    r"write.*outside.*autoskillit",
    r"modify any.*source",
    r"modify any.*code",
)

_ROOT = Path(__file__).resolve().parents[2]
_SKILLS_ROOT = _ROOT / "src" / "autoskillit" / "skills_extended"


def _has_write_restriction_prose(content: str) -> bool:
    never = extract_never_block(content).lower()
    return any(re.search(pattern, never) for pattern in _WRITE_RESTRICTION_PATTERNS)


def test_never_modify_source_skills_have_write_prefix() -> None:
    missing: list[str] = []
    for skill_path in sorted(_SKILLS_ROOT.glob("*/SKILL.md")):
        content = skill_path.read_text(encoding="utf-8")
        if not _has_write_restriction_prose(content):
            continue
        if skill_path.parent.name in UNRESTRICTED_WRITE_SKILLS:
            continue
        parsed = parse_frontmatter_content(content)
        if not parsed.is_valid or parsed.data is None or "write_paths" not in parsed.data:
            missing.append(skill_path.parent.name)
    assert not missing, f"write-restricted skills lack write_paths: {missing}"


def test_planner_skills_always_have_output_dir() -> None:
    recipe = load_yaml(_ROOT / "src" / "autoskillit" / "recipes" / "planner.yaml")
    missing = [
        name
        for name, step in recipe.get("steps", {}).items()
        if isinstance(step, dict)
        and step.get("tool") == "run_skill"
        and not (step.get("with") or {}).get("output_dir")
    ]
    assert not missing, f"planner run_skill steps missing output_dir: {missing}"


def test_skill_md_guard_claims_are_true() -> None:
    """Unqualified blocking claims must describe guards reachable in both classes."""
    claim_pattern = re.compile(
        r"\b(write guard|[a-z_]+_guard)\s+(?:blocks|denies|prevents)\b", re.I
    )
    assert claim_pattern.search("write guard blocks an out-of-scope write")
    violations: list[str] = []
    for skill_path in _SKILLS_ROOT.glob("*/SKILL.md"):
        content = skill_path.read_text(encoding="utf-8")
        for claim in claim_pattern.finditer(content):
            stem = claim.group(1).lower().replace(" ", "_")
            guards = [hook for hook in HOOK_REGISTRY if f"guards/{stem}.py" in hook.scripts]
            if not guards or not all(
                any(
                    hook_applies_to_backend(
                        hook, backend="claude_code", session_scope=session_class
                    )
                    for hook in guards
                )
                for session_class in ("headless", "interactive")
            ):
                violations.append(f"{skill_path.parent.name}: {claim.group(0)}")
    assert not violations, f"unreachable guard claims: {violations}"
