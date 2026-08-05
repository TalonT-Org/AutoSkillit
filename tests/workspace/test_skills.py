"""Structural guard: skill resolution hierarchy and @-mention enforcement."""

from __future__ import annotations

import functools
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

import autoskillit.workspace.skills as _skills_mod
from autoskillit.core.io import load_yaml
from autoskillit.core.types import (
    SkillContractError,
    SkillExecutionRole,
    SkillSource,
    SkillSourceRef,
    SkillVisibilitySpec,
)
from autoskillit.workspace.skills import (
    DefaultSkillResolver,
    bundled_skills_dir,
    bundled_skills_extended_dir,
)

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.small]

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
    "validate-test-audit",
    "validate-review-decisions",
    "audit-docs",
    "audit-review-decisions",
]


@functools.lru_cache(maxsize=1)
def _get_bundled_skill_names() -> frozenset[str]:
    """Return the set of all bundled skill names (public + internal).

    Lazy and cached to defer and isolate initialization failures — a broken
    DefaultSkillResolver will only affect tests that call this function, not
    the entire module at import time.
    """
    return frozenset({s.name for s in DefaultSkillResolver().list_all()} | INTERNAL_SKILLS)


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
        """No SKILL.md may contain a hardcoded GitHub @-mention in prose."""
        # Negative lookbehind prevents matching email local-parts (e.g. noreply@anthropic.com).
        mention_pattern = re.compile(r"(?<![a-zA-Z0-9.])@[A-Za-z][A-Za-z0-9_-]{2,}")
        # Known-safe @-tokens that are not GitHub usernames (e.g. template variables, org names
        # used in documentation context rather than as literal mentions).
        SAFE_TOKENS: frozenset[str] = frozenset({"@anthropic"})
        violations: list[str] = []

        for skills_dir in _all_skill_roots():
            for skill_md in sorted(skills_dir.rglob("SKILL.md")):
                skill_name = skill_md.parent.name
                content = skill_md.read_text()
                in_fence = False
                for lineno, raw_line in enumerate(content.splitlines(), start=1):
                    stripped = raw_line.strip()
                    if stripped.startswith("```"):
                        in_fence = not in_fence
                        continue
                    if in_fence:
                        continue
                    # Strip inline code before matching
                    prose_line = re.sub(r"`[^`]*`", "", raw_line)
                    for match in mention_pattern.finditer(prose_line):
                        token = match.group()
                        if token in SAFE_TOKENS:
                            continue
                        violations.append(f"{skill_name}/SKILL.md:{lineno}: {token!r}")

        assert violations == [], (
            "Hardcoded GitHub @-mentions found in SKILL.md prose. "
            "Use dynamic derivation (e.g., `gh api user -q .login`) instead:\n"
            + "\n".join(violations)
        )

    def test_mention_guard_ignores_python_decorators_in_code_fences(self, tmp_path: Path) -> None:
        """Python decorators inside code fences must not trigger the @-mention guard."""
        from tests._helpers import strip_markdown_code_regions

        mention_pattern = re.compile(r"(?<![a-zA-Z0-9.])@[A-Za-z][A-Za-z0-9_-]{2,}")
        SAFE_TOKENS: frozenset[str] = frozenset({"@anthropic"})

        skill_md = tmp_path / "SKILL.md"
        skill_md.write_text(
            "---\nname: test-skill\n---\n"
            "# Test Skill\n\n"
            "```python\n"
            "@dataclass\n"
            "class Foo:\n"
            "    pass\n"
            "\n"
            "@pytest.mark.parametrize('x', [1, 2])\n"
            "def test_bar(x):\n"
            "    assert x > 0\n"
            "```\n"
            "\n"
            "Use `@mcp.tool()` for registration.\n"
        )

        prose = strip_markdown_code_regions(skill_md.read_text())
        violations = [
            match.group()
            for line in prose.splitlines()
            for match in mention_pattern.finditer(line)
            if match.group() not in SAFE_TOKENS
        ]
        assert violations == [], f"False positives on code-zone content: {violations}"

    def test_mention_guard_catches_prose_at_mention(self, tmp_path: Path) -> None:
        """A GitHub @-mention in prose (not code) must be caught by the guard."""
        from tests._helpers import strip_markdown_code_regions

        mention_pattern = re.compile(r"(?<![a-zA-Z0-9.])@[A-Za-z][A-Za-z0-9_-]{2,}")
        SAFE_TOKENS: frozenset[str] = frozenset({"@anthropic"})

        skill_md = tmp_path / "SKILL.md"
        skill_md.write_text("---\nname: test-skill\n---\n# Test\n\nContact @SomeUser for help.\n")

        prose = strip_markdown_code_regions(skill_md.read_text())
        violations = [
            match.group()
            for line in prose.splitlines()
            for match in mention_pattern.finditer(line)
            if match.group() not in SAFE_TOKENS
        ]
        assert "@SomeUser" in violations, (
            f"Guard failed to catch prose @-mention; got {violations}"
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
                    if name in _get_bundled_skill_names():
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
                data = load_yaml(fm_match.group(1))
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
        """write-recipe SKILL.md bundled skills list must match filesystem."""
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
            f"write-recipe bundled skills list doesn't match filesystem.\n"
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

    def test_skills_in_skills_extended(self) -> None:
        """skills_extended/ contains at least 125 SKILL.md-carrying directories."""
        skills = [
            d
            for d in bundled_skills_extended_dir().iterdir()
            if d.is_dir() and (d / "SKILL.md").is_file()
        ]
        assert len(skills) >= 125

    def test_skill_resolver_list_all_minimum_count(self) -> None:
        """list_all() returns at least 128 public skills (2 Tier-1 + extended)."""
        assert len(DefaultSkillResolver().list_all()) >= 128

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

    def test_list_all_excludes_retired_skill_names(self) -> None:
        """list_all() must not surface any skill whose name is in RETIRED_SKILL_NAMES."""
        from autoskillit.core import RETIRED_SKILL_NAMES

        names = {s.name for s in DefaultSkillResolver().list_all()}
        surfaced = RETIRED_SKILL_NAMES & names
        assert not surfaced, f"list_all() returned retired skill name(s): {sorted(surfaced)}"

    def test_bundled_skill_names_covers_filesystem(self) -> None:
        """BUNDLED_SKILL_NAMES must cover every skill directory with a SKILL.md."""
        from autoskillit.core import RETIRED_SKILL_NAMES

        filesystem_names = {
            entry.name
            for root in _all_skill_roots()
            for entry in root.iterdir()
            if entry.is_dir()
            and (entry / "SKILL.md").is_file()
            and entry.name not in RETIRED_SKILL_NAMES
            and entry.name not in INTERNAL_SKILLS
        }
        assert filesystem_names == _get_bundled_skill_names() - INTERNAL_SKILLS


class TestSkillCategories:
    # T6 — structured frontmatter categories and SkillInfo.categories

    def test_read_skill_categories_returns_frozenset_for_github_skill(self, tmp_path) -> None:
        from autoskillit.workspace.skill_format import read_skill_frontmatter

        skill_md = tmp_path / "SKILL.md"
        skill_md.write_text("---\nname: open-pr\ncategories: [github]\n---\n# content")
        result = frozenset((read_skill_frontmatter(skill_md).data or {}).get("categories", ()))
        assert result == frozenset({"github"})

    def test_read_skill_categories_returns_empty_when_no_categories_key(self, tmp_path) -> None:
        from autoskillit.workspace.skill_format import read_skill_frontmatter

        skill_md = tmp_path / "SKILL.md"
        skill_md.write_text("---\nname: investigate\ndescription: foo\n---\n# content")
        result = frozenset((read_skill_frontmatter(skill_md).data or {}).get("categories", ()))
        assert result == frozenset()

    def test_read_skill_categories_returns_empty_when_no_frontmatter(self, tmp_path) -> None:
        from autoskillit.workspace.skill_format import read_skill_frontmatter

        skill_md = tmp_path / "SKILL.md"
        skill_md.write_text("# No frontmatter here")
        result = frozenset((read_skill_frontmatter(skill_md).data or {}).get("categories", ()))
        assert result == frozenset()

    def test_read_skill_categories_multiple_categories(self, tmp_path) -> None:
        from autoskillit.workspace.skill_format import read_skill_frontmatter

        skill_md = tmp_path / "SKILL.md"
        skill_md.write_text("---\nname: foo\ncategories: [github, audit]\n---\n# body")
        result = frozenset((read_skill_frontmatter(skill_md).data or {}).get("categories", ()))
        assert result == frozenset({"github", "audit"})

    def test_skill_info_has_categories_field(self) -> None:
        from pathlib import Path

        from autoskillit.workspace.skills import SkillInfo

        info = SkillInfo(name="test", source=SkillSource.BUNDLED, path=Path("/fake/SKILL.md"))
        assert info.categories == frozenset()

    @pytest.mark.parametrize(
        "source_ref",
        [
            SkillSourceRef(
                origin=SkillSource.THIRD_PARTY,
                logical_name="test",
                skill_path=Path("/fake/SKILL.md"),
            ),
            SkillSourceRef(
                origin=SkillSource.BUNDLED,
                logical_name="other",
                skill_path=Path("/fake/SKILL.md"),
            ),
            SkillSourceRef(
                origin=SkillSource.BUNDLED,
                logical_name="test",
                skill_path=Path("/other/SKILL.md"),
            ),
        ],
    )
    def test_skill_info_rejects_mismatched_source_ref(
        self,
        source_ref: SkillSourceRef,
    ) -> None:
        from autoskillit.workspace.skills import SkillInfo

        with pytest.raises(SkillContractError, match="source_ref does not match direct fields"):
            SkillInfo(
                name="test",
                source=SkillSource.BUNDLED,
                path=Path("/fake/SKILL.md"),
                source_ref=source_ref,
            )

    def test_direct_skill_info_rejects_invalid_canonical_frontmatter(self, tmp_path) -> None:
        from autoskillit.core import SkillContractError
        from autoskillit.workspace import EffectiveSkillInvocation
        from autoskillit.workspace.skills import SkillInfo

        info = SkillInfo(
            name="invalid-direct",
            source=SkillSource.PROJECT_LOCAL,
            path=tmp_path / "SKILL.md",
            canonical_content="missing frontmatter delimiters",
        )

        assert info.invalid_reason == "invalid frontmatter: missing_opening_delimiter"
        with pytest.raises(SkillContractError, match="invalid effective invocation contract"):
            EffectiveSkillInvocation(
                root=info,
                closure=(info,),
                capability_union=frozenset(),
                project_root=tmp_path,
                execution_role=SkillExecutionRole.SESSION,
            )

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
            "audit-docs",
            "audit-review-decisions",
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

    def test_make_campaign_has_fleet_category(self) -> None:
        """make-campaign must declare both orchestration-family and fleet categories."""
        info = DefaultSkillResolver().resolve("make-campaign")
        assert info is not None
        assert "fleet" in info.categories, "make-campaign missing 'fleet' category"
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

    def test_planner_elaborate_assignments_has_planner_category(self) -> None:
        info = DefaultSkillResolver().resolve("planner-elaborate-assignments")
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
    "classify-experiment-type",
    "apply-review-dimensions",
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


def test_all_session_extended_skills_have_tier_assignment():
    """Every agent-facing extended skill must have a session injection tier."""
    from autoskillit.config import load_config

    config = load_config()
    all_tiers = set(config.skills.tier1) | set(config.skills.tier2) | set(config.skills.tier3)
    resolver = DefaultSkillResolver()
    extended = {
        s.name
        for s in resolver.list_all()
        if s.source == SkillSource.BUNDLED_EXTENDED
        and s.execution_role is SkillExecutionRole.SESSION
    }
    unassigned = extended - all_tiers
    assert not unassigned, f"Skills missing tier assignment: {sorted(unassigned)}"


def test_orchestrator_skill_cannot_be_readded_to_session_tier(tmp_path: Path) -> None:
    """Tier validation rejects role-derived orchestrators in session injection tiers."""
    from autoskillit.config import load_config
    from autoskillit.core import SkillContractError
    from autoskillit.workspace import validate_skill_tier_roles

    config_dir = tmp_path / ".autoskillit"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text("skills:\n  tier2:\n    - process-issues\n")

    config = load_config(tmp_path)
    with pytest.raises(SkillContractError, match="process-issues|ORCHESTRATOR"):
        validate_skill_tier_roles(
            config.skill_visibility_spec(),
            DefaultSkillResolver(),
            tmp_path,
        )


def test_activate_deps_are_resolvable():
    """Every activate_deps entry resolves to a known pack or known skill."""
    from autoskillit.core import PACK_REGISTRY

    resolver = DefaultSkillResolver()
    all_names = {s.name for s in resolver.list_all()}
    for skill_info in resolver.list_all():
        for dep in skill_info.activate_deps:
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


def test_audit_docs_skill_md_exists() -> None:
    resolver = DefaultSkillResolver()
    info = resolver.resolve("audit-docs")
    assert info is not None
    assert info.path.exists()


# ---------------------------------------------------------------------------
# Cache tests
# ---------------------------------------------------------------------------


def test_list_all_cache_hit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Second list_all() call returns cached result without re-scanning."""
    monkeypatch.setattr(_skills_mod, "_LIST_ALL_CACHE", None)
    monkeypatch.setattr(_skills_mod, "_LIST_ALL_CACHE_KEY", (0.0, 0.0))
    resolver = DefaultSkillResolver()
    result1 = resolver.list_all()
    call_count = 0
    original_scan = _skills_mod._scan_directory

    def counting_scan(*args: object, **kwargs: object) -> list[object]:
        nonlocal call_count
        call_count += 1
        return original_scan(*args, **kwargs)  # type: ignore[no-any-return]

    monkeypatch.setattr(_skills_mod, "_scan_directory", counting_scan)
    result2 = resolver.list_all()
    assert result2 == result1
    assert call_count == 0  # Cache hit — no re-scan


def test_list_all_cache_invalidation_on_mtime_change(monkeypatch: pytest.MonkeyPatch) -> None:
    """list_all() re-scans when directory mtime changes."""
    monkeypatch.setattr(_skills_mod, "_LIST_ALL_CACHE", None)
    monkeypatch.setattr(_skills_mod, "_LIST_ALL_CACHE_KEY", (0.0, 0.0))
    resolver = DefaultSkillResolver()
    result1 = resolver.list_all()
    monkeypatch.setattr(_skills_mod, "_LIST_ALL_CACHE_KEY", (999.0, 999.0))
    call_count = 0
    original_scan = _skills_mod._scan_directory

    def counting_scan(*args: object, **kwargs: object) -> list[object]:
        nonlocal call_count
        call_count += 1
        return original_scan(*args, **kwargs)  # type: ignore[no-any-return]

    monkeypatch.setattr(_skills_mod, "_scan_directory", counting_scan)
    result2 = resolver.list_all()
    assert result2 == result1  # Same content
    assert call_count == 2  # Re-scanned both directories


def test_resolve_instance_cache_hit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Second resolve() for same name returns cached SkillInfo without disk I/O."""
    monkeypatch.setattr(_skills_mod, "_LIST_ALL_CACHE", None)
    monkeypatch.setattr(_skills_mod, "_LIST_ALL_CACHE_KEY", (0.0, 0.0))
    resolver = DefaultSkillResolver()
    result1 = resolver.resolve("make-plan")
    assert result1 is not None
    call_count = 0
    original_fn = _skills_mod._skill_info_from_frontmatter

    def counting_fn(*args: object, **kwargs: object) -> object:
        nonlocal call_count
        call_count += 1
        return original_fn(*args, **kwargs)

    monkeypatch.setattr(_skills_mod, "_skill_info_from_frontmatter", counting_fn)
    result2 = resolver.resolve("make-plan")
    assert result2 is not None
    assert result2.name == result1.name
    assert call_count == 0  # Cache hit


def test_resolve_instance_cache_caches_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """resolve() caches None for unknown skills to avoid repeated is_file() checks."""
    monkeypatch.setattr(_skills_mod, "_LIST_ALL_CACHE", None)
    monkeypatch.setattr(_skills_mod, "_LIST_ALL_CACHE_KEY", (0.0, 0.0))
    resolver = DefaultSkillResolver()
    result1 = resolver.resolve("nonexistent-skill-xyz")
    assert result1 is None
    assert "nonexistent-skill-xyz" in resolver._resolve_cache
    assert resolver._resolve_cache["nonexistent-skill-xyz"] is None


# ---------------------------------------------------------------------------
# Backend requirements parsing tests
# ---------------------------------------------------------------------------


class TestSkillExecutionRoleParsing:
    def test_valid_omission_defaults_to_session(self, tmp_path: Path) -> None:
        from autoskillit.workspace.skills import _skill_info_from_frontmatter

        skill_md = tmp_path / "SKILL.md"
        skill_md.write_text("---\nname: test\n---\n# body")
        info = _skill_info_from_frontmatter("test", SkillSource.BUNDLED, skill_md)
        assert info.execution_role is SkillExecutionRole.SESSION
        assert info.invalid_reason is None

    @pytest.mark.parametrize(
        ("role", "expected"),
        [
            ("session", SkillExecutionRole.SESSION),
            ("orchestrator", SkillExecutionRole.ORCHESTRATOR),
            ("fleet", SkillExecutionRole.FLEET),
        ],
    )
    def test_explicit_execution_role_is_typed(
        self, tmp_path: Path, role: str, expected: SkillExecutionRole
    ) -> None:
        from autoskillit.workspace.skills import _skill_info_from_frontmatter

        skill_md = tmp_path / "SKILL.md"
        skill_md.write_text(f"---\nname: test\nexecution_role: {role}\n---\n# body")
        info = _skill_info_from_frontmatter("test", SkillSource.BUNDLED, skill_md)
        assert info.execution_role is expected
        assert info.invalid_reason is None

    @pytest.mark.parametrize(
        "content",
        [
            "# no frontmatter",
            "---\nname: [unterminated\n---\n# body",
            "---\nname: test\n# missing closing delimiter",
            "---\n- name\n- test\n---\n# body",
        ],
    )
    def test_invalid_frontmatter_never_receives_session_default(
        self, tmp_path: Path, content: str
    ) -> None:
        from autoskillit.workspace.skills import _skill_info_from_frontmatter

        skill_md = tmp_path / "SKILL.md"
        skill_md.write_text(content)
        info = _skill_info_from_frontmatter("test", SkillSource.BUNDLED, skill_md)
        assert info.execution_role is None
        assert info.invalid_reason is not None

    def test_session_contract_cannot_declare_run_skill(self, tmp_path: Path) -> None:
        from autoskillit.workspace.skills import _skill_info_from_frontmatter

        skill_md = tmp_path / "SKILL.md"
        skill_md.write_text(
            "---\nname: test\nexecution_role: session\nuses_capabilities: [run_skill]\n---\n# body"
        )
        info = _skill_info_from_frontmatter("test", SkillSource.BUNDLED, skill_md)
        assert info.invalid_reason is not None
        assert "run_skill" in info.invalid_reason
        assert "session" in info.invalid_reason

    def test_orchestrator_contract_may_declare_run_skill(self, tmp_path: Path) -> None:
        from autoskillit.workspace.skills import _skill_info_from_frontmatter

        skill_md = tmp_path / "SKILL.md"
        skill_md.write_text(
            "---\nname: test\nexecution_role: orchestrator\n"
            'uses_capabilities: [run_skill]\n---\nCall run_skill("child").'
        )
        info = _skill_info_from_frontmatter("test", SkillSource.BUNDLED, skill_md)
        assert info.execution_role is SkillExecutionRole.ORCHESTRATOR
        assert info.invalid_reason is None


class TestSkillInfoSchemaExhaustiveness:
    def test_all_skillinfo_fields_parsed_by_frontmatter_function(self) -> None:
        """Every non-constructor field on SkillInfo must be parsed."""
        import ast
        import dataclasses
        import inspect

        from autoskillit.workspace.skills import SkillInfo, _skill_info_from_frontmatter

        dc_fields = {f.name for f in dataclasses.fields(SkillInfo)}
        constructor_only = {"name", "source", "path", "source_ref"}
        derived_fields = {
            "execution_role",
            "frontmatter",
            "invalidities",
            "semantic_plan",
        }
        parseable_fields = dc_fields - constructor_only - derived_fields

        source = inspect.getsource(_skill_info_from_frontmatter)
        tree = ast.parse(source)
        data_gets: set[str] = set()
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                data_gets.add(node.args[0].value)

        missing = parseable_fields - data_gets
        assert not missing, (
            f"SkillInfo field(s) {sorted(missing)} are not read by "
            f"_skill_info_from_frontmatter via data.get(). "
            f"Add data.get() calls for these fields."
        )


def test_effective_catalog_is_path_free(tmp_path: Path) -> None:
    catalog = DefaultSkillResolver().list_effective(
        tmp_path,
        SkillExecutionRole.SESSION,
    )

    assert not hasattr(catalog, "project_root")
    assert catalog.skills
    for skill in catalog.skills:
        assert not hasattr(skill, "path")
        assert not hasattr(skill, "source_ref")
        assert all(
            not isinstance(value, Path)
            for value in (
                skill.source_identity.logical_name,
                skill.source_identity.search_dir,
                skill.source_identity.precedence,
            )
        )


def _resolver_with_visibility_skills(tmp_path: Path) -> DefaultSkillResolver:
    skills_dir = tmp_path / "bundled"
    extended_dir = tmp_path / "extended"
    extended_dir.mkdir()
    for name, category in (
        ("core-skill", "kitchen-core"),
        ("research-skill", "research"),
        ("audit-skill", "audit"),
        ("planner-skill", "planner"),
    ):
        skill_dir = skills_dir / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\ncategories: [{category}]\n---\n# {name}\n",
            encoding="utf-8",
        )
    resolver = DefaultSkillResolver()
    resolver._dir = skills_dir
    resolver._extended_dir = extended_dir
    return resolver


