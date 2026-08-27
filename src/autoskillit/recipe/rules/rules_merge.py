"""Semantic rules for merge_worktree — thin facade for sibling rule modules.

Side-effect imports below register every rule family via its module's
``@semantic_rule`` decorators. Module-level aliases re-export symbols that
existing tests reach through ``rules_merge.*``.
"""

from __future__ import annotations

from autoskillit.recipe.rules import rules_merge_enrollment as _enrollment  # noqa: F401
from autoskillit.recipe.rules import rules_merge_guards as _guards
from autoskillit.recipe.rules import rules_merge_push_symmetry as _push_symmetry  # noqa: F401
from autoskillit.recipe.rules import rules_merge_routing as _routing
from autoskillit.recipe.rules import rules_merge_wait as _wait  # noqa: F401

_RECOVERABLE_FAILED_STEPS = _routing._RECOVERABLE_FAILED_STEPS
_TERMINAL_FAILED_STEPS = _routing._TERMINAL_FAILED_STEPS
_MERGE_FAILURE_DOMAINS = _routing._MERGE_FAILURE_DOMAINS
_REQUIRED_RECOVERY_CLASS = _routing._REQUIRED_RECOVERY_CLASS
_is_commit_guard = _guards._is_commit_guard
_classify_recovery_class = _routing._classify_recovery_class
