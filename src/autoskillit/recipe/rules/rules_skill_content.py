"""Compatibility facade for the decomposed `rules_skill_content` family.

The 15 SKILL.md content semantic rules have been split into four sibling
modules under this name's `rules_skill_content_*.py` family:

  - rules_skill_content_shell_safety        — shell-safety rules
  - rules_skill_content_github_api_safety   — GitHub-API-safety rules
  - rules_skill_content_content_structure   — content-structure rules
  - rules_skill_content_skill_contract      — skill-contract rules

This module exists so that:

  (a) tests patching `autoskillit.recipe.rules.rules_skill_content.X` (via
      `patch.object(_rsc, "_resolve_skill_md", ...)` or via the dotted-string
      `patch("autoskillit.recipe.rules.rules_skill_content.load_bundled_manifest", ...)`)
      continue to resolve. Sibling rule bodies route their `_resolve_skill_md`
      and `load_bundled_manifest` calls through this facade via function-body
      lazy imports so patches take effect.
  (b) cascade stems keyed on `rules_skill_content` continue to fire when the
      facade is touched.
  (c) external imports of `INTERPRETER_WRITE_ALLOWLIST`, `_PSEUDOCODE_ALLOWLIST`,
      and the regex constants continue to resolve at this path.
"""

from __future__ import annotations

from autoskillit.recipe._git_helpers import (
    _GIT_REMOTE_COMMAND_RE,
    _LITERAL_ORIGIN_RE,
)
from autoskillit.recipe._skill_helpers import _resolve_skill_md
from autoskillit.recipe.contracts import load_bundled_manifest

# Side-effect registration: importing each sibling fires its @semantic_rule
# decorators, populating the rule registry exactly once per rule name. The
# aliases use a uniform `_rsc_<category>` prefix so the facade's imports stay
# symmetric with each other; the long form `rules_skill_content_<category>`
# re-exports the same modules.
from autoskillit.recipe.rules import (  # noqa: E402, F401
    rules_skill_content_content_structure as _rsc_content,
)
from autoskillit.recipe.rules import (  # noqa: E402, F401
    rules_skill_content_github_api_safety as _rsc_gh,
)
from autoskillit.recipe.rules import (  # noqa: E402, F401
    rules_skill_content_shell_safety as _rsc_shell,
)
from autoskillit.recipe.rules import (  # noqa: E402, F401
    rules_skill_content_skill_contract as _rsc_contract,
)
from autoskillit.recipe.rules.rules_skill_content_shell_safety import (
    _GIT_GREP_BRE_RE,
    _GREP_BRE_ALTERNATION_RE,
    _POSIX_CHAR_CLASS_RE,
    INTERPRETER_WRITE_ALLOWLIST,
)
from autoskillit.recipe.rules.rules_skill_content_skill_contract import (
    _PSEUDOCODE_ALLOWLIST,
)

__all__ = (
    "INTERPRETER_WRITE_ALLOWLIST",
    "_PSEUDOCODE_ALLOWLIST",
    "_GREP_BRE_ALTERNATION_RE",
    "_GIT_GREP_BRE_RE",
    "_POSIX_CHAR_CLASS_RE",
    "_GIT_REMOTE_COMMAND_RE",
    "_LITERAL_ORIGIN_RE",
    "_resolve_skill_md",
    "load_bundled_manifest",
)
