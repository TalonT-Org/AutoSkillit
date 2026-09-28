"""Tests for effective skill closure and closure write-path contracts."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import SkillContractError
from autoskillit.workspace.session_skills import (
    SkillsDirectoryProvider,
    resolve_closure_write_dirs,
)

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.small]


def _make_synthetic_provider(
    tmp_path: Path,
    skills: dict[str, dict],
):
    """Build a mocked SkillsDirectoryProvider serving synthetic SKILL.md files.

    skills: mapping of name -> {"deps": [...], "categories": [...]}
    """
    from unittest.mock import MagicMock

    from autoskillit.workspace.skills import SkillInfo, SkillSource, _skill_info_from_frontmatter

    tmp_path.mkdir(parents=True, exist_ok=True)
    skill_infos: list[SkillInfo] = []
    for name, spec in skills.items():
        skill_dir = tmp_path / name
        skill_dir.mkdir()
        deps = spec.get("deps", [])
        categories = spec.get("categories", [])
        fm_lines = [f"name: {name}", f"description: Synthetic {name} skill for testing."]
        if categories:
            fm_lines.append(f"categories: [{', '.join(categories)}]")
        if deps:
            fm_lines.append(f"activate_deps: [{', '.join(deps)}]")
        write_paths = spec.get("write_paths", "inherit")
        if isinstance(write_paths, list):
            quoted = ", ".join(f'"{wp}"' for wp in write_paths)
            fm_lines.append(f"write_paths: [{quoted}]")
        else:
            fm_lines.append(f"write_paths: {write_paths}")
        content = "---\n" + "\n".join(fm_lines) + "\n---\nbody\n"
        (skill_dir / "SKILL.md").write_text(content)
        skill_infos.append(
            _skill_info_from_frontmatter(
                name,
                SkillSource.BUNDLED_EXTENDED,
                skill_dir / "SKILL.md",
            )
        )

    by_name = {info.name: info for info in skill_infos}

    provider = SkillsDirectoryProvider()
    resolver = MagicMock()
    resolver.list_all.return_value = skill_infos
    resolver.resolve.side_effect = lambda n: by_name.get(n)
    provider._resolver = resolver
    return provider


class TestComputeSkillClosure:
    """Tests for ``compute_skill_closure`` and its top-level helper."""

    def test_closure_standalone_returns_only_self(self, tmp_path: Path) -> None:
        from autoskillit.workspace.session_skills import compute_skill_closure

        provider = _make_synthetic_provider(tmp_path, {"lone": {}})
        assert compute_skill_closure("lone", provider) == frozenset({"lone"})

    def test_make_plan_production_closure_is_exact(self) -> None:
        from autoskillit.workspace.session_skills import compute_skill_closure

        provider = SkillsDirectoryProvider()
        closure = compute_skill_closure("make-plan", provider)
        assert closure == frozenset({"make-plan", "write-recipe"})

    def test_closure_individual_skill_dep(self, tmp_path: Path) -> None:
        from autoskillit.workspace.session_skills import compute_skill_closure

        provider = _make_synthetic_provider(
            tmp_path,
            {"target": {"deps": ["other"]}, "other": {}},
        )
        assert compute_skill_closure("target", provider) == frozenset({"target", "other"})

    def test_closure_two_level_transitive(self, tmp_path: Path) -> None:
        from autoskillit.workspace.session_skills import compute_skill_closure

        provider = _make_synthetic_provider(
            tmp_path,
            {"a": {"deps": ["b"]}, "b": {"deps": ["c"]}, "c": {}},
        )
        assert compute_skill_closure("a", provider) == frozenset({"a", "b", "c"})

    def test_closure_cycle_safe(self, tmp_path: Path) -> None:
        from autoskillit.workspace.session_skills import compute_skill_closure

        provider = _make_synthetic_provider(
            tmp_path,
            {"a": {"deps": ["b"]}, "b": {"deps": ["a"]}},
        )
        assert compute_skill_closure("a", provider) == frozenset({"a", "b"})

    def test_closure_unknown_dep_silently_ignored(self, tmp_path: Path) -> None:
        from autoskillit.workspace.session_skills import compute_skill_closure

        provider = _make_synthetic_provider(
            tmp_path,
            {"target": {"deps": ["ghost"]}},
        )
        assert compute_skill_closure("target", provider) == frozenset({"target"})

    def test_closure_unknown_target_returns_empty_frozenset(self, tmp_path: Path) -> None:
        from autoskillit.workspace.session_skills import compute_skill_closure

        provider = _make_synthetic_provider(tmp_path, {"alpha": {}})
        assert compute_skill_closure("nonexistent", provider) == frozenset()

    def test_closure_pack_dep_with_no_members_returns_only_target(self, tmp_path: Path) -> None:
        from autoskillit.workspace.session_skills import compute_skill_closure

        # 'audit' is a real PACK_REGISTRY key, but no synthetic skills declare it.
        provider = _make_synthetic_provider(
            tmp_path,
            {"target": {"deps": ["audit"]}},
        )
        assert compute_skill_closure("target", provider) == frozenset({"target"})


def _write_invocation_skill(
    root: Path,
    name: str,
    *,
    capabilities: tuple[str, ...] = (),
    execution_role: str = "session",
    deps: tuple[str, ...] = (),
    categories: tuple[str, ...] = (),
) -> None:
    skill_path = root / name / "SKILL.md"
    skill_path.parent.mkdir(parents=True, exist_ok=True)
    frontmatter = [
        f"name: {name}",
        f"description: Synthetic {name} invocation contract.",
        f"execution_role: {execution_role}",
        "write_paths: inherit",
    ]
    if capabilities:
        frontmatter.append(f"uses_capabilities: [{', '.join(capabilities)}]")
    if deps:
        frontmatter.append(f"activate_deps: [{', '.join(deps)}]")
    if categories:
        frontmatter.append(f"categories: [{', '.join(categories)}]")
    evidence = {
        "github_api_write": "Run `gh issue edit 1 --body-file issue.md`.",
        "test_check": "Call `test_check()`.",
        "run_skill": 'Call run_skill("child").',
    }
    body = "\n".join(evidence[capability] for capability in capabilities)
    skill_path.write_text("---\n" + "\n".join(frontmatter) + "\n---\n" + (body or "body") + "\n")


def _make_effective_resolver(tmp_path: Path, monkeypatch, skills: dict[str, dict]):
    from autoskillit.workspace.skills import DefaultSkillResolver

    bundled = tmp_path / "bundled"
    extended = tmp_path / "extended"
    bundled.mkdir()
    extended.mkdir()
    for name, spec in skills.items():
        _write_invocation_skill(extended, name, **spec)
    resolver = DefaultSkillResolver()
    monkeypatch.setattr(resolver, "_dir", bundled)
    monkeypatch.setattr(resolver, "_extended_dir", extended)
    return resolver


class TestEffectiveInvocationClosurePolicy:
    """The complete direct/pack closure supplies one validated capability contract."""

    def test_direct_closure_does_not_scan_unrelated_catalog(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        from autoskillit.core import SkillExecutionRole

        resolver = _make_effective_resolver(
            tmp_path,
            monkeypatch,
            {
                "root": {"deps": ("direct",)},
                "direct": {"capabilities": ("github_api_write",)},
                "unrelated": {},
            },
        )
        project_root = tmp_path / "project"
        project_root.mkdir()

        def fail_catalog_scan(_project_root: Path | None) -> tuple[()]:
            raise AssertionError("direct invocation must not scan unrelated skills")

        monkeypatch.setattr(resolver, "_list_effective_unfiltered", fail_catalog_scan)

        invocation = resolver.resolve_invocation(
            "root",
            project_root,
            SkillExecutionRole.SESSION,
        )

        assert [member.name for member in invocation.closure] == ["root", "direct"]

    def test_capability_union_includes_direct_and_pack_expanded_dependencies(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        from autoskillit.core import SkillExecutionRole

        resolver = _make_effective_resolver(
            tmp_path,
            monkeypatch,
            {
                "root": {"deps": ("direct", "audit")},
                "direct": {"capabilities": ("github_api_write",)},
                "pack-member": {
                    "capabilities": ("test_check",),
                    "categories": ("audit",),
                },
            },
        )
        project_root = tmp_path / "project"
        project_root.mkdir()

        invocation = resolver.resolve_invocation("root", project_root, SkillExecutionRole.SESSION)

        assert invocation.root.name == "root"
        assert {member.name for member in invocation.closure} == {
            "root",
            "direct",
            "pack-member",
        }
        assert invocation.capability_union == frozenset({"github_api_write", "test_check"})
        assert invocation.project_root == project_root.resolve()

    def test_pack_expansion_survives_unrelated_invalid_local_skill(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """T13: resolve_invocation's pack-expansion path calls
        _list_effective_unfiltered directly and must unpack its
        (skills, exclusions) pair correctly — pins the call path in
        skills/__init__.py. An unrelated invalid
        project-local skill in the same scan must not break pack-member
        enumeration."""
        from autoskillit.core import SkillExecutionRole

        resolver = _make_effective_resolver(
            tmp_path,
            monkeypatch,
            {
                "root": {"deps": ("audit",)},
                "pack-member": {"categories": ("audit",)},
            },
        )
        project_root = tmp_path / "project"
        broken_dir = project_root / ".claude" / "skills" / "broken-unrelated"
        broken_dir.mkdir(parents=True)
        (broken_dir / "SKILL.md").write_text(
            '---\nname: broken-unrelated\n---\nSpawn via `Agent(model="sonnet")`.\n',
            encoding="utf-8",
        )

        invocation = resolver.resolve_invocation("root", project_root, SkillExecutionRole.SESSION)

        assert {member.name for member in invocation.closure} == {"root", "pack-member"}

    @pytest.mark.parametrize(
        ("dependency", "root_deps", "categories"),
        [
            pytest.param("direct", ("direct",), (), id="direct"),
            pytest.param("pack-member", ("audit",), ("audit",), id="pack-expanded"),
        ],
    )
    def test_orchestrator_dependency_rejected_before_materialization(
        self,
        tmp_path: Path,
        monkeypatch,
        dependency: str,
        root_deps: tuple[str, ...],
        categories: tuple[str, ...],
    ) -> None:
        from autoskillit.core import SkillExecutionRole

        resolver = _make_effective_resolver(
            tmp_path,
            monkeypatch,
            {
                "root": {"deps": root_deps},
                dependency: {
                    "execution_role": "orchestrator",
                    "capabilities": ("run_skill",),
                    "categories": categories,
                },
            },
        )
        project_root = tmp_path / "project"
        project_root.mkdir()

        with pytest.raises(
            ValueError, match=rf"{dependency}.*orchestrator|orchestrator.*{dependency}"
        ):
            resolver.resolve_invocation("root", project_root, SkillExecutionRole.SESSION)


class TestResolveClosureWriteDirs:
    """Closure write dirs compose through the shared write-scope fold."""

    def test_only_bounded_members_contribute(self, tmp_path: Path) -> None:
        provider = _make_synthetic_provider(
            tmp_path / "skills",
            {
                "a": {"write_paths": ["{{AUTOSKILLIT_TEMP}}/a/"]},
                "open": {"write_paths": "unrestricted"},
                "quiet": {"write_paths": "inherit"},
                "b": {"write_paths": [".autoskillit/temp/b/"]},
            },
        )
        closure = tuple(provider.list_skills())
        cwd = tmp_path / "project"
        cwd.mkdir()

        dirs = resolve_closure_write_dirs(closure, str(cwd))

        temp = (cwd / ".autoskillit" / "temp").resolve()
        assert dirs == [temp / "a", temp / "b"]
        assert resolve_closure_write_dirs(closure, str(cwd), [temp / "a"]) == [temp / "b"]

    def test_member_without_valid_scope_is_a_contract_error(self, tmp_path: Path) -> None:
        provider = _make_synthetic_provider(
            tmp_path / "skills", {"broken": {"write_paths": "all"}}
        )

        with pytest.raises(SkillContractError, match="broken lacks a valid write scope"):
            resolve_closure_write_dirs(tuple(provider.list_skills()), str(tmp_path))

    def test_declared_directory_escaping_the_temp_root_is_a_contract_error(
        self, tmp_path: Path
    ) -> None:
        provider = _make_synthetic_provider(
            tmp_path / "skills", {"escape": {"write_paths": ["{{AUTOSKILLIT_TEMP}}/escape/"]}}
        )
        cwd = tmp_path / "project"
        (cwd / ".autoskillit" / "temp").mkdir(parents=True)
        (cwd / ".autoskillit" / "temp" / "escape").symlink_to(tmp_path, target_is_directory=True)

        with pytest.raises(SkillContractError, match="escape"):
            resolve_closure_write_dirs(tuple(provider.list_skills()), str(cwd))
