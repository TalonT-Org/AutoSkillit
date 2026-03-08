"""Tests for skill resolution hierarchy."""

from __future__ import annotations

import re

import yaml

from autoskillit.core.types import SkillSource
from autoskillit.workspace.skills import SkillResolver, bundled_skills_dir

BUNDLED_SKILLS = [
    "analyze-prs",
    "audit-friction",
    "audit-impl",
    "collapse-issues",
    "create-review-pr",
    "dry-walkthrough",
    "enrich-issues",
    "implement-worktree",
    "implement-worktree-no-merge",
    "investigate",
    "issue-splitter",
    "make-groups",
    "make-plan",
    "merge-pr",
    "write-recipe",
    "mermaid",
    "migrate-recipes",
    "open-pr",
    "pipeline-summary",
    "prepare-issue",
    "process-issues",
    "rectify",
    "report-bug",
    "resolve-failures",
    "resolve-review",
    "retry-worktree",
    "review-approach",
    "review-pr",
    "setup-project",
    "smoke-task",
    "sous-chef",
    "triage-issues",
]

# Internal-only skill documents: injected programmatically, never invocable as slash commands.
# They have no YAML frontmatter and do not follow the user-facing SKILL.md structural contract.
INTERNAL_SKILLS: frozenset[str] = frozenset({"sous-chef"})

BUNDLED_SKILL_NAMES = set(BUNDLED_SKILLS)


class TestSkillResolver:
    # SK1
    def test_bundled_skill_found(self) -> None:
        resolver = SkillResolver()
        info = resolver.resolve("investigate")
        assert info is not None
        assert info.name == "investigate"
        assert info.source == SkillSource.BUNDLED
        assert info.path.name == "SKILL.md"

    # SK6
    def test_unknown_skill_returns_none(self) -> None:
        resolver = SkillResolver()
        assert resolver.resolve("nonexistent") is None

    # SK8
    def test_bundled_skills_match_filesystem(self) -> None:
        """BUNDLED_SKILLS list must exactly match what's on the filesystem."""
        bd = bundled_skills_dir()
        actual = sorted(d.name for d in bd.iterdir() if d.is_dir() and (d / "SKILL.md").is_file())
        assert actual == sorted(BUNDLED_SKILLS), (
            f"BUNDLED_SKILLS out of sync.\n"
            f"  On disk: {actual}\n"
            f"  In test: {sorted(BUNDLED_SKILLS)}"
        )

    def test_no_hardcoded_username_mentions_in_skill_mds(self) -> None:
        """No SKILL.md may contain a hardcoded GitHub @-mention in any line (including code fences)."""
        # Negative lookbehind prevents matching email local-parts (e.g. noreply@anthropic.com)
        # and decorator-like patterns where @ follows alphanumeric or dot.
        mention_pattern = re.compile(r"(?<![a-zA-Z0-9.])@[A-Za-z][A-Za-z0-9_-]{2,}")
        # Known-safe @-tokens that are not GitHub usernames (e.g. template variables, org names
        # used in documentation context rather than as literal mentions).
        SAFE_TOKENS: frozenset[str] = frozenset({"@anthropic"})
        violations: list[str] = []

        skills_dir = bundled_skills_dir()
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
        """list_all returns all bundled skills with source='bundled'."""
        resolver = SkillResolver()
        skills = resolver.list_all()
        names = {s.name for s in skills}
        assert "investigate" in names
        assert "make-plan" in names
        sources = {s.source for s in skills}
        assert sources == {SkillSource.BUNDLED}

    def test_skill_md_cross_references_are_namespaced(self) -> None:
        """All /skill-name references in SKILL.md files use /autoskillit: prefix."""
        skills_dir = bundled_skills_dir()
        for skill_md in skills_dir.rglob("SKILL.md"):
            content = skill_md.read_text()
            for match in re.finditer(r"(?<!\w)/([a-z][\w-]+)", content):
                name = match.group(1)
                if name.startswith("autoskillit:") or name.startswith("mcp__"):
                    continue
                # Skip URI paths like workflow://bugfix-loop — not skill invocations
                start = match.start()
                if start >= 1 and content[start - 1] == "/":
                    continue
                if name in BUNDLED_SKILL_NAMES:
                    assert False, (
                        f"{skill_md.parent.name}/SKILL.md: /{name} should be /autoskillit:{name}"
                    )

    def test_skill_md_yaml_examples_are_valid_workflows(self) -> None:
        """YAML workflow examples embedded in SKILL.md files must pass validation."""
        import yaml as _yaml

        from autoskillit.recipe.io import (
            _parse_recipe as _parse_workflow,
        )
        from autoskillit.recipe.validator import (
            validate_recipe as validate_workflow,
        )

        skills_dir = bundled_skills_dir()
        yaml_block_re = re.compile(r"```yaml\n(.*?)```", re.DOTALL)

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
        bd = bundled_skills_dir()
        failures: list[str] = []
        for skill_md in bd.rglob("SKILL.md"):
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
            "investigate": "temp/investigate/",
            "make-groups": "temp/make-groups/",
            "make-plan": "temp/make-plan/",
            "write-recipe": ".autoskillit/recipes/",
            "rectify": "temp/rectify/",
            "review-approach": "temp/review-approach/",
            "setup-project": "temp/setup-project/",
            "triage-issues": "temp/triage-issues/",
        }
        bd = bundled_skills_dir()
        failures: list[str] = []
        for skill_name, output_dir in FILE_PRODUCING_SKILLS.items():
            skill_md = bd / skill_name / "SKILL.md"
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
        bd = bundled_skills_dir()
        failures: list[str] = []
        for skill_md in bd.rglob("SKILL.md"):
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
        skill_path = bundled_skills_dir() / "make-groups" / "SKILL.md"
        content = skill_path.read_text()
        assert "per-group" in content.lower() or "groupA_" in content
        assert "manifest" in content.lower()

    def test_bundled_skills_list_matches_filesystem(self) -> None:
        """make-script-skill SKILL.md bundled skills list must match filesystem."""
        skill_md = bundled_skills_dir() / "write-recipe" / "SKILL.md"
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
        bd = bundled_skills_dir()
        actual_skills = sorted(
            d.name for d in bd.iterdir() if d.is_dir() and (d / "SKILL.md").is_file()
        )

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
