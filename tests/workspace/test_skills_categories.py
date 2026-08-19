"""Skill categories and session-injection tiers: taxonomy, tier assignment, tier fall-through."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core.types import (
    SkillContractError,
    SkillExecutionRole,
    SkillSource,
    SkillSourceRef,
)
from autoskillit.workspace.skills import (
    DefaultSkillResolver,
    render_skill_invalidities,
)

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.small]


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

        assert render_skill_invalidities(info.invalidities) == (
            "invalid frontmatter: missing_opening_delimiter"
        )
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

        resolver = DefaultSkillResolver()
        for name in EXP_LENS_NAMES:
            info = resolver.resolve(name)
            assert info is not None, f"exp-lenz skill '{name}' not found in bundled skills"
            assert info.path.exists(), f"SKILL.md missing for '{name}' at {info.path}"

    def test_all_exp_lens_skills_have_exp_lens_category(self) -> None:
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


def test_stale_precontract_copy_of_bundled_tier_skill_does_not_crash_composition(
    tmp_path: Path,
) -> None:
    """A pre-contract-era project-local shadow of a bundled tier skill falls
    through to the bundled twin instead of crashing composition (#4470)."""
    from autoskillit.config import load_config
    from autoskillit.core import SkillInvalidityKind
    from autoskillit.workspace import validate_skill_tier_roles

    stale_dir = tmp_path / ".claude" / "skills" / "audit-bugs"
    stale_dir.mkdir(parents=True)
    stale_path = stale_dir / "SKILL.md"
    stale_path.write_text(
        "---\n"
        "name: audit-bugs\n"
        "description: Stale pre-contract-era copy.\n"
        "---\n"
        "# audit-bugs\n\n"
        'LOG_DIR="$HOME/.claude/projects/${PWD//\\//-}"\n',
        encoding="utf-8",
    )

    resolver = DefaultSkillResolver()
    config = load_config(tmp_path)
    visibility = config.skill_visibility_spec()

    # Currently raises SkillContractError with the exact #4470 traceback signature:
    # "configured tier2 skill 'audit-bugs' is invalid ... missing declaration for 'claude_dir'".
    validate_skill_tier_roles(visibility, resolver, tmp_path)

    effective = resolver.resolve_effective("audit-bugs", tmp_path)
    assert effective is not None
    assert effective.source in (SkillSource.BUNDLED, SkillSource.BUNDLED_EXTENDED)
    assert not effective.invalidities

    catalog = resolver.list_effective(tmp_path, SkillExecutionRole.SESSION)
    entry = next(skill for skill in catalog.skills if skill.name == "audit-bugs")
    assert entry.source in (SkillSource.BUNDLED, SkillSource.BUNDLED_EXTENDED)
    assert len(catalog.exclusions) == 1
    exclusion = catalog.exclusions[0]
    assert exclusion.name == "audit-bugs"
    assert exclusion.path == stale_path
    assert any(
        item.kind is SkillInvalidityKind.UNDECLARED_CAPABILITY for item in exclusion.invalidities
    )
    assert {item.capability for item in exclusion.invalidities} == {"claude_dir"}


def test_local_only_invalid_skill_is_excluded_with_a_record(tmp_path: Path) -> None:
    """A local-only invalid skill (no bundled twin) is excluded, not silently dropped."""
    stale_dir = tmp_path / ".claude" / "skills" / "my-own-notes"
    stale_dir.mkdir(parents=True)
    stale_path = stale_dir / "SKILL.md"
    stale_path.write_text(
        "---\n"
        "name: my-own-notes\n"
        "description: Stale pre-contract-era copy.\n"
        "---\n"
        "# my-own-notes\n\n"
        'LOG_DIR="$HOME/.claude/projects/${PWD//\\//-}"\n',
        encoding="utf-8",
    )

    resolver = DefaultSkillResolver()
    catalog = resolver.list_effective(tmp_path, SkillExecutionRole.SESSION)

    assert "my-own-notes" not in {skill.name for skill in catalog.skills}
    assert len(catalog.exclusions) == 1
    exclusion = catalog.exclusions[0]
    assert exclusion.name == "my-own-notes"
    assert exclusion.path == stale_path
    assert exclusion.invalidities
    assert exclusion.hints
    assert exclusion.fallback is None


def test_multi_dir_fallthrough_preserves_recipe_loader_semantics(tmp_path: Path) -> None:
    """An invalid higher-precedence local copy falls through to a valid lower one."""
    invalid_dir = tmp_path / ".claude" / "skills" / "x"
    invalid_dir.mkdir(parents=True)
    invalid_path = invalid_dir / "SKILL.md"
    invalid_path.write_text(
        '---\nname: x\n---\nSpawn via `Agent(model="sonnet")`.\n',
        encoding="utf-8",
    )
    valid_dir = tmp_path / ".autoskillit" / "skills" / "x"
    valid_dir.mkdir(parents=True)
    valid_path = valid_dir / "SKILL.md"
    valid_path.write_text(
        "---\nname: x\ndescription: Valid lower-precedence copy.\n---\n# x\n",
        encoding="utf-8",
    )

    resolver = DefaultSkillResolver()
    catalog = resolver.list_effective(tmp_path, SkillExecutionRole.SESSION)

    entry = next(skill for skill in catalog.skills if skill.name == "x")
    assert entry.source is SkillSource.PROJECT_LOCAL
    assert entry.source_identity.search_dir == ".autoskillit/skills"
    assert len(catalog.exclusions) == 1
    assert catalog.exclusions[0].path == invalid_path
    assert catalog.exclusions[0].fallback is SkillSource.PROJECT_LOCAL

    # resolve_effective agrees: the valid lower-precedence copy wins outright.
    resolved = resolver.resolve_effective("x", tmp_path)
    assert resolved is not None
    assert resolved.path == valid_path


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
