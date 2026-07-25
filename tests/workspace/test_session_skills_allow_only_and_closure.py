"""Tests for effective skill closure and closure write-path contracts."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.workspace.session_skills import (
    SkillsDirectoryProvider,
)
from autoskillit.workspace.session_skills import (
    _parse_write_paths as _parse_structured_write_paths,
)
from autoskillit.workspace.skill_format import parse_frontmatter_content

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.small]


def _write_paths(content: str) -> list[str]:
    return _parse_structured_write_paths(parse_frontmatter_content(content))


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
        write_paths = spec.get("write_paths", [])
        if write_paths:
            quoted = ", ".join(f'"{wp}"' for wp in write_paths)
            fm_lines.append(f"write_paths: [{quoted}]")
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
    ]
    if capabilities:
        frontmatter.append(f"uses_capabilities: [{', '.join(capabilities)}]")
    if deps:
        frontmatter.append(f"activate_deps: [{', '.join(deps)}]")
    if categories:
        frontmatter.append(f"categories: [{', '.join(categories)}]")
    evidence = {
        "github_api_write": "Run `gh issue edit 1 --body-file issue.md`.",
        "git_metadata_write": "Run `git commit -m test`.",
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
                    "capabilities": ("git_metadata_write",),
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
        assert invocation.capability_union == frozenset({"github_api_write", "git_metadata_write"})
        assert invocation.project_root == project_root.resolve()

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


class TestParseWritePaths:
    """Unit tests for _parse_write_paths frontmatter parser."""

    def test_no_write_paths_returns_empty(self) -> None:
        content = "---\nname: skill-a\ndescription: A.\n---\nbody"
        assert _write_paths(content) == []

    def test_single_path(self) -> None:
        content = (
            '---\nname: a\ndescription: A.\nwrite_paths: ["{{AUTOSKILLIT_TEMP}}/a/"]\n---\nbody'
        )
        assert _write_paths(content) == ["{{AUTOSKILLIT_TEMP}}/a/"]

    def test_multiple_paths(self) -> None:
        content = (
            "---\nname: a\ndescription: A.\n"
            'write_paths: ["{{AUTOSKILLIT_TEMP}}/a/", "{{AUTOSKILLIT_TEMP}}/b/"]\n---\nbody'
        )
        assert _write_paths(content) == [
            "{{AUTOSKILLIT_TEMP}}/a/",
            "{{AUTOSKILLIT_TEMP}}/b/",
        ]

    def test_no_frontmatter(self) -> None:
        assert _write_paths("no frontmatter here") == []

    def test_empty_list(self) -> None:
        content = "---\nname: a\ndescription: A.\nwrite_paths: []\n---\nbody"
        assert _write_paths(content) == []

    def test_multiline_yaml_list(self) -> None:
        content = (
            "---\nname: a\ndescription: A.\nwrite_paths:\n"
            '  - "{{AUTOSKILLIT_TEMP}}/a/"\n'
            '  - "{{AUTOSKILLIT_TEMP}}/b/"\n---\nbody'
        )
        assert _write_paths(content) == [
            "{{AUTOSKILLIT_TEMP}}/a/",
            "{{AUTOSKILLIT_TEMP}}/b/",
        ]

    def test_non_list_returns_empty(self) -> None:
        content = '---\nname: a\ndescription: A.\nwrite_paths: "bad"\n---\nbody'
        assert _write_paths(content) == []
