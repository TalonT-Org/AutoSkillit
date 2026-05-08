"""Shared anti-confirmation regex for contract tests — mirrors production regex."""

from __future__ import annotations

import re

ANTI_CONFIRM_RE = re.compile(
    r"(?:never|do\s+not|must\s+not|prohibited)"
    r"[^\n]{0,80}"
    r"(?:ask|confirm|pause|AskUserQuestion"
    r"|wait\s+for\s+[^\n]{0,30}(?:user|human|permission|approval|confirmation))"
    r"|(?:proceed|dispatch|continue|start)"
    r"[^\n]{0,60}"
    r"(?:immediately|without[^\n]{0,30}(?:asking|confirming|pausing|waiting))",
    re.IGNORECASE,
)
