"""Phase 2 tests: session_skills module — namespace rewriting for ephemeral SKILL.md content."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from autoskillit.core import ClaudeDirectoryConventions
from autoskillit.workspace.session_skills import (
    DefaultSessionSkillManager,
    SkillsDirectoryProvider,
    _rewrite_skill_namespace_refs,
)
from autoskillit.workspace.skills import DefaultSkillResolver

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.medium]


def _make_resolver() -> DefaultSkillResolver:
    return DefaultSkillResolver()


def test_rewrite_bundled_extended_ref_is_stripped() -> None:
    resolver = _make_resolver()
    content = "Use the /autoskillit:mermaid skill to render diagrams."
    result = _rewrite_skill_namespace_refs(content, resolver)
    assert "/autoskillit:mermaid" not in result
    assert "/mermaid" in result


def test_rewrite_bundled_ref_is_preserved() -> None:
    resolver = _make_resolver()
    content = "Run /autoskillit:open-kitchen to start."
    result = _rewrite_skill_namespace_refs(content, resolver)
    assert "/autoskillit:open-kitchen" in result


def test_rewrite_unknown_ref_is_preserved() -> None:
    resolver = _make_resolver()
    content = "Call /autoskillit:nonexistent-skill for help."
    result = _rewrite_skill_namespace_refs(content, resolver)
    assert "/autoskillit:nonexistent-skill" in result


def test_rewrite_already_bare_ref_unaffected() -> None:
    resolver = _make_resolver()
    content = "Use /make-plan to plan the work."
    result = _rewrite_skill_namespace_refs(content, resolver)
    assert result == content


def test_rewrite_multiple_refs_in_document() -> None:
    resolver = _make_resolver()
    content = (
        "Step 1: /autoskillit:mermaid to draw.\n"
        "Step 2: /autoskillit:open-kitchen to open.\n"
        "Step 3: /autoskillit:make-plan to plan.\n"
    )
    result = _rewrite_skill_namespace_refs(content, resolver)
    assert "/autoskillit:mermaid" not in result
    assert "/mermaid" in result
    assert "/autoskillit:open-kitchen" in result
    assert "/autoskillit:make-plan" not in result
    assert "/make-plan" in result


def test_rewrite_refs_inside_code_fence() -> None:
    resolver = _make_resolver()
    content = "```\nSkill tool: /autoskillit:mermaid\n```"
    result = _rewrite_skill_namespace_refs(content, resolver)
    assert "/autoskillit:mermaid" not in result
    assert "/mermaid" in result


def test_activate_with_deps_materialised_content_has_namespace_rewritten(
    tmp_path: Path,
) -> None:
    """Materialised absent skill has /autoskillit: refs rewritten to bare names."""
    from tests._helpers import make_skills_config, make_test_config

    provider = SkillsDirectoryProvider()
    mgr = DefaultSessionSkillManager(provider, ephemeral_root=tmp_path)
    config = make_test_config(
        skills=make_skills_config(
            tier1=["open-kitchen", "close-kitchen"],
            tier2=["mermaid"],
            tier3=[],
        )
    )
    mgr.init_session(
        "session-ns-activate",
        cook_session=False,
        config=config,
    )

    mermaid_md = (
        tmp_path
        / "session-ns-activate"
        / ClaudeDirectoryConventions.ADD_DIR_SKILLS_SUBDIR
        / "mermaid"
        / "SKILL.md"
    )
    assert not mermaid_md.exists()

    result = mgr.activate_skill_deps("session-ns-activate", "mermaid")
    assert result is True
    assert mermaid_md.exists()

    content = mermaid_md.read_text()
    prefixed_re = re.compile(r"/autoskillit:([a-z][a-z0-9-]*)")
    from autoskillit.core import SkillSource
    from autoskillit.workspace.skills import DefaultSkillResolver as _Resolver

    resolver = _Resolver()
    for m in prefixed_re.finditer(content):
        ref_name = m.group(1)
        info = resolver.resolve(ref_name)
        assert info is None or info.source != SkillSource.BUNDLED_EXTENDED, (
            f"Materialised mermaid/SKILL.md still contains /autoskillit:{ref_name} "
            f"which is BUNDLED_EXTENDED — namespace was not rewritten"
        )