def test_effective_catalog_applies_pack_and_recipe_visibility(tmp_path: Path) -> None:
    resolver = _resolver_with_visibility_skills(tmp_path)

    default_catalog = resolver.list_effective(tmp_path, SkillExecutionRole.SESSION)
    recipe_catalog = resolver.list_effective(
        tmp_path,
        SkillExecutionRole.SESSION,
        recipe_packs=frozenset({"research"}),
    )

    assert "research-skill" not in {skill.name for skill in default_catalog.skills}
    assert "research-skill" not in default_catalog.namespace_sources
    assert "research-skill" in {skill.name for skill in recipe_catalog.skills}
    assert "research-skill" in recipe_catalog.namespace_sources


def test_invalid_project_override_fails_closed_without_bundled_fallback(
    tmp_path: Path,
) -> None:
    resolver = _resolver_with_visibility_skills(tmp_path)
    override_dir = tmp_path / ".claude" / "skills" / "core-skill"
    override_dir.mkdir(parents=True)
    override_path = override_dir / "SKILL.md"
    override_path.write_text(
        '---\nname: core-skill\n---\nSpawn the worker via `Agent(model="sonnet")`.\n',
        encoding="utf-8",
    )

    selected = resolver.resolve_effective("core-skill", tmp_path)

    assert selected is not None
    assert selected.source is SkillSource.PROJECT_LOCAL
    assert selected.path == override_path
    assert selected.invalid_reason is not None
    with pytest.raises(SkillContractError) as exc_info:
        resolver.list_effective(tmp_path, SkillExecutionRole.SESSION)
    assert str(exc_info.value) == (
        "effective skill catalog contains invalid contracts: "
        f"'core-skill': {selected.invalid_reason}"
    )


