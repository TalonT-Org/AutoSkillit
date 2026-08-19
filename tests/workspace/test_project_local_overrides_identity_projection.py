"""Winning project-local override identity binds to projection, rendering, and digests."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from tests.workspace._helpers import _write_effective_skill

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.small]


def test_backend_rendering_uses_the_selected_effective_source(tmp_path, monkeypatch):
    from autoskillit.core import BackendConventions, render_target_skill_command
    from autoskillit.workspace.skills import DefaultSkillResolver

    bundled = tmp_path / "bundled"
    extended = tmp_path / "extended"
    project = tmp_path / "project"
    bundled.mkdir()
    extended.mkdir()
    project.mkdir()
    _write_effective_skill(
        bundled,
        "target",
        capabilities=("open_kitchen",),
        execution_role="session",
        body="bundled",
    )
    winning_path = _write_effective_skill(
        project / ".claude" / "skills",
        "target",
        capabilities=("github_api_write",),
        execution_role="session",
        body="selected override",
    )

    resolver = DefaultSkillResolver()
    monkeypatch.setattr(resolver, "_dir", bundled)
    monkeypatch.setattr(resolver, "_extended_dir", extended)
    selected = resolver.resolve_effective("target", project)

    assert selected is not None
    assert selected.path == winning_path
    assert selected.source_ref is not None
    rendered = render_target_skill_command(
        "/autoskillit:target --flag",
        selected.source_ref,
        BackendConventions(skills_subdir=Path("skills"), skill_sigil="@"),
    )
    assert rendered == "@target --flag"


def test_prepare_skill_projection_authenticates_project_root_not_managed_add_dir(
    tmp_path,
    monkeypatch,
) -> None:
    """Generated skill views are cwd data, never project override authority."""
    from autoskillit.core import PluginLoadMode
    from autoskillit.execution.backends import get_backend
    from autoskillit.workspace import prepare_skill_projection
    from autoskillit.workspace.skills import DefaultSkillResolver

    project_root = tmp_path / "source-project"
    cwd = tmp_path / "generated-home" / "session" / "add-dir"
    cwd.mkdir(parents=True)
    project_override = _write_effective_skill(
        project_root / ".claude" / "skills",
        "process-issues",
        capabilities=("run_skill",),
        execution_role="orchestrator",
        body=(
            "winning project-root body\n"
            "merge {{DEFAULT_BASE_BRANCH}} from {{AUTOSKILLIT_TEMP}}\n"
            'run_skill("/test child")'
        ),
    )
    managed_projection = cwd / ".claude" / "skills" / "process-issues" / "SKILL.md"
    managed_projection.parent.mkdir(parents=True)
    managed_projection.write_text(
        "---\nname: process-issues\ndescription: invalid generated projection\n"
        "semantic_version: 0\nsemantic_requirements: {}\n---\n"
        "wrong execution-cwd body\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")

    backend = get_backend("claude-code")
    plugin_authority, preparation = prepare_skill_projection(
        project_root=project_root,
        cwd=cwd,
        resolver=DefaultSkillResolver(),
        visibility=None,
        default_base_branch=None,
        recipe_packs=None,
        recipe_features=None,
    )

    with plugin_authority.acquire_launch_binding(
        backend=backend,
        load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
    ) as binding:
        assert binding.plugin_dir is not None
        contract = preparation.finalize(backend=backend, binding=binding)
        assert contract.project_root == str(project_root.resolve())
        assert contract.cwd == str(cwd.resolve())
        projected = (binding.plugin_dir / "skills" / "process-issues" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        assert "winning project-root body" in projected
        assert "wrong execution-cwd body" not in projected
        assert "{{DEFAULT_BASE_BRANCH}}" not in projected
        assert "{{AUTOSKILLIT_TEMP}}" not in projected
        assert (
            contract.projected_digests["process-issues"]
            == hashlib.sha256(projected.encode()).hexdigest()
        )
    assert binding.closed

    # 2.2's resolution-boundary containment supersedes the old fail-closed
    # pin here: an invalid real project-root override no longer poisons
    # composition. It falls through to the valid bundled `process-issues`
    # twin (recorded as a catalog exclusion) instead of raising —
    # `skill_projection.py:239` is one of the five crash sites the plan
    # names as needing no individual guard, since post-2.2 they simply
    # never see an invalid candidate reach them.
    project_override.write_text(
        "---\nname: process-issues\ndescription: invalid user override\n"
        "semantic_version: 0\nsemantic_requirements: {}\n---\n"
        "invalid real project override\n",
        encoding="utf-8",
    )
    plugin_authority, preparation = prepare_skill_projection(
        project_root=project_root,
        cwd=cwd,
        resolver=DefaultSkillResolver(),
        visibility=None,
        default_base_branch=None,
        recipe_packs=None,
        recipe_features=None,
    )

    assert preparation.catalog is not None
    exclusions = preparation.catalog.exclusions
    assert any(exclusion.name == "process-issues" for exclusion in exclusions)

    with plugin_authority.acquire_launch_binding(
        backend=backend,
        load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
    ) as binding:
        assert binding.plugin_dir is not None
        preparation.finalize(backend=backend, binding=binding)
        projected = (binding.plugin_dir / "skills" / "process-issues" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        assert "invalid real project override" not in projected
        assert "winning project-root body" not in projected
    assert binding.closed


def test_winning_override_identity_policy_projection_and_digests_are_atomic(
    tmp_path, monkeypatch
) -> None:
    """No bundled SkillInfo can leak after a project override wins resolution."""
    import autoskillit.workspace.skills as skills_module
    from autoskillit.core import SkillExecutionRole, render_target_skill_command
    from autoskillit.execution.backends import ClaudeCodeBackend
    from autoskillit.workspace import (
        SkillProjectionContext,
        build_skill_projection_binding,
        project_agent_skill_document,
    )
    from autoskillit.workspace.skills import DefaultSkillResolver

    bundled = tmp_path / "bundled"
    extended = tmp_path / "extended"
    project = tmp_path / "project"
    bundled.mkdir()
    extended.mkdir()
    project.mkdir()
    _write_effective_skill(
        bundled,
        "target",
        capabilities=("open_kitchen",),
        execution_role="session",
        body="bundled sentinel body",
    )
    override_path = _write_effective_skill(
        project / ".claude" / "skills",
        "target",
        capabilities=("github_api_write", "test_check"),
        execution_role="session",
        body=(
            "winning override body\ngh issue edit 42 --body-file report.md\nCall `test_check()`."
        ),
    )
    source_before = override_path.read_bytes()

    resolver = DefaultSkillResolver()
    monkeypatch.setattr(resolver, "_dir", bundled)
    monkeypatch.setattr(resolver, "_extended_dir", extended)
    monkeypatch.setattr(skills_module, "_LIST_ALL_CACHE", None)
    monkeypatch.setattr(skills_module, "_LIST_ALL_CACHE_KEY", None)
    monkeypatch.setattr(
        resolver,
        "resolve",
        lambda _name: pytest.fail("bundled resolver lookup leaked into effective flow"),
    )

    invocation = resolver.resolve_invocation(
        "target",
        project,
        SkillExecutionRole.SESSION,
    )
    backend = ClaudeCodeBackend()
    context = SkillProjectionContext(cwd=tmp_path, invocation=invocation, backend=backend)
    document = project_agent_skill_document(invocation.root, context)
    contract = build_skill_projection_binding(context)

    assert invocation.root.path == override_path
    assert invocation.root.execution_role is SkillExecutionRole.SESSION
    assert invocation.capability_union == frozenset({"github_api_write", "test_check"})
    assert "winning override body" in invocation.root.canonical_content
    assert "bundled sentinel body" not in invocation.root.canonical_content
    assert "winning override body" in document.content
    assert contract.source_identities["target"]["origin"] == "project_local"
    assert contract.canonical_digests["target"] == hashlib.sha256(source_before).hexdigest()
    assert (
        contract.projected_digests["target"]
        == hashlib.sha256(document.content.encode()).hexdigest()
    )
    assert (
        render_target_skill_command("/autoskillit:target", invocation.root.source_ref) == "/target"
    )
    assert override_path.read_bytes() == source_before
