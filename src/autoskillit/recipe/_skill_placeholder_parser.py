"""Backward-compat shim for ``recipe/_skill_placeholder_parser.py``.

Real implementation: ``autoskillit.recipe.helpers._skill_placeholder_parser`` (#4671 D).
Preserves old import path ``autoskillit.recipe._skill_placeholder_parser``.
"""

from __future__ import annotations

from autoskillit.recipe.helpers._skill_placeholder_parser import (
    _BANNED_CONTENT_SUFFIX_RE,
    _CONTENT_VAR_SIGNAL_RE,
    _DYNAMIC_WRITE_VAR_RE,
    _PROSE_GRAPHQL_EXECUTION_RE,
    _STEP_RE,
    _VRULE_RE,
    _WRITE_SCOPE_RE,
    annotations,
    extract_bash_blocks,
    extract_bash_placeholders,
    extract_blockquote_placeholders,
    extract_blockquote_sections,
    extract_declared_ingredients,
    extract_fenced_blocks,
    extract_git_commands,
    extract_graphql_blocks,
    extract_never_block,
    extract_python_blocks,
    extract_sections,
    extract_step_sections,
    extract_validation_rule_block,
    extract_write_path_declarations,
    has_dynamic_write_path,
    has_prose_graphql_execution,
    shell_vars_assigned,
)

__all__ = [
    "_BANNED_CONTENT_SUFFIX_RE",
    "_CONTENT_VAR_SIGNAL_RE",
    "_DYNAMIC_WRITE_VAR_RE",
    "_PROSE_GRAPHQL_EXECUTION_RE",
    "_STEP_RE",
    "_VRULE_RE",
    "_WRITE_SCOPE_RE",
    "annotations",
    "extract_bash_blocks",
    "extract_bash_placeholders",
    "extract_blockquote_placeholders",
    "extract_blockquote_sections",
    "extract_declared_ingredients",
    "extract_fenced_blocks",
    "extract_git_commands",
    "extract_graphql_blocks",
    "extract_never_block",
    "extract_python_blocks",
    "extract_sections",
    "extract_step_sections",
    "extract_validation_rule_block",
    "extract_write_path_declarations",
    "has_dynamic_write_path",
    "has_prose_graphql_execution",
    "shell_vars_assigned",
]