def test_invalid_project_only_skill_is_excluded_without_poisoning_catalog(
    tmp_path: Path,
) -> None:
    resolver = _resolver_with_visibility_skills(tmp_path)
    skill_dir = tmp_path / ".claude" / "skills" / "project-only"
    skill_dir.mkdir(parents=True)
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text(
        '---\nname: project-only\n---\nSpawn via `Agent(model="sonnet")`.\n',
        encoding="utf-8",
    )

    selected = resolver.resolve_effective("project-only", tmp_path)
    catalog = resolver.list_effective(tmp_path, SkillExecutionRole.SESSION)

    assert selected is not None
    assert selected.source is SkillSource.PROJECT_LOCAL
    assert selected.path == skill_path
    assert selected.invalid_reason is not None
    assert "project-only" not in {skill.name for skill in catalog.skills}
    assert "project-only" not in catalog.namespace_sources


def test_effective_catalog_applies_subsets_and_recipe_features(tmp_path: Path) -> None:
    resolver = _resolver_with_visibility_skills(tmp_path)
    visibility = SkillVisibilitySpec(
        disabled_categories=frozenset({"audit"}),
    )

    catalog = resolver.list_effective(
        tmp_path,
        SkillExecutionRole.SESSION,
        visibility=visibility,
        recipe_features=frozenset({"planner"}),
    )
    names = {skill.name for skill in catalog.skills}

    assert "audit-skill" not in names
    assert "audit-skill" not in catalog.namespace_sources
    assert "planner-skill" in names
    assert "planner-skill" in catalog.namespace_sources


