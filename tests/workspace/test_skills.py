"""Tests for skill resolution hierarchy."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from autoskillit.core.types import SkillSource
from autoskillit.workspace.skills import (
    SkillResolver,
    bundled_skills_dir,
    bundled_skills_extended_dir,
)

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
    "audit-cohesion",
    "audit-defense-standards",
    "audit-friction",
    "audit-impl",
    "audit-tests",
    "close-kitchen",
    "collapse-issues",
    "design-guards",
    "diagnose-ci",
    "dry-walkthrough",
    "elaborate-phase",
    "enrich-issues",
    "implement-worktree",
    "implement-worktree-no-merge",
    "investigate",
    "issue-splitter",
    "make-arch-diag",
    "make-groups",
    "make-plan",
    "make-req",
    "merge-pr",
    "mermaid",
    "migrate-recipes",
    "open-integration-pr",
    "open-kitchen",
    "open-pr",
    "pipeline-summary",
    "prepare-issue",
    "process-issues",
    "rectify",
    "report-bug",
    "resolve-failures",
    "resolve-merge-conflicts",
    "resolve-review",
    "retry-worktree",
    "review-pr",
    "review-approach",
    "setup-project",
    "smoke-task",
    "sous-chef",
    "sprint-planner",
    "triage-issues",
    "verify-diag",
    "write-recipe",
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

AUDIT_SKILL_NAMES = [
    "audit-arch",
    "audit-tests",
    "audit-cohesion",
    "audit-defense-standards",
]

BUNDLED_SKILL_NAMES = set(BUNDLED_SKILLS)


def _all_skill_roots() -> list[Path]:
    return [bundled_skills_dir(), bundled_skills_extended_dir()]


class TestSkillResolver:
    # SK1
    def test_bundled_skill_found(self) -> None:
        resolver = SkillResolver()
        info = resolver.resolve("open-kitchen")
        assert info is not None
        assert info.name == "open-kitchen"
        assert info.source == SkillSource.BUNDLED
        assert info.path.name == "SKILL.md"

    # SK6
    def test_unknown_skill_returns_none(self) -> None:
        resolver = SkillResolver()
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
        resolver = SkillResolver()
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
                    if name in BUNDLED_SKILL_NAMES:
                        skill_file = f"{skill_md.parent.name}/SKILL.md"
                        assert False, f"{skill_file}: /{name} should be /autoskillit:{name}"

    def test_skill_md_yaml_examples_are_valid_workflows(self) -> None:
        """YAML workflow examples embedded in SKILL.md files must pass validation."""
        import yaml as _yaml

        from autoskillit.recipe.io import (
            _parse_recipe as _parse_workflow,
        )
        from autoskillit.recipe.validator import (
            validate_recipe as validate_workflow,
        )

        yaml_block_re = re.compile(r"```yaml\n(.*?)```", re.DOTALL)

        for skills_dir in _all_skill_roots():
            for skill_md in skills_dir.rglob("SKILL.md"):
                content = skill_md.read_text()
                for match in yaml_block_re.finditer(content):
                    block = match.group(1)
                    # Only validate blocks that look like full workflow definitions
                    if "steps:" not in block or "name:" not in block:
                        continue
                    # Skip format templates that use {placeholder} syntax
                    if "{script-name}" in block or "{mcp_tool_name}" in block:
                        continue
                    data = _yaml.safe_load(block)
                    if not isinstance(data, dict) or "steps" not in data:
                        continue
                    wf = _parse_workflow(data)
                    errors = [e for e in validate_workflow(wf) if "kitchen_rules" not in e.lower()]
                    assert not errors, (
                        f"{skill_md.parent.name}/SKILL.md has invalid YAML example:\n  {errors}"
                    )

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
        skill_path = SkillResolver().resolve("make-groups").path
        content = skill_path.read_text()
        assert "per-group" in content.lower() or "groupA_" in content
        assert "manifest" in content.lower()

    def test_bundled_skills_list_matches_filesystem(self) -> None:
        """make-script-skill SKILL.md bundled skills list must match filesystem."""
        skill_md = SkillResolver().resolve("write-recipe").path
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
        actual_skills = sorted(s.name for s in SkillResolver().list_all())

        assert listed_skills == actual_skills, (
            f"make-script-skill bundled skills list doesn't match filesystem.\n"
            f"  Listed:  {listed_skills}\n"
            f"  On disk: {actual_skills}"
        )

    def test_pipeline_summary_skill_exists(self) -> None:
        """pipeline-summary must be in the bundled skills list."""
        resolver = SkillResolver()
        all_names = {s.name for s in resolver.list_all()}
        assert "pipeline-summary" in all_names

    def test_internal_skills_excluded_from_list_all(self) -> None:
        """sous-chef must NOT appear in list_all (internal-only skill)."""
        resolver = SkillResolver()
        all_names = {s.name for s in resolver.list_all()}
        assert "sous-chef" not in all_names

    def test_list_all_returns_user_invocable_skills_only(self) -> None:
        """list_all returns bundled skills minus internal skills."""
        resolver = SkillResolver()
        all_names = {s.name for s in resolver.list_all()}
        expected = set(BUNDLED_SKILLS) - INTERNAL_SKILLS
        assert all_names == expected

    def test_diagnose_ci_skill_is_resolvable(self) -> None:
        """AP1: SkillResolver must find the diagnose-ci bundled skill."""
        resolver = SkillResolver()
        info = resolver.resolve("diagnose-ci")
        assert info is not None
        assert info.path.exists()

    def test_all_arch_lens_skills_bundled(self) -> None:
        """All 13 arch-lens skill variants must be resolvable via SkillResolver."""
        resolver = SkillResolver()
        for name in ARCH_LENS_NAMES:
            info = resolver.resolve(name)
            assert info is not None, f"arch-lens skill '{name}' not found in bundled skills"
            assert info.path.exists(), f"SKILL.md missing for '{name}' at {info.path}"

    def test_all_audit_skills_bundled(self) -> None:
        """Audit skills must be bundled and available for use in recipes."""
        resolver = SkillResolver()
        for name in AUDIT_SKILL_NAMES:
            info = resolver.resolve(name)
            assert info is not None, f"audit skill '{name}' not found in bundled skills"

    def test_review_pr_is_bundled(self) -> None:
        """review-pr must be in bundled skills."""
        resolver = SkillResolver()
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

    def test_57_skills_in_skills_extended(self) -> None:
        """skills_extended/ contains exactly 57 SKILL.md-carrying directories."""
        skills = [
            d
            for d in bundled_skills_extended_dir().iterdir()
            if d.is_dir() and (d / "SKILL.md").is_file()
        ]
        assert len(skills) == 57

    def test_skill_resolver_list_all_total_count(self) -> None:
        """list_all() returns 59 public skills (2 Tier-1 + 57 extended)."""
        assert len(SkillResolver().list_all()) == 59

    def test_skill_resolver_resolve_extended_skill(self) -> None:
        """resolve() finds a skill living in skills_extended/ with BUNDLED_EXTENDED source."""
        result = SkillResolver().resolve("make-plan")
        assert result is not None
        assert result.source == SkillSource.BUNDLED_EXTENDED

    def test_skill_resolver_bundled_source_for_tier1(self) -> None:
        """Skills in skills/ carry SkillSource.BUNDLED."""
        result = SkillResolver().resolve("open-kitchen")
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
        resolver = SkillResolver()
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

    def test_open_pr_skill_has_github_category(self) -> None:
        info = SkillResolver().resolve("open-pr")
        assert info is not None
        assert "github" in info.categories

    def test_diagnose_ci_skill_has_ci_category(self) -> None:
        info = SkillResolver().resolve("diagnose-ci")
        assert info is not None
        assert "ci" in info.categories

    def test_all_arch_lens_skills_have_arch_lens_category(self) -> None:
        resolver = SkillResolver()
        for name in ARCH_LENS_NAMES:
            info = resolver.resolve(name)
            assert info is not None
            assert "arch-lens" in info.categories, f"{name} missing 'arch-lens' category"

    def test_make_arch_diag_has_arch_lens_category(self) -> None:
        info = SkillResolver().resolve("make-arch-diag")
        assert info is not None
        assert "arch-lens" in info.categories

    def test_verify_diag_has_arch_lens_category(self) -> None:
        info = SkillResolver().resolve("verify-diag")
        assert info is not None
        assert "arch-lens" in info.categories

    def test_all_audit_skills_have_audit_category(self) -> None:
        resolver = SkillResolver()
        for name in [
            "audit-arch",
            "audit-cohesion",
            "audit-tests",
            "audit-defense-standards",
            "audit-bugs",
            "audit-friction",
            "audit-impl",
        ]:
            info = resolver.resolve(name)
            assert info is not None
            assert "audit" in info.categories, f"{name} missing 'audit' category"

    def test_uncategorized_skills_have_empty_categories(self) -> None:
        info = SkillResolver().resolve("investigate")
        assert info is not None
        assert info.categories == frozenset()
