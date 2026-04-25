"""Tests for skill resolution hierarchy."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from autoskillit.core.types import SkillSource
from autoskillit.workspace.skills import (
    DefaultSkillResolver,
    bundled_skills_dir,
    bundled_skills_extended_dir,
)

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.small]

BUNDLED_SKILLS = [
    "analyze-prs",
    "arch-lens-c4-container",
    "arch-lens-concurrency",
    "arch-lens-data-lineage",
    "arch-lens-deployment",
    "arch-lens-development",
    "arch-lens-error-resilience",
    "arch-lens-module-dependency",
    "arch-lens-operational",
    "arch-lens-process-flow",
    "arch-lens-repository-access",
    "arch-lens-scenarios",
    "arch-lens-security",
    "arch-lens-state-lifecycle",
    "audit-arch",
    "audit-bugs",
    "audit-claims",
    "audit-cohesion",
    "audit-defense-standards",
    "audit-friction",
    "audit-impl",
    "audit-tests",
    "build-execution-map",
    "bundle-local-report",
    "close-kitchen",
    "collapse-issues",
    "design-guards",
    "diagnose-ci",
    "dry-walkthrough",
    "elaborate-phase",
    "enrich-issues",
    "exp-lens-benchmark-representativeness",
    "exp-lens-causal-assumptions",
    "exp-lens-comparator-construction",
    "exp-lens-error-budget",
    "exp-lens-estimand-clarity",
    "exp-lens-exploratory-confirmatory",
    "exp-lens-fair-comparison",
    "exp-lens-governance-risk",
    "exp-lens-iterative-learning",
    "exp-lens-measurement-validity",
    "exp-lens-pipeline-integrity",
    "exp-lens-randomization-blocking",
    "exp-lens-reproducibility-artifacts",
    "exp-lens-sensitivity-robustness",
    "exp-lens-severity-testing",
    "exp-lens-unit-interference",
    "exp-lens-validity-threats",
    "exp-lens-variance-stability",
    "implement-experiment",
    "implement-worktree",
    "implement-worktree-no-merge",
    "investigate",
    "issue-splitter",
    "make-arch-diag",
    "make-campaign",
    "make-experiment-diag",
    "make-groups",
    "make-plan",
    "make-req",
    "merge-pr",
    "mermaid",
    "migrate-recipes",
    "open-integration-pr",
    "open-kitchen",
    "open-pr",
    "open-research-pr",
    "pipeline-summary",
    "plan-experiment",
    "plan-visualization",
    "prepare-issue",
    "process-issues",
    "rectify",
    "report-bug",
    "resolve-claims-review",
    "resolve-design-review",
    "resolve-failures",
    "resolve-merge-conflicts",
    "resolve-research-review",
    "resolve-review",
    "retry-worktree",
    "review-pr",
    "review-approach",
    "review-design",
    "review-research-pr",
    "run-experiment",
    "scope",
    "setup-project",
    "smoke-task",
    "sous-chef",
    "sprint-planner",
    "stage-data",
    "triage-issues",
    "troubleshoot-experiment",
    "validate-audit",
    "verify-diag",
    "vis-lens-always-on",
    "vis-lens-antipattern",
    "vis-lens-caption-annot",
    "vis-lens-chart-select",
    "vis-lens-color-access",
    "vis-lens-domain-norms",
    "vis-lens-figure-table",
    "vis-lens-multi-compare",
    "vis-lens-reproducibility",
    "vis-lens-story-arc",
    "vis-lens-temporal",
    "vis-lens-uncertainty",
    "write-recipe",
    "generate-report",
]

# Internal-only skill documents: injected programmatically, never invocable as slash commands.
# They have no YAML frontmatter and do not follow the user-facing SKILL.md structural contract.
INTERNAL_SKILLS: frozenset[str] = frozenset({"sous-chef"})

ARCH_LENS_NAMES = [
    "arch-lens-c4-container",
    "arch-lens-process-flow",
    "arch-lens-data-lineage",
    "arch-lens-module-dependency",
    "arch-lens-concurrency",
    "arch-lens-error-resilience",
    "arch-lens-repository-access",
    "arch-lens-operational",
    "arch-lens-security",
    "arch-lens-development",
    "arch-lens-scenarios",
    "arch-lens-state-lifecycle",
    "arch-lens-deployment",
]

EXP_LENS_NAMES = [
    "exp-lens-estimand-clarity",
    "exp-lens-causal-assumptions",
    "exp-lens-comparator-construction",
    "exp-lens-pipeline-integrity",
    "exp-lens-variance-stability",
    "exp-lens-fair-comparison",
    "exp-lens-reproducibility-artifacts",
    "exp-lens-measurement-validity",
    "exp-lens-sensitivity-robustness",
    "exp-lens-benchmark-representativeness",
    "exp-lens-unit-interference",
    "exp-lens-error-budget",
    "exp-lens-severity-testing",
    "exp-lens-randomization-blocking",
    "exp-lens-validity-threats",
    "exp-lens-iterative-learning",
    "exp-lens-exploratory-confirmatory",
    "exp-lens-governance-risk",
]

AUDIT_SKILL_NAMES = [
    "audit-arch",
    "audit-tests",
    "audit-cohesion",
    "audit-defense-standards",
    "validate-audit",
]

BUNDLED_SKILL_NAMES = set(BUNDLED_SKILLS)


def _all_skill_roots() -> list[Path]:
    return [bundled_skills_dir(), bundled_skills_extended_dir()]


class TestSkillResolver:
    # SK1
    def test_bundled_skill_found(self) -> None:
        resolver = DefaultSkillResolver()
        info = resolver.resolve("open-kitchen")
        assert info is not None
        assert info.name == "open-kitchen"
        assert info.source == SkillSource.BUNDLED
        assert info.path.name == "SKILL.md"

    # SK6
    def test_unknown_skill_returns_none(self) -> None:
        resolver = DefaultSkillResolver()
        assert resolver.resolve("nonexistent") is None

    def test_no_hardcoded_username_mentions_in_skill_mds(self) -> None:
        """No SKILL.md may contain a hardcoded GitHub @-mention in any line.

        Includes code fences — all lines are checked.
        """
        # Negative lookbehind prevents matching email local-parts (e.g. noreply@anthropic.com)
        # and decorator-like patterns where @ follows alphanumeric or dot.
        mention_pattern = re.compile(r"(?<![a-zA-Z0-9.])@[A-Za-z][A-Za-z0-9_-]{2,}")
        # Known-safe @-tokens that are not GitHub usernames (e.g. template variables, org names
        # used in documentation context rather than as literal mentions).
        SAFE_TOKENS: frozenset[str] = frozenset({"@anthropic"})
        violations: list[str] = []

        for skills_dir in _all_skill_roots():
            for skill_md in sorted(skills_dir.rglob("SKILL.md")):
                skill_name = skill_md.parent.name
                for lineno, raw_line in enumerate(skill_md.read_text().splitlines(), start=1):
                    for match in mention_pattern.finditer(raw_line):
                        token = match.group()
                        if token in SAFE_TOKENS:
                            continue
                        violations.append(f"{skill_name}/SKILL.md:{lineno}: {token!r}")

        assert violations == [], (
            "Hardcoded GitHub @-mentions found in SKILL.md files. "
            "Use dynamic derivation (e.g., `gh api user -q .login`) instead:\n"
            + "\n".join(violations)
        )

    def test_list_all_returns_bundled_skills(self) -> None:
        """list_all returns all bundled skills from both skill directories."""
        resolver = DefaultSkillResolver()
        skills = resolver.list_all()
        names = {s.name for s in skills}
        assert "investigate" in names
        assert "make-plan" in names
        sources = {s.source for s in skills}
        assert sources == {SkillSource.BUNDLED, SkillSource.BUNDLED_EXTENDED}

    def test_skill_md_cross_references_are_namespaced(self) -> None:
        """All /skill-name references in SKILL.md files use /autoskillit: prefix."""
        for skills_dir in _all_skill_roots():
            for skill_md in skills_dir.rglob("SKILL.md"):
                content = skill_md.read_text()
                for match in re.finditer(r"(?<!\w)/([a-z][\w-]+)", content):
                    name = match.group(1)
                    if name.startswith("autoskillit:") or name.startswith("mcp__"):
                        continue
                    # Skip URI paths like workflow://some-recipe — not skill invocations
                    start = match.start()
                    if start >= 1 and content[start - 1] == "/":
                        continue
                    # Skip placeholder filesystem paths like {{AUTOSKILLIT_TEMP}}/skill-name/
                    if start >= 1 and content[start - 1] == "}":
                        continue
                    if name in BUNDLED_SKILL_NAMES:
                        skill_file = f"{skill_md.parent.name}/SKILL.md"
                        assert False, f"{skill_file}: /{name} should be /autoskillit:{name}"

    def test_skill_md_has_critical_constraints(self) -> None:
        """Every user-invocable SKILL.md must have Critical Constraints (NEVER/ALWAYS blocks)."""
        failures: list[str] = []
        for skills_dir in _all_skill_roots():
            for skill_md in skills_dir.rglob("SKILL.md"):
                skill_name = skill_md.parent.name
                if skill_name in INTERNAL_SKILLS:
                    continue
                content = skill_md.read_text()
                missing: list[str] = []
                if not re.search(r"^##\s+.*Critical Constraints", content, re.MULTILINE):
                    missing.append("## Critical Constraints heading")
                if "**NEVER:**" not in content:
                    missing.append("**NEVER:** block")
                if "**ALWAYS:**" not in content:
                    missing.append("**ALWAYS:** block")
                if missing:
                    failures.append(f"  {skill_name}: missing {', '.join(missing)}")
        assert not failures, "SKILL.md structural contract violated:\n" + "\n".join(failures)

    def test_file_producing_skills_have_output_guard(self) -> None:
        """File-producing skills must have a negative output constraint in NEVER block."""
        FILE_PRODUCING_SKILLS = {
            "build-execution-map": ".autoskillit/temp/build-execution-map/",
            "investigate": ".autoskillit/temp/investigate/",
            "make-groups": ".autoskillit/temp/make-groups/",
            "make-plan": ".autoskillit/temp/make-plan/",
            "write-recipe": ".autoskillit/recipes/",
            "rectify": ".autoskillit/temp/rectify/",
            "review-approach": ".autoskillit/temp/review-approach/",
            "setup-project": ".autoskillit/temp/setup-project/",
            "triage-issues": ".autoskillit/temp/triage-issues/",
        }
        bd_ext = bundled_skills_extended_dir()
        failures: list[str] = []
        for skill_name, output_dir in FILE_PRODUCING_SKILLS.items():
            skill_md = bd_ext / skill_name / "SKILL.md"
            content = skill_md.read_text()
            # Extract NEVER block: from **NEVER:** to the next ** or ## heading
            never_match = re.search(r"\*\*NEVER:\*\*(.*?)(?=\n\*\*|\n##)", content, re.DOTALL)
            if never_match is None:
                failures.append(f"  {skill_name}: no **NEVER:** block found")
                continue
            never_block = never_match.group(1).lower()
            if "create files outside" not in never_block:
                failures.append(
                    f"  {skill_name}: NEVER block missing "
                    f"'Create files outside' constraint for {output_dir}"
                )
        assert not failures, "File-producing skills missing output guard:\n" + "\n".join(failures)

    def test_skill_md_frontmatter_matches_directory(self) -> None:
        """SKILL.md frontmatter name: field must match its directory name."""
        failures: list[str] = []
        for skills_dir in _all_skill_roots():
            for skill_md in skills_dir.rglob("SKILL.md"):
                skill_name = skill_md.parent.name
                if skill_name in INTERNAL_SKILLS:
                    continue
                content = skill_md.read_text()
                # Parse YAML frontmatter between --- delimiters
                fm_match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
                if fm_match is None:
                    failures.append(f"  {skill_name}: no YAML frontmatter found")
                    continue
                data = yaml.safe_load(fm_match.group(1))
                if not isinstance(data, dict) or "name" not in data:
                    failures.append(f"  {skill_name}: frontmatter missing 'name' field")
                    continue
                if data["name"] != skill_name:
                    failures.append(
                        f"  {skill_name}: frontmatter name '{data['name']}' "
                        f"!= directory name '{skill_name}'"
                    )
        assert not failures, "SKILL.md frontmatter/directory mismatch:\n" + "\n".join(failures)

    def test_make_groups_skill_documents_per_group_output(self) -> None:
        """make-groups SKILL.md must document per-group file output for pipeline consumption."""
        skill_path = DefaultSkillResolver().resolve("make-groups").path
        content = skill_path.read_text()
        assert "per-group" in content.lower() or "groupA_" in content
        assert "manifest" in content.lower()

    def test_bundled_skills_list_matches_filesystem(self) -> None:
        """make-script-skill SKILL.md bundled skills list must match filesystem."""
        skill_md = DefaultSkillResolver().resolve("write-recipe").path
        content = skill_md.read_text()

        # Extract the bundled skills list section
        in_section = False
        skills_text = ""
        for line in content.splitlines():
            if "## Bundled AutoSkillit Skills" in line:
                in_section = True
                continue
            if in_section and line.startswith("## "):
                break
            if in_section:
                skills_text += line + "\n"

        # Parse comma-separated skill names from the section body
        # Skip lines that are empty or start with "These skills"
        listed_skills = sorted(
            name.strip()
            for line in skills_text.splitlines()
            if line.strip() and not line.strip().startswith("These skills")
            for name in line.split(",")
            if name.strip()
        )

        # Get actual filesystem skills
        actual_skills = sorted(s.name for s in DefaultSkillResolver().list_all())

        assert listed_skills == actual_skills, (
            f"make-script-skill bundled skills list doesn't match filesystem.\n"
            f"  Listed:  {listed_skills}\n"
            f"  On disk: {actual_skills}"
        )

    def test_diagnose_ci_skill_is_resolvable(self) -> None:
        """AP1: SkillResolver must find the diagnose-ci bundled skill."""
        resolver = DefaultSkillResolver()
        info = resolver.resolve("diagnose-ci")
        assert info is not None
        assert info.path.exists()

    def test_all_arch_lens_skills_bundled(self) -> None:
        """All 13 arch-lens skill variants must be resolvable via SkillResolver."""
        resolver = DefaultSkillResolver()
        for name in ARCH_LENS_NAMES:
            info = resolver.resolve(name)
            assert info is not None, f"arch-lens skill '{name}' not found in bundled skills"
            assert info.path.exists(), f"SKILL.md missing for '{name}' at {info.path}"

    def test_all_audit_skills_bundled(self) -> None:
        """Audit skills must be bundled and available for use in recipes."""
        resolver = DefaultSkillResolver()
        for name in AUDIT_SKILL_NAMES:
            info = resolver.resolve(name)
            assert info is not None, f"audit skill '{name}' not found in bundled skills"

    def test_review_pr_is_bundled(self) -> None:
        """review-pr must be in bundled skills."""
        resolver = DefaultSkillResolver()
        assert resolver.resolve("review-pr") is not None, "review-pr must be a bundled skill"

    # ── New tests for three-tier skill directory layout ────────────────────────

    def test_bundled_skills_extended_dir_path(self) -> None:
        """bundled_skills_extended_dir() returns pkg_root() / 'skills_extended'."""
        from autoskillit.core import pkg_root

        assert bundled_skills_extended_dir() == pkg_root() / "skills_extended"

    def test_skills_extended_dir_exists(self) -> None:
        """skills_extended/ directory is present in the installed package."""
        assert bundled_skills_extended_dir().is_dir()

    def test_tier1_only_in_skills_dir(self) -> None:
        """Only open-kitchen, close-kitchen, sous-chef remain in skills/."""
        names = {d.name for d in bundled_skills_dir().iterdir() if d.is_dir()}
        assert names == {"open-kitchen", "close-kitchen", "sous-chef"}

    def test_115_skills_in_skills_extended(self) -> None:
        """skills_extended/ contains exactly 115 SKILL.md-carrying directories."""
        skills = [
            d
            for d in bundled_skills_extended_dir().iterdir()
            if d.is_dir() and (d / "SKILL.md").is_file()
        ]
        assert len(skills) == 115

    def test_skill_resolver_list_all_total_count(self) -> None:
        """list_all() returns 117 public skills (2 Tier-1 + 115 extended)."""
        assert len(DefaultSkillResolver().list_all()) == 117

    def test_skill_resolver_resolve_extended_skill(self) -> None:
        """resolve() finds a skill living in skills_extended/ with BUNDLED_EXTENDED source."""
        result = DefaultSkillResolver().resolve("make-plan")
        assert result is not None
        assert result.source == SkillSource.BUNDLED_EXTENDED

    def test_skill_resolver_bundled_source_for_tier1(self) -> None:
        """Skills in skills/ carry SkillSource.BUNDLED."""
        result = DefaultSkillResolver().resolve("open-kitchen")
        assert result is not None
        assert result.source == SkillSource.BUNDLED

    def test_skill_source_bundled_extended_exists(self) -> None:
        """SkillSource.BUNDLED_EXTENDED enum member exists."""
        assert SkillSource.BUNDLED_EXTENDED == "bundled_extended"

    def test_list_all_no_cross_directory_name_collision(self) -> None:
        """No skill name may appear in both skills/ and skills_extended/.

        If a name collision exists, list_all() raises RuntimeError.
        This test verifies the current filesystem has no collisions.
        """
        resolver = DefaultSkillResolver()
        skills = resolver.list_all()
        names = [s.name for s in skills]
        dupes = {n for n in names if names.count(n) > 1}
        assert not dupes, (
            f"Skill name collision across skills/ and skills_extended/: {sorted(dupes)}"
        )


class TestSkillCategories:
    # T6 — read_skill_categories() and SkillInfo.categories

    def test_read_skill_categories_returns_frozenset_for_github_skill(self, tmp_path) -> None:
        from autoskillit.workspace.skills import read_skill_categories

        skill_md = tmp_path / "SKILL.md"
        skill_md.write_text("---\nname: open-pr\ncategories: [github]\n---\n# content")
        result = read_skill_categories(skill_md)
        assert result == frozenset({"github"})

    def test_read_skill_categories_returns_empty_when_no_categories_key(self, tmp_path) -> None:
        from autoskillit.workspace.skills import read_skill_categories

        skill_md = tmp_path / "SKILL.md"
        skill_md.write_text("---\nname: investigate\ndescription: foo\n---\n# content")
        result = read_skill_categories(skill_md)
        assert result == frozenset()

    def test_read_skill_categories_returns_empty_when_no_frontmatter(self, tmp_path) -> None:
        from autoskillit.workspace.skills import read_skill_categories

        skill_md = tmp_path / "SKILL.md"
        skill_md.write_text("# No frontmatter here")
        result = read_skill_categories(skill_md)
        assert result == frozenset()

    def test_read_skill_categories_multiple_categories(self, tmp_path) -> None:
        from autoskillit.workspace.skills import read_skill_categories

        skill_md = tmp_path / "SKILL.md"
        skill_md.write_text("---\nname: foo\ncategories: [github, audit]\n---\n# body")
        result = read_skill_categories(skill_md)
        assert result == frozenset({"github", "audit"})

    def test_skill_info_has_categories_field(self) -> None:
        from pathlib import Path

        from autoskillit.workspace.skills import SkillInfo

        info = SkillInfo(name="test", source=SkillSource.BUNDLED, path=Path("/fake/SKILL.md"))
        assert info.categories == frozenset()

    def test_compose_pr_skill_has_github_category(self) -> None:
        info = DefaultSkillResolver().resolve("compose-pr")
        assert info is not None
        assert "github" in info.categories

    def test_diagnose_ci_skill_has_ci_category(self) -> None:
        info = DefaultSkillResolver().resolve("diagnose-ci")
        assert info is not None
        assert "ci" in info.categories

    def test_all_arch_lens_skills_have_arch_lens_category(self) -> None:
        resolver = DefaultSkillResolver()
        for name in ARCH_LENS_NAMES:
            info = resolver.resolve(name)
            assert info is not None
            assert "arch-lens" in info.categories, f"{name} missing 'arch-lens' category"

    def test_make_arch_diag_has_arch_lens_category(self) -> None:
        info = DefaultSkillResolver().resolve("make-arch-diag")
        assert info is not None
        assert "arch-lens" in info.categories

    def test_verify_diag_has_arch_lens_category(self) -> None:
        info = DefaultSkillResolver().resolve("verify-diag")
        assert info is not None
        assert "arch-lens" in info.categories

    def test_all_audit_skills_have_audit_category(self) -> None:
        resolver = DefaultSkillResolver()
        for name in [
            "audit-arch",
            "audit-cohesion",
            "audit-tests",
            "audit-defense-standards",
            "audit-bugs",
            "audit-friction",
            "audit-impl",
            "validate-audit",
        ]:
            info = resolver.resolve(name)
            assert info is not None
            assert "audit" in info.categories, f"{name} missing 'audit' category"

    def test_uncategorized_skills_have_empty_categories(self) -> None:
        info = DefaultSkillResolver().resolve("investigate")
        assert info is not None
        assert info.categories == frozenset()

    def test_all_exp_lens_skills_bundled(self) -> None:
        """All 18 exp-lens skill variants must be resolvable via SkillResolver."""
        resolver = DefaultSkillResolver()
        for name in EXP_LENS_NAMES:
            info = resolver.resolve(name)
            assert info is not None, f"exp-lens skill '{name}' not found in bundled skills"
            assert info.path.exists(), f"SKILL.md missing for '{name}' at {info.path}"

    def test_all_exp_lens_skills_have_exp_lens_category(self) -> None:
        resolver = DefaultSkillResolver()
        for name in EXP_LENS_NAMES:
            info = resolver.resolve(name)
            assert info is not None
            assert "exp-lens" in info.categories, f"{name} missing 'exp-lens' category"

    def test_make_experiment_diag_has_exp_lens_category(self) -> None:
        info = DefaultSkillResolver().resolve("make-experiment-diag")
        assert info is not None
        assert "exp-lens" in info.categories

    def test_make_campaign_has_franchise_category(self) -> None:
        """make-campaign must declare both orchestration-family and franchise categories."""
        info = DefaultSkillResolver().resolve("make-campaign")
        assert info is not None
        assert "franchise" in info.categories, "make-campaign missing 'franchise' category"
        assert "orchestration-family" in info.categories, (
            "make-campaign must retain 'orchestration-family' category"
        )

    def test_planner_analyze_has_planner_category(self) -> None:
        info = DefaultSkillResolver().resolve("planner-analyze")
        assert info is not None
        assert "planner" in info.categories

    def test_planner_extract_domain_has_planner_category(self) -> None:
        info = DefaultSkillResolver().resolve("planner-extract-domain")
        assert info is not None
        assert "planner" in info.categories

    def test_planner_generate_phases_has_planner_category(self) -> None:
        info = DefaultSkillResolver().resolve("planner-generate-phases")
        assert info is not None
        assert "planner" in info.categories

    def test_planner_elaborate_phase_has_planner_category(self) -> None:
        info = DefaultSkillResolver().resolve("planner-elaborate-phase")
        assert info is not None
        assert "planner" in info.categories

    def test_planner_elaborate_assignment_has_planner_category(self) -> None:
        info = DefaultSkillResolver().resolve("planner-elaborate-assignment")
        assert info is not None
        assert "planner" in info.categories

    def test_planner_elaborate_wp_has_planner_category(self) -> None:
        info = DefaultSkillResolver().resolve("planner-elaborate-wp")
        assert info is not None
        assert "planner" in info.categories


RESEARCH_SKILL_NAMES = {
    "scope",
    "plan-experiment",
    "implement-experiment",
    "run-experiment",
    "generate-report",
    "review-research-pr",
    "prepare-research-pr",
    "compose-research-pr",
    "review-design",
    "resolve-design-review",
    "resolve-research-review",
    "troubleshoot-experiment",
    "audit-claims",
    "resolve-claims-review",
}


def test_research_skills_all_discoverable():
    names = {s.name for s in DefaultSkillResolver().list_all()}
    assert RESEARCH_SKILL_NAMES.issubset(names)


def test_research_skills_have_research_category():
    resolver = DefaultSkillResolver()
    for name in RESEARCH_SKILL_NAMES:
        info = resolver.resolve(name)
        assert info is not None, f"Skill {name!r} not found"
        assert "research" in info.categories, (
            f"Skill {name!r} missing 'research' category; got {info.categories}"
        )


def test_all_extended_skills_have_tier_assignment():
    """Every skill in skills_extended/ must be assigned to tier2 or tier3 in defaults.yaml."""
    from autoskillit.config import load_config

    config = load_config()
    all_tiers = set(config.skills.tier1) | set(config.skills.tier2) | set(config.skills.tier3)
    resolver = DefaultSkillResolver()
    extended = {s.name for s in resolver.list_all() if s.source == SkillSource.BUNDLED_EXTENDED}
    unassigned = extended - all_tiers
    assert not unassigned, f"Skills missing tier assignment: {sorted(unassigned)}"


def test_activate_deps_are_resolvable():
    """Every activate_deps entry resolves to a known pack or known skill."""
    from autoskillit.core import PACK_REGISTRY
    from autoskillit.workspace.session_skills import _parse_activate_deps

    resolver = DefaultSkillResolver()
    all_names = {s.name for s in resolver.list_all()}
    for skill_info in resolver.list_all():
        content = skill_info.path.read_text()
        deps = _parse_activate_deps(content)
        for dep in deps:
            assert dep in PACK_REGISTRY or dep in all_names, (
                f"Skill {skill_info.name!r} has unresolvable activate_dep: {dep!r}"
            )


def test_audit_claims_and_resolve_claims_review_in_tier3() -> None:
    from autoskillit.config import load_config

    config = load_config()
    assert "audit-claims" in config.skills.tier3
    assert "resolve-claims-review" in config.skills.tier3


def test_audit_claims_skill_md_exists() -> None:
    resolver = DefaultSkillResolver()
    info = resolver.resolve("audit-claims")
    assert info is not None, "audit-claims skill not found"
    assert info.path.exists(), f"SKILL.md missing at {info.path}"


def test_resolve_claims_review_skill_md_exists() -> None:
    resolver = DefaultSkillResolver()
    info = resolver.resolve("resolve-claims-review")
    assert info is not None, "resolve-claims-review skill not found"
    assert info.path.exists(), f"SKILL.md missing at {info.path}"
