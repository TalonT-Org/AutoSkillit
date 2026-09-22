"""Skills that restrict writes in prose must declare the runtime boundary."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import autoskillit.hooks  # noqa: F401  (populates the deferred registry)
from autoskillit.core import load_yaml
from autoskillit.hook_registry import HOOK_REGISTRY, hook_applies_to_backend
from autoskillit.recipe._skill_placeholder_parser import extract_never_block
from autoskillit.workspace.skills._format import (
    SkillFrontmatterParseResult,
    parse_frontmatter_content,
    validate_skill_frontmatter,
)

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


def _collect_write_paths_violations(
    parsed: SkillFrontmatterParseResult,
    skill_name: str,
) -> list[str]:
    """Return human-readable violations for write_paths under a never-modify claim.

    Skills whose NEVER-block prose restricts writes must declare a non-empty
    write_paths list intersecting the restricted prefix surface. The presence,
    non-null, and non-empty-list checks are the test's tightening over what
    production accepts (which silently permits absent/null/empty write_paths);
    the per-entry shape and prefix checks delegate to validate_skill_frontmatter
    to keep this helper's surface aligned with the runtime contract.
    """
    if not parsed.is_valid or parsed.data is None:
        return [f"{skill_name}: frontmatter is not parseable or missing mapping"]
    if "write_paths" not in parsed.data:
        return [f"{skill_name}: 'write_paths' key missing from frontmatter"]
    write_paths = parsed.data["write_paths"]
    if write_paths is None:
        return [f"{skill_name}: 'write_paths' must be declared (got null)"]
    if not isinstance(write_paths, list):
        return [f"{skill_name}: 'write_paths' must be a list (got {type(write_paths).__name__})"]
    if len(write_paths) == 0:
        return [f"{skill_name}: 'write_paths' must be a non-empty list"]
    full_errors = validate_skill_frontmatter(parsed.data, skill_name)
    return [f"{skill_name}: {err}" for err in full_errors if "write_paths" in err]


def test_never_modify_source_skills_have_write_prefix() -> None:
    violations: list[str] = []
    for skill_path in sorted(_SKILLS_ROOT.glob("*/SKILL.md")):
        content = skill_path.read_text(encoding="utf-8")
        if not _has_write_restriction_prose(content):
            continue
        if skill_path.parent.name in UNRESTRICTED_WRITE_SKILLS:
            continue
        parsed = parse_frontmatter_content(content)
        violations.extend(_collect_write_paths_violations(parsed, skill_path.parent.name))
    assert not violations, (
        "write-restricted skills have invalid write_paths:\n  - " + "\n  - ".join(violations)
    )


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


@pytest.mark.parametrize(
    ("frontmatter_text", "expected_violation_count", "expected_substring"),
    [
        pytest.param(
            "---\nname: foo\ndescription: bar\n---\nbody\n",
            1,
            "missing",
            id="no_write_paths_key",
        ),
        pytest.param(
            "---\nname: foo\ndescription: bar\nwrite_paths: null\n---\nbody\n",
            1,
            "must be declared",
            id="write_paths_null",
        ),
        pytest.param(
            "---\nname: foo\ndescription: bar\nwrite_paths: 123\n---\nbody\n",
            1,
            "must be a list",
            id="write_paths_not_list",
        ),
        pytest.param(
            "---\nname: foo\ndescription: bar\nwrite_paths: []\n---\nbody\n",
            1,
            "non-empty",
            id="write_paths_empty_list",
        ),
        pytest.param(
            "---\nname: foo\ndescription: bar\nwrite_paths:\n- 123\n---\nbody\n",
            1,
            "must be a non-empty string",
            id="write_paths_non_string_entry",
        ),
        pytest.param(
            "---\nname: foo\ndescription: bar\nwrite_paths:\n- ''\n---\nbody\n",
            1,
            "must be a non-empty string",
            id="write_paths_empty_string_entry",
        ),
        pytest.param(
            "---\nname: foo\ndescription: bar\nwrite_paths:\n- /tmp/foo\n---\nbody\n",
            1,
            "must start with",
            id="write_paths_wrong_prefix",
        ),
        pytest.param(
            "---\nname: foo\ndescription: bar\nwrite_paths:\n"
            "- '{{AUTOSKILLIT_TEMP}}/foo/'\n---\nbody\n",
            0,
            "",
            id="write_paths_valid_placeholder",
        ),
        pytest.param(
            "---\nname: foo\ndescription: bar\nwrite_paths:\n"
            "- .autoskillit/temp/foo/\n---\nbody\n",
            0,
            "",
            id="write_paths_valid_resolved",
        ),
        pytest.param(
            "no frontmatter at all\n",
            1,
            "not parseable",
            id="write_paths_missing_opening_delimiter",
        ),
        pytest.param(
            "---\nname: foo\ndescription: bar\n",
            1,
            "not parseable",
            id="write_paths_missing_closing_delimiter",
        ),
        pytest.param(
            "---\n- a\n- b\n---\n",
            1,
            "not parseable",
            id="write_paths_non_mapping_root",
        ),
        pytest.param(
            "---\nname: [bad yaml\n---\n",
            1,
            "not parseable",
            id="write_paths_malformed_yaml_within_delimiters",
        ),
    ],
)
def test_collect_write_paths_violations(
    frontmatter_text: str,
    expected_violation_count: int,
    expected_substring: str,
) -> None:
    parsed = parse_frontmatter_content(frontmatter_text)
    result = _collect_write_paths_violations(parsed, "foo")
    assert len(result) == expected_violation_count, (
        f"frontmatter={frontmatter_text!r}\n"
        f"expected {expected_violation_count} violations, "
        f"got {len(result)}: {result}"
    )
    if expected_substring:
        assert all(expected_substring in violation for violation in result), (
            f"frontmatter={frontmatter_text!r}\n"
            f"expected substring {expected_substring!r} in every violation, "
            f"got: {result}"
        )
