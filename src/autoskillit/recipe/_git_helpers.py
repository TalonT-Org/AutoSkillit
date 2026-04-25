"""Shared regex helpers for git remote command lint rules."""

from __future__ import annotations

import re

# Matches any line that contains a git command followed by a remote-aware verb.
# Expanded from rules_skill_content.py to include push, merge-base, diff, ls-remote.
_GIT_REMOTE_COMMAND_RE: re.Pattern[str] = re.compile(
    r"\bgit\b.*?\b(?:fetch|rebase|log|show|rev-parse|push|merge-base|diff|ls-remote)\b"
)

# Matches literal 'origin' not immediately preceded by $, {, or - (i.e., not a shell
# variable reference or shell default-value expression like ${REMOTE:-origin}).
_LITERAL_ORIGIN_RE: re.Pattern[str] = re.compile(r"(?<!\$)(?<!\{)(?<!-)\borigin\b")