def test_effective_catalog_keeps_only_available_external_namespace_targets(
    tmp_path: Path,
) -> None:
    resolver = _resolver_with_visibility_skills(tmp_path)

    catalog = resolver.list_effective(
        tmp_path,
        SkillExecutionRole.SESSION,
        allow_only=frozenset({"core-skill"}),
    )

    assert {skill.name for skill in catalog.skills} == {"core-skill"}
    assert "audit-skill" in catalog.namespace_sources
    assert "research-skill" not in catalog.namespace_sources


def test_explicit_invocation_bypasses_feature_but_not_pack_visibility(
    tmp_path: Path,
) -> None:
    resolver = _resolver_with_visibility_skills(tmp_path)
    visibility = SkillVisibilitySpec()

    invocation = resolver.resolve_invocation(
        "planner-skill",
        tmp_path,
        SkillExecutionRole.SESSION,
        visibility=visibility,
    )

    assert invocation.root.name == "planner-skill"
    with pytest.raises(SkillContractError, match="disabled"):
        resolver.resolve_invocation(
            "research-skill",
            tmp_path,
            SkillExecutionRole.SESSION,
            visibility=visibility,
        )


def test_effective_invocation_rejects_inconsistent_direct_construction(
    tmp_path: Path,
) -> None:
    from dataclasses import replace

    resolver = _resolver_with_visibility_skills(tmp_path)
    invocation = resolver.resolve_invocation(
        "core-skill",
        tmp_path,
        SkillExecutionRole.SESSION,
    )

    with pytest.raises(SkillContractError, match="root.*closure"):
        replace(invocation, closure=())
    with pytest.raises(SkillContractError, match="role"):
        replace(invocation, execution_role=SkillExecutionRole.ORCHESTRATOR)
    with pytest.raises(SkillContractError, match="capability union"):
        replace(invocation, capability_union=frozenset({"run_skill"}))


