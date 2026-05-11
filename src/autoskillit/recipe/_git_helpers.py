"""Shared regex helpers for git remote command lint rules."""

from __future__ import annotations

import regex as re

from autoskillit.recipe._rule_helpers import cmd_keyword_pattern

# Matches any line that contains a git command followed by a remote-aware verb.
# Expanded from rules_skill_content.py to include push, merge-base, diff, ls-remote.
_GIT_REMOTE_COMMAND_RE: re.Pattern[str] = cmd_keyword_pattern(
    r"git\b.*?(?:fetch|rebase|log|show|rev-parse|push|merge-base|diff|ls-remote)"
)

# Matches literal 'origin' not immediately preceded by $, {, or - (i.e., not a shell
# variable reference or shell default-value expression like ${REMOTE:-origin}).
_LITERAL_ORIGIN_RE: re.Pattern[str] = re.compile(r"(?<!\$)(?<!\{)(?<!-)\borigin\b")
