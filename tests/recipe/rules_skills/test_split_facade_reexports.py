"""Cross-cutting tests for the rules_skill_content facade.

Verifies that the facade (`autoskillit.recipe.rules.rules_skill_content`)
preserves the public-API contracts that external tests and consumers depend on:

  - `_GIT_REMOTE_COMMAND_RE` and `_LITERAL_ORIGIN_RE` are identity-equal to
    the originals in `autoskillit.recipe._git_helpers` (so monkey-patched
    identity checks still pass).
  - `_resolve_skill_md` is monkey-patchable via
    `patch.object(rules_skill_content, "_resolve_skill_md", ...)` and the
    patch takes effect inside rule bodies (because rule bodies import it
    through the facade at call time).
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

import autoskillit.recipe._skill_helpers as _sh
import autoskillit.recipe.rules.rules_skill_content as _rsc
from autoskillit.recipe.io import load_recipe
from autoskillit.recipe.registry import run_semantic_rules

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def test_git_remote_command_re_imported_from_git_helpers() -> None:
    """_GIT_REMOTE_COMMAND_RE must be imported from _git_helpers, not defined locally."""
    import autoskillit.recipe._git_helpers as _gh
    import autoskillit.recipe.rules.rules_skill_content as _rsc  # noqa: F401

    # The regex object in rules_skill_content must be the same object as in _git_helpers
    # (identity check confirms it's an import, not a re-definition).
    assert _rsc._GIT_REMOTE_COMMAND_RE is _gh._GIT_REMOTE_COMMAND_RE
    assert _rsc._LITERAL_ORIGIN_RE is _gh._LITERAL_ORIGIN_RE


def test_rules_pass_ctx_skill_resolver_to_resolve_skill_md(tmp_path: Path) -> None:
    """Rule functions thread ctx.skill_resolver through to _resolve_skill_md."""
    from autoskillit.recipe._analysis import make_validation_context
    from autoskillit.workspace.skills import DefaultSkillResolver

    # Build a minimal recipe with a run_skill step that triggers skill content rules
    skill_dir = tmp_path / "test-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# test-skill\n## Arguments\nNone.\n")

    recipe_yaml = tmp_path / "recipe.yaml"
    recipe_yaml.write_text(
        textwrap.dedent(
            """\
        name: test-recipe
        kitchen_rules:
          - "Use run_skill only."
        steps:
          run_impl:
            tool: run_skill
            with:
              skill_command: "/autoskillit:test-skill"
            on_success: done
          done:
            action: stop
            message: "Done."
        """
        )
    )
    recipe = load_recipe(recipe_yaml)

    # Track whether _resolve_skill_md received a non-None resolver
    from autoskillit.core.types._type_protocols_workspace import SkillResolver

    received_resolvers: list[SkillResolver | None] = []
    original_fn = _rsc._resolve_skill_md

    received_project_roots: list[Path | None] = []

    def tracking_fn(
        skill_name: str,
        *,
        project_root: Path | None,
        resolver: SkillResolver | None = None,
    ) -> Path | None:
        received_resolvers.append(resolver)
        received_project_roots.append(project_root)
        return original_fn(skill_name, project_root=project_root, resolver=resolver)

    resolver = DefaultSkillResolver()
    ctx = make_validation_context(recipe, project_dir=tmp_path, skill_resolver=resolver)

    with (
        patch.object(_rsc, "_resolve_skill_md", tracking_fn),
        patch.object(_sh, "SKILL_SEARCH_DIRS", [tmp_path]),
    ):
        run_semantic_rules(ctx)

    # At least one call should have received the resolver from ctx
    non_none = [r for r in received_resolvers if r is not None]
    assert len(non_none) > 0, (
        "Expected rule functions to pass ctx.skill_resolver to _resolve_skill_md"
    )
    assert all(r is resolver for r in non_none), (
        "Expected every non-None resolver to be the exact instance from ctx"
    )
    assert received_project_roots
    assert all(root == tmp_path for root in received_project_roots)


# ---------------------------------------------------------------------------
# Additional facade re-export tests (added by #4852 split)
# ---------------------------------------------------------------------------


def test_public_allowlist_reexported() -> None:
    """INTERPRETER_WRITE_ALLOWLIST must be importable from the facade as a frozenset."""
    from autoskillit.recipe.rules.rules_skill_content import INTERPRETER_WRITE_ALLOWLIST

    assert isinstance(INTERPRETER_WRITE_ALLOWLIST, frozenset)


def test_pseudocode_allowlist_reexported() -> None:
    """_PSEUDOCODE_ALLOWLIST must be importable from the facade."""
    from autoskillit.recipe.rules.rules_skill_content import _PSEUDOCODE_ALLOWLIST

    assert len(_PSEUDOCODE_ALLOWLIST) > 0, (
        "pseudocode allowlist must be non-empty (rules rely on it for placeholder classification)"
    )
    assert isinstance(_PSEUDOCODE_ALLOWLIST, frozenset)


def test_regex_constants_reexported() -> None:
    """_GREP_BRE_ALTERNATION_RE, _GIT_GREP_BRE_RE, _POSIX_CHAR_CLASS_RE are re-exported."""
    import regex as _regex_module

    from autoskillit.recipe.rules.rules_skill_content import (
        _GIT_GREP_BRE_RE,
        _GREP_BRE_ALTERNATION_RE,
        _POSIX_CHAR_CLASS_RE,
    )

    assert isinstance(_GREP_BRE_ALTERNATION_RE, _regex_module.Pattern)
    assert isinstance(_GIT_GREP_BRE_RE, _regex_module.Pattern)
    assert isinstance(_POSIX_CHAR_CLASS_RE, _regex_module.Pattern)


def test_load_bundled_manifest_patch_path_resolves() -> None:
    """`patch(..., 'load_bundled_manifest', fake)` resolves through the facade namespace.

    The dotted-string patch path is used by 5 test sites that target
    `load_bundled_manifest` re-exported via the facade. Verify it can be
    patched via the facade namespace without raising AttributeError.
    """
    sentinel = {"sentinel": True}
    with patch(
        "autoskillit.recipe.rules.rules_skill_content.load_bundled_manifest",
        return_value=sentinel,
    ) as mocked:
        from autoskillit.recipe.rules.rules_skill_content import load_bundled_manifest

        result = load_bundled_manifest()
        assert result is sentinel
        assert mocked.called
