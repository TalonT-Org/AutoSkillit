#!/usr/bin/env python3
"""PreToolUse hook: validate compose-pr body file before gh pr create.

Prevents PRs from being created on GitHub with missing Closes #N references
when a closing issue is known from the pipeline prep file.

stdlib-only; no autoskillit imports.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path

_HOOKS_DIR = str(Path(__file__).resolve().parent.parent)
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

from _command_classification import _SHELL_OPS  # type: ignore[import-not-found]  # noqa: E402

COMPOSE_PR_BODY_DENY_TRIGGER: str = "compose-pr body missing Closes reference"

_CLOSING_RE = re.compile(
    r"(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?):?\s+#(\d+)",
    re.IGNORECASE,
)

_METADATA_CLOSING_RE = re.compile(r"^-\s*closing_issue:\s*(\S+)", re.MULTILINE)


def _extract_body_file_path(cmd: str) -> str | None:
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        return None

    for i, tok in enumerate(tokens):
        if tok != "gh":
            continue
        if i != 0 and tokens[i - 1] not in _SHELL_OPS:
            continue
        if i + 2 >= len(tokens) or tokens[i + 1] != "pr" or tokens[i + 2] != "create":
            continue
        start = i + 3
        for j in range(start, len(tokens)):
            t = tokens[j]
            if t in _SHELL_OPS:
                break
            if t == "--body-file" and j + 1 < len(tokens) and tokens[j + 1] not in _SHELL_OPS:
                return tokens[j + 1]
            if t.startswith("--body-file="):
                return t.split("=", 1)[1]
        continue
    return None


def _find_closing_issue(project_root: Path) -> str | None:
    prep_dir = project_root / ".autoskillit" / "temp" / "prepare-pr"
    if not prep_dir.is_dir():
        return None
    prep_files = sorted(
        prep_dir.glob("pr_prep_*.md"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not prep_files:
        return None
    try:
        content = prep_files[0].read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = _METADATA_CLOSING_RE.search(content)
    if not m:
        return None
    value = m.group(1).strip().strip('"').strip("'")
    return value if value else None


def _body_has_any_closing_ref(body: str) -> bool:
    return bool(_CLOSING_RE.search(body))


def main() -> None:
    if os.environ.get("AUTOSKILLIT_HEADLESS") != "1":
        sys.exit(0)

    skill_name = os.environ.get("AUTOSKILLIT_SKILL_NAME", "")
    if skill_name != "compose-pr":
        sys.exit(0)

    try:
        data = json.loads(sys.stdin.read())
        tool_input = data.get("tool_input", {})
        cmd = tool_input.get("command", "") or tool_input.get("cmd", "")
    except (json.JSONDecodeError, AttributeError, OSError):
        sys.exit(0)

    if not cmd:
        sys.exit(0)

    body_path_str = _extract_body_file_path(cmd)
    if not body_path_str:
        sys.exit(0)

    body_path = Path(body_path_str)
    if not body_path.is_absolute():
        body_path = Path.cwd() / body_path

    try:
        body_content = body_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        sys.exit(0)

    closing_issue = _find_closing_issue(Path.cwd())
    if not closing_issue:
        sys.exit(0)

    if _body_has_any_closing_ref(body_content):
        sys.exit(0)

    reason = (
        f"{COMPOSE_PR_BODY_DENY_TRIGGER}: the PR body file at {body_path} does not contain "
        f"any GitHub closing reference (e.g., 'Closes #N'). The prep file "
        f"specifies closing_issue: {closing_issue}. "
        f"Add 'Closes #{closing_issue}' to the body file and retry "
        f"the gh pr create command."
    )
    payload = json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }
    )
    sys.stdout.write(payload + "\n")
    sys.stdout.flush()
    sys.exit(0)


if __name__ == "__main__":
    main()