def test_projection_reuses_the_single_frontmatter_parse(tmp_path: Path, monkeypatch) -> None:
    import autoskillit.workspace._projected_artifact.materialization as projection_module
    from autoskillit.workspace import (
        EffectiveSkillCatalog,
        SkillCatalogEntry,
        SkillProjectionContext,
        project_agent_skill_document,
    )
    from autoskillit.workspace.skills import _skill_info_from_frontmatter

    skill_md = tmp_path / "SKILL.md"
    skill_md.write_text(
        "---\nname: parsed-once\ndescription: Parsed once.\n---\nbody\n",
        encoding="utf-8",
    )
    info = _skill_info_from_frontmatter("parsed-once", SkillSource.PROJECT_LOCAL, skill_md)
    entry = SkillCatalogEntry.from_skill_info(info)
    catalog = EffectiveSkillCatalog(
        skills=(entry,),
        execution_role=SkillExecutionRole.SESSION,
    )
    monkeypatch.setattr(
        projection_module,
        "parse_frontmatter_content",
        lambda _content: pytest.fail("projection reparsed canonical frontmatter"),
    )

    document = project_agent_skill_document(
        entry,
        SkillProjectionContext(cwd=tmp_path, catalog=catalog),
    )

    assert document.content.endswith("body\n")
    assert entry.frontmatter is info.frontmatter
    assert not hasattr(_skills_mod, "_read_skill_frontmatter")


