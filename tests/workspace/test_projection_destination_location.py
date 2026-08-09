"""Containment over a write destination is about *location*, not current content.

`Path.resolve()` follows a final-component symlink. Applied to a write
destination that answers the wrong question — "what does this currently point
at?" instead of "where am I about to write?" — and when the destination happened
to be a symlink into the source root, the projection refused to overwrite it.
The symlink-tolerant replacement code fifteen lines further down was unreachable.

Both halves are tested here: the guard must stop rejecting a symlinked
destination, and it must keep rejecting a destination that genuinely lives
inside the source root.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import DIRECT_PREFIX, SkillContractError, destination_location

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.small]


class TestDestinationLocation:
    def test_returns_the_links_own_location_for_a_symlinked_destination(
        self, tmp_path: Path
    ) -> None:
        target = tmp_path / "target"
        target.mkdir()
        link = tmp_path / "link"
        link.symlink_to(target)

        assert link.resolve() == target, "precondition: resolve() follows the final component"
        assert destination_location(link) == tmp_path / "link"

    def test_resolves_symlinked_ancestors(self, tmp_path: Path) -> None:
        """Only the *final* component is left unfollowed — ancestors still resolve."""
        real = tmp_path / "real"
        real.mkdir()
        linked_parent = tmp_path / "parent"
        linked_parent.symlink_to(real)

        assert destination_location(linked_parent / "child") == real / "child"

    def test_nonexistent_destination_is_fine(self, tmp_path: Path) -> None:
        assert destination_location(tmp_path / "not-yet") == tmp_path / "not-yet"


def _catalog():
    from autoskillit.core import SkillExecutionRole, SkillSource
    from autoskillit.workspace import (
        DefaultSkillResolver,
        EffectiveSkillCatalog,
        SkillCatalogEntry,
    )

    skills = tuple(s for s in DefaultSkillResolver().list_all() if s.source is SkillSource.BUNDLED)
    return EffectiveSkillCatalog(
        skills=tuple(SkillCatalogEntry.from_skill_info(s) for s in skills),
        execution_role=SkillExecutionRole.SESSION,
    )


def _context(catalog, cwd: Path):
    from autoskillit.workspace import SkillProjectionContext

    return SkillProjectionContext(cwd=cwd, catalog=catalog)


class TestMaterializeSanitizedPluginRoot:
    def test_succeeds_over_a_destination_symlinked_to_the_source_root(
        self, tmp_path: Path
    ) -> None:
        """F1 reproduction: this raised SkillContractError before the fix."""
        from autoskillit.core import pkg_root
        from autoskillit.workspace import materialize_sanitized_plugin_root

        destination = tmp_path / "plugins" / "autoskillit"
        destination.parent.mkdir(parents=True)
        destination.symlink_to(pkg_root())

        catalog = _catalog()
        materialize_sanitized_plugin_root(
            pkg_root(),
            destination,
            catalog,
            _context(catalog, tmp_path),
            mcp_tool_prefix=DIRECT_PREFIX,
        )

        assert destination.is_dir()
        assert not destination.is_symlink(), "the stale symlink must be replaced, not respected"
        assert (destination / "skills").is_dir()

    def test_still_raises_when_an_ancestor_is_a_symlink_into_the_source_root(
        self, tmp_path: Path
    ) -> None:
        """Regression guard: the fix narrows the predicate, it does not defeat it.

        Passed before the fix and must keep passing. Without this case, "we
        stopped resolving the destination" would look indistinguishable from
        "we stopped checking containment".
        """
        from autoskillit.core import pkg_root
        from autoskillit.workspace import materialize_sanitized_plugin_root

        linked_parent = tmp_path / "plugins"
        linked_parent.symlink_to(pkg_root())
        destination = linked_parent / "autoskillit"

        catalog = _catalog()
        with pytest.raises(SkillContractError, match="outside its source root"):
            materialize_sanitized_plugin_root(
                pkg_root(),
                destination,
                catalog,
                _context(catalog, tmp_path),
                mcp_tool_prefix=DIRECT_PREFIX,
            )


class TestMaterializeAgentSkillTree:
    """The same predicate lives at a second site and carried the same defect."""

    def test_succeeds_over_a_symlinked_destination(self, tmp_path: Path) -> None:
        from autoskillit.workspace import materialize_agent_skill_tree

        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        destination = tmp_path / "skills"
        destination.symlink_to(elsewhere)

        catalog = _catalog()
        documents = materialize_agent_skill_tree(destination, catalog, _context(catalog, tmp_path))

        assert documents
        assert destination.is_dir() and not destination.is_symlink()

    def test_still_rejects_a_destination_containing_a_canonical_source(
        self, tmp_path: Path
    ) -> None:
        from autoskillit.core import ResolvedSkillAuthority, SkillSource, pkg_root
        from autoskillit.workspace import DefaultSkillResolver, materialize_agent_skill_tree

        resolved = [
            s
            for s in DefaultSkillResolver().list_all()
            if s.source is SkillSource.BUNDLED and isinstance(s, ResolvedSkillAuthority)
        ]
        assert resolved, "precondition: at least one resolved bundled skill with a path"

        catalog = _catalog()
        with pytest.raises(SkillContractError, match="contains canonical source"):
            materialize_agent_skill_tree(pkg_root(), resolved[:1], _context(catalog, tmp_path))
