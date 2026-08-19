"""Skill resolver: resolve/list, bundled directory layout, cache, execution-role, schema."""

from __future__ import annotations

import functools
import re
from pathlib import Path

import pytest

import autoskillit.workspace.skills as _skills_mod
from autoskillit.core.io import load_yaml
from autoskillit.core.types import (
    SkillExecutionRole,
    SkillSource,
)
from autoskillit.workspace.skills import (
    DefaultSkillResolver,
    bundled_skills_dir,
    bundled_skills_extended_dir,
    render_skill_invalidities,
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
        assert not info.invalidities

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
        assert not info.invalidities

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
        assert info.invalidities

    def test_session_contract_cannot_declare_run_skill(self, tmp_path: Path) -> None:
        from autoskillit.workspace.skills import _skill_info_from_frontmatter

        skill_md = tmp_path / "SKILL.md"
        skill_md.write_text(
            "---\nname: test\nexecution_role: session\nuses_capabilities: [run_skill]\n---\n# body"
        )
        info = _skill_info_from_frontmatter("test", SkillSource.BUNDLED, skill_md)
        assert info.invalidities
        reason = render_skill_invalidities(info.invalidities)
        assert "run_skill" in reason
        assert "session" in reason

    def test_orchestrator_contract_may_declare_run_skill(self, tmp_path: Path) -> None:
        from autoskillit.workspace.skills import _skill_info_from_frontmatter

        skill_md = tmp_path / "SKILL.md"
        skill_md.write_text(
            "---\nname: test\nexecution_role: orchestrator\n"
            'uses_capabilities: [run_skill]\n---\nCall run_skill("child").'
        )
        info = _skill_info_from_frontmatter("test", SkillSource.BUNDLED, skill_md)
        assert info.execution_role is SkillExecutionRole.ORCHESTRATOR
        assert not info.invalidities


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
            "exploration_sidecar_digest",
            "exploration_vectors",
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