def test_projection_context_derives_and_validates_backend_conventions(
    tmp_path: Path,
) -> None:
    from autoskillit.core import BackendConventions
    from autoskillit.workspace import EffectiveSkillCatalog, SkillProjectionContext

    conventions = BackendConventions(skills_subdir=Path("agent-skills"))
    backend = SimpleNamespace(conventions=conventions)
    catalog = EffectiveSkillCatalog(
        skills=(),
        execution_role=SkillExecutionRole.SESSION,
    )

    context = SkillProjectionContext(
        cwd=tmp_path,
        catalog=catalog,
        backend=backend,
    )

    assert context.conventions is conventions
    with pytest.raises(SkillContractError, match="conventions do not match"):
        SkillProjectionContext(
            cwd=tmp_path,
            catalog=catalog,
            backend=backend,
            conventions=BackendConventions(skills_subdir=Path("other-skills")),
        )


@pytest.mark.parametrize("invalid_version", [True, 0, -1])
def test_projection_context_requires_exact_positive_version(
    tmp_path: Path,
    invalid_version: object,
) -> None:
    from autoskillit.workspace import EffectiveSkillCatalog, SkillProjectionContext

    catalog = EffectiveSkillCatalog(
        skills=(),
        execution_role=SkillExecutionRole.SESSION,
    )

    with pytest.raises(SkillContractError, match="positive integer"):
        SkillProjectionContext(
            cwd=tmp_path,
            catalog=catalog,
            projection_version=invalid_version,  # type: ignore[arg-type]
        )


