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

from _command_classification import (  # type: ignore[import-not-found]  # noqa: E402
    _SHELL_CONTROL_WORDS,
    _SHELL_OPS,
)

# Tokens that mark the boundary of a fresh command — either a shell operator
# (already in _SHELL_OPS) or a shell control word like `do`/`then` that
# introduces a new command inside a compound construct (loops, conditionals).
_GH_COMMAND_BOUNDARY: frozenset[str] = _SHELL_OPS | _SHELL_CONTROL_WORDS

COMPOSE_PR_BODY_DENY_TRIGGER: str = "compose-pr body missing Closes reference"

_CLOSING_RE = re.compile(
    r"(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?):?\s+#(\d+)",
    re.IGNORECASE,
)

_METADATA_CLOSING_RE = re.compile(r"^-[ \t]*closing_issue:[ \t]*(\S+)", re.MULTILINE)

_SIMPLE_ASSIGN_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
_VAR_REF_RE = re.compile(r"^\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?$")
_UNSAFE_ASSIGN_VALUE_RE = re.compile(r"[`()|&;]")
_NESTED_VAR_RE = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?")

# Keywords that open a nesting level (loop/conditional/case bodies).
_DEPTH_INCREASE: frozenset[str] = frozenset({"while", "for", "until", "if", "case"})
# Keywords that close a nesting level.
_DEPTH_DECREASE: frozenset[str] = frozenset({"done", "fi", "esac"})


def _collect_depth0_assignments(tokens: list[str], before_index: int) -> dict[str, str]:
    """Collect simple variable assignments at nesting depth 0 before *before_index*.

    Only assignments with safe values (no command substitution, backticks, or
    shell operators in the value) are included. Simple $VAR references in values
    are left as-is for downstream resolution.
    """
    assignments: dict[str, str] = {}
    depth = 0
    for i, tok in enumerate(tokens):
        if i >= before_index:
            break
        if tok in _DEPTH_INCREASE:
            depth += 1
        elif tok in _DEPTH_DECREASE:
            if depth > 0:
                depth -= 1
        elif depth == 0:
            m = _SIMPLE_ASSIGN_RE.match(tok)
            if m:
                name, value = m.group(1), m.group(2)
                if not _UNSAFE_ASSIGN_VALUE_RE.search(value):
                    assignments[name] = value
    return assignments


def _resolve_nested_vars(value: str, assignments: dict[str, str]) -> str | None:
    """Expand simple $VAR references in *value* from *assignments*.

    Returns the expanded string, or None if any variable cannot be resolved
    (fail-open: caller should treat None as "path unknown").
    """
    result_parts: list[str] = []
    i = 0
    while i < len(value):
        if value[i] == "$":
            m = _NESTED_VAR_RE.match(value, i)
            if not m:
                return None
            var_name = m.group(1)
            if var_name not in assignments:
                return None
            nested_val = assignments[var_name]
            if "$" in nested_val:
                return None
            result_parts.append(nested_val)
            i = m.end()
        else:
            result_parts.append(value[i])
            i += 1
    return "".join(result_parts)


def _resolve_variable_body_path(raw_token: str, tokens: list[str], gh_index: int) -> str | None:
    """Resolve a $VAR or ${VAR} body-file token from depth-0 assignments before *gh_index*.

    Returns the resolved path string, or None if resolution fails (fail-open).
    """
    m = _VAR_REF_RE.match(raw_token)
    if not m:
        return None
    var_name = m.group(1)

    assignments = _collect_depth0_assignments(tokens, gh_index)
    if var_name not in assignments:
        return None

    raw_value = assignments[var_name]
    if "$" not in raw_value:
        return raw_value
    return _resolve_nested_vars(raw_value, assignments)


def _preprocess_newlines(cmd: str) -> str:
    """Replace bare (unquoted) newlines with ' ; ' to preserve command boundaries.

    In shell a bare newline terminates a command just like ';'. shlex.split()
    collapses newlines to whitespace, so a later command's --body-file can
    bleed into an earlier gh pr create that has none. Replacing unquoted
    newlines before tokenisation inserts a proper ';' separator.
    """
    result: list[str] = []
    in_single = False
    in_double = False
    i = 0
    while i < len(cmd):
        c = cmd[i]
        if c == "\\" and not in_single and i + 1 < len(cmd):
            result.append(c)
            result.append(cmd[i + 1])
            i += 2
            continue
        if c == "'" and not in_double:
            in_single = not in_single
        elif c == '"' and not in_single:
            in_double = not in_double
        elif c == "\n" and not in_single and not in_double:
            result.append(" ; ")
            i += 1
            continue
        result.append(c)
        i += 1
    return "".join(result)


def _extract_body_file_path(cmd: str) -> str | None:
    try:
        tokens = shlex.split(_preprocess_newlines(cmd))
    except ValueError:
        return None

    for i, tok in enumerate(tokens):
        if tok != "gh":
            continue
        if i != 0 and tokens[i - 1] not in _GH_COMMAND_BOUNDARY:
            continue
        if i + 2 >= len(tokens) or tokens[i + 1] != "pr" or tokens[i + 2] != "create":
            continue
        start = i + 3
        for j in range(start, len(tokens)):
            t = tokens[j]
            if t in _SHELL_OPS:
                break
            if t == "--body-file" and j + 1 < len(tokens) and tokens[j + 1] not in _SHELL_OPS:
                raw = tokens[j + 1]
                if raw.startswith("$"):
                    return _resolve_variable_body_path(raw, tokens, i)
                return raw
            if t.startswith("--body-file="):
                raw = t.split("=", 1)[1]
                if raw.startswith("$"):
                    return _resolve_variable_body_path(raw, tokens, i)
                return raw
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