def test_direct_install_projection_cache_identity_and_reuse(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from autoskillit.core import (
        BackendConventions,
        DirectInstall,
        PluginArtifactContentionError,
        PluginLoadMode,
        SkillSourceRef,
    )
    from autoskillit.workspace import (
        EffectiveSkillCatalog,
        SkillCatalogEntry,
        project_direct_install_authority,
    )
    from autoskillit.workspace.skills import _skill_info_from_frontmatter

    monkeypatch.setenv("HOME", str(tmp_path))
    source_root = tmp_path / "plugin"
    skill_path = source_root / "canonical" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text(
        "---\n"
        "name: immutable-cache\n"
        "description: Immutable projection fixture.\n"
        "execution_role: session\n"
        "uses_capabilities: []\n"
        "---\n"
        "base branch: {{DEFAULT_BASE_BRANCH}}\n"
        "external skill: /autoskillit:external\n",
        encoding="utf-8",
    )
    info = _skill_info_from_frontmatter(
        "immutable-cache",
        SkillSource.BUNDLED,
        skill_path,
        source_ref=SkillSourceRef(
            origin=SkillSource.BUNDLED,
            logical_name="immutable-cache",
            skill_path=skill_path,
        ),
    )
    catalog = EffectiveSkillCatalog(
        skills=(SkillCatalogEntry.from_skill_info(info),),
        execution_role=SkillExecutionRole.SESSION,
        namespace_sources={"external": SkillSource.BUNDLED},
    )
    backend = SimpleNamespace(
        name="codex",
        conventions=BackendConventions(),
    )
    source = DirectInstall(plugin_dir=source_root)

    authority = project_direct_install_authority(
        source,
        cwd=tmp_path,
        base_branch="develop",
        catalog=catalog,
    )
    first = authority.acquire_launch_binding(
        backend=backend, load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR
    )
    assert first.plugin_dir is not None
    first_inode = first.plugin_dir.stat().st_ino
    projected_skill = first.plugin_dir / "skills" / "immutable-cache" / "SKILL.md"
    assert "base branch: develop" in projected_skill.read_text(encoding="utf-8")
    second = authority.acquire_launch_binding(
        backend=backend,
        load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
    )
    assert second.plugin_dir is not None
    assert second.plugin_dir == first.plugin_dir
    assert second.plugin_dir.stat().st_ino == first_inode
    manifest_path = first.plugin_dir.parent / (
        f".{first.plugin_dir.name}.autoskillit-projection.json"
    )
    manifest_path.unlink()
    with pytest.raises(PluginArtifactContentionError):
        authority.acquire_launch_binding(
            backend=backend,
            load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
        )
    assert not manifest_path.exists()
    assert first.plugin_dir.stat().st_ino == first_inode
    first.close()
    second.close()
    recovered = authority.acquire_launch_binding(
        backend=backend,
        load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
    )
    assert recovered.plugin_dir is not None
    assert recovered.plugin_dir == first.plugin_dir
    assert manifest_path.is_file()

    main_authority = project_direct_install_authority(
        source,
        cwd=tmp_path,
        base_branch="main",
        catalog=catalog,
    )
    main_projection = main_authority.acquire_launch_binding(
        backend=backend,
        load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
    )
    assert main_projection.plugin_dir is not None
    assert main_projection.plugin_dir != first.plugin_dir
    assert "base branch: main" in (
        main_projection.plugin_dir / "skills" / "immutable-cache" / "SKILL.md"
    ).read_text(encoding="utf-8")

    local_namespace_catalog = EffectiveSkillCatalog(
        skills=catalog.skills,
        execution_role=SkillExecutionRole.SESSION,
        namespace_sources={"external": SkillSource.PROJECT_LOCAL},
    )
    local_authority = project_direct_install_authority(
        source,
        cwd=tmp_path,
        base_branch="develop",
        catalog=local_namespace_catalog,
    )
    local_namespace_projection = local_authority.acquire_launch_binding(
        backend=backend,
        load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
    )
    assert local_namespace_projection.plugin_dir is not None
    assert local_namespace_projection.plugin_dir != first.plugin_dir
    assert "external skill: /external" in (
        local_namespace_projection.plugin_dir / "skills" / "immutable-cache" / "SKILL.md"
    ).read_text(encoding="utf-8")
    for binding in (local_namespace_projection, main_projection, recovered, second, first):
        binding.close()
        assert binding.closed


def test_projection_strips_all_machine_authority_and_preserves_private_deps(
    tmp_path: Path,
) -> None:
    from autoskillit.core import MACHINE_ONLY_SKILL_FRONTMATTER_KEYS
    from autoskillit.workspace import (
        EffectiveSkillCatalog,
        SkillCatalogEntry,
        SkillProjectionContext,
        parse_frontmatter_content,
        project_agent_skill_document,
    )
    from autoskillit.workspace.skills import _skill_info_from_frontmatter

    expected_machine_keys = frozenset(
        {
            "activate_deps",
            "execution_role",
            "uses_capabilities",
        }
    )
    assert MACHINE_ONLY_SKILL_FRONTMATTER_KEYS == expected_machine_keys
    skill_md = tmp_path / "SKILL.md"
    skill_md.write_text(
        "---\n"
        "name: projected-contract\n"
        "description: Public description.\n"
        "uses_capabilities: []\n"
        "execution_role: session\n"
        "activate_deps: [dependency]\n"
        "---\n"
        "public body\n",
        encoding="utf-8",
    )
    info = _skill_info_from_frontmatter(
        "projected-contract",
        SkillSource.PROJECT_LOCAL,
        skill_md,
    )
    entry = SkillCatalogEntry.from_skill_info(info)
    catalog = EffectiveSkillCatalog(
        skills=(entry,),
        execution_role=SkillExecutionRole.SESSION,
    )

    document = project_agent_skill_document(
        entry,
        SkillProjectionContext(cwd=tmp_path, catalog=catalog),
    )
    projected = parse_frontmatter_content(document.content)

    assert projected.is_valid and projected.data is not None
    assert expected_machine_keys.isdisjoint(projected.data)
    assert projected.data["description"] == "Public description."
    assert document.content.endswith("public body\n")
    assert info.activate_deps == ("dependency",)
    assert entry.activate_deps == ("dependency",)


@pytest.mark.parametrize(
    ("source", "expected_reference"),
    [
        (SkillSource.BUNDLED, "/autoskillit:target"),
        (SkillSource.BUNDLED_EXTENDED, "/target"),
        (SkillSource.PROJECT_LOCAL, "/target"),
        (SkillSource.THIRD_PARTY, "/target"),
    ],
)
def test_projection_namespace_is_exhaustive_for_every_source(
    tmp_path: Path,
    source: SkillSource,
    expected_reference: str,
) -> None:
    from autoskillit.workspace import (
        EffectiveSkillCatalog,
        SkillCatalogEntry,
        SkillProjectionContext,
        project_agent_skill_document,
    )
    from autoskillit.workspace.skills import _skill_info_from_frontmatter

    def write_skill(name: str, body: str, origin: SkillSource) -> SkillCatalogEntry:
        skill_md = tmp_path / origin.value / name / "SKILL.md"
        skill_md.parent.mkdir(parents=True, exist_ok=True)
        skill_md.write_text(
            f"---\nname: {name}\ndescription: Fixture.\n---\n{body}\n",
            encoding="utf-8",
        )
        return SkillCatalogEntry.from_skill_info(
            _skill_info_from_frontmatter(name, origin, skill_md)
        )

    root = write_skill("root", "Call /autoskillit:target now.", SkillSource.BUNDLED)
    target = write_skill("target", "Target.", source)
    catalog = EffectiveSkillCatalog(
        skills=(root, target),
        execution_role=SkillExecutionRole.SESSION,
    )

    projected = project_agent_skill_document(
        root,
        SkillProjectionContext(cwd=tmp_path, catalog=catalog),
    )

    assert f"Call {expected_reference} now." in projected.content
    assert {member.value for member in SkillSource} == {
        "bundled",
        "bundled_extended",
        "project_local",
        "third_party",
    }


@pytest.mark.parametrize(
    "source",
    [SkillSource.PROJECT_LOCAL, SkillSource.THIRD_PARTY],
)
def test_projection_never_mutates_external_canonical_sources(
    tmp_path: Path,
    source: SkillSource,
) -> None:
    from autoskillit.workspace import (
        EffectiveSkillCatalog,
        SkillCatalogEntry,
        SkillProjectionContext,
        project_agent_skill_document,
    )
    from autoskillit.workspace.skills import _skill_info_from_frontmatter

    skill_md = tmp_path / source.value / "external" / "SKILL.md"
    skill_md.parent.mkdir(parents=True)
    skill_md.write_text(
        "---\n"
        "name: external\n"
        "description: External source.\n"
        "uses_capabilities: []\n"
        "execution_role: session\n"
        "---\n"
        "external body\n",
        encoding="utf-8",
    )
    before = skill_md.read_bytes()
    info = _skill_info_from_frontmatter("external", source, skill_md)
    entry = SkillCatalogEntry.from_skill_info(info)
    catalog = EffectiveSkillCatalog(
        skills=(entry,),
        execution_role=SkillExecutionRole.SESSION,
    )

    document = project_agent_skill_document(
        entry,
        SkillProjectionContext(cwd=tmp_path, catalog=catalog),
    )

    assert "uses_capabilities:" not in document.content
    assert "execution_role:" not in document.content
    assert skill_md.read_bytes() == before
