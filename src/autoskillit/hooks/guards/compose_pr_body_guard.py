#!/usr/bin/env python3
"""PreToolUse hook: validate exact PR-body provenance before ``gh pr create``.

Every create issued by the PR skills must name a body whose sibling metadata
binds the exact body bytes to its canonical source issue identity.

stdlib-only; no autoskillit imports.
"""

from __future__ import annotations

import hashlib
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
from _hook_payload import (  # type: ignore[import-not-found]  # noqa: E402
    parse_hook_command,
    resolve_state_root,
)

# Tokens that mark the boundary of a fresh command — either a shell operator
# (already in _SHELL_OPS) or a shell control word like `do`/`then` that
# introduces a new command inside a compound construct (loops, conditionals).
_GH_COMMAND_BOUNDARY: frozenset[str] = _SHELL_OPS | _SHELL_CONTROL_WORDS

COMPOSE_PR_BODY_DENY_TRIGGER: str = "PR body provenance validation failed"

_ORDINARY_METADATA_FIELDS = frozenset(
    {"schema_version", "body_sha256", "closing_issue", "source_issue_url"}
)
_INTEGRATION_METADATA_FIELDS = frozenset({"schema_version", "body_sha256", "source_issue_urls"})
_ISSUE_URL_RE = re.compile(r"^https://github\.com/[^/\s]+/[^/\s]+/issues/([1-9]\d*)$")
_CLOSING_URL_RE = re.compile(
    r"(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?):?\s+"
    r"(https://github\.com/[^/\s]+/[^/\s]+/issues/[1-9]\d*)"
    r"(?=$|\s|[.,;)])",
    re.IGNORECASE,
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

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
        elif c in {"\n", ";"} and not in_single and not in_double:
            result.append(" ; ")
            i += 1
            continue
        result.append(c)
        i += 1
    return "".join(result)


def _extract_create_body_paths(cmd: str) -> list[str | None] | None:
    try:
        tokens = shlex.split(_preprocess_newlines(cmd))
    except ValueError:
        return None

    body_paths: list[str | None] = []
    for i, tok in enumerate(tokens):
        if tok != "gh":
            continue
        if i != 0 and tokens[i - 1] not in _GH_COMMAND_BOUNDARY:
            continue
        if i + 2 >= len(tokens) or tokens[i + 1] != "pr" or tokens[i + 2] != "create":
            continue
        body_path: str | None = None
        start = i + 3
        for j in range(start, len(tokens)):
            t = tokens[j]
            if t in _GH_COMMAND_BOUNDARY:
                break
            if (
                t == "--body-file"
                and j + 1 < len(tokens)
                and tokens[j + 1] not in _GH_COMMAND_BOUNDARY
            ):
                raw = tokens[j + 1]
                if raw.startswith("$"):
                    body_path = _resolve_variable_body_path(raw, tokens, i)
                else:
                    body_path = raw
                break
            if t.startswith("--body-file="):
                raw = t.split("=", 1)[1]
                if raw.startswith("$"):
                    body_path = _resolve_variable_body_path(raw, tokens, i)
                else:
                    body_path = raw
                break
        body_paths.append(body_path)
    return body_paths


def _has_closing_url(body: str, issue_url: str) -> bool:
    return issue_url in _CLOSING_URL_RE.findall(body)


def _read_bound_pair(body_path: Path) -> tuple[str, dict[str, object]] | None:
    if not body_path.is_file():
        return None
    metadata_path = body_path.with_suffix(".metadata.json")
    if not metadata_path.is_file():
        return None
    try:
        body_bytes = body_path.read_bytes()
        body = body_bytes.decode("utf-8")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(metadata, dict):
        return None
    digest = metadata.get("body_sha256")
    if (
        not isinstance(digest, str)
        or not _SHA256_RE.fullmatch(digest)
        or digest != hashlib.sha256(body_bytes).hexdigest()
    ):
        return None
    return body, metadata


def _valid_ordinary_pair(body: str, metadata: dict[str, object]) -> bool:
    if set(metadata) != _ORDINARY_METADATA_FIELDS or metadata.get("schema_version") != 1:
        return False
    closing_issue = metadata.get("closing_issue")
    issue_url = metadata.get("source_issue_url")
    if closing_issue is None or issue_url is None:
        return closing_issue is None and issue_url is None
    if isinstance(closing_issue, bool) or not isinstance(closing_issue, int):
        return False
    if not isinstance(issue_url, str):
        return False
    match = _ISSUE_URL_RE.fullmatch(issue_url)
    return bool(
        match and int(match.group(1)) == closing_issue and _has_closing_url(body, issue_url)
    )


def _valid_integration_pair(body: str, metadata: dict[str, object]) -> bool:
    if set(metadata) != _INTEGRATION_METADATA_FIELDS or metadata.get("schema_version") != 1:
        return False
    issue_urls = metadata.get("source_issue_urls")
    if not isinstance(issue_urls, list) or not all(isinstance(url, str) for url in issue_urls):
        return False
    if issue_urls != sorted(set(issue_urls)):
        return False
    body_issue_urls = sorted(set(_CLOSING_URL_RE.findall(body)))
    return body_issue_urls == issue_urls and all(
        _ISSUE_URL_RE.fullmatch(issue_url) for issue_url in issue_urls
    )


def _deny(reason: str) -> None:
    payload = json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (f"{COMPOSE_PR_BODY_DENY_TRIGGER}: {reason}"),
            }
        }
    )
    sys.stdout.write(payload + "\n")
    sys.stdout.flush()


def main() -> None:
    if os.environ.get("AUTOSKILLIT_HEADLESS") != "1":
        sys.exit(0)

    skill_name = os.environ.get("AUTOSKILLIT_SKILL_NAME", "")
    if skill_name not in {"compose-pr", "open-integration-pr"}:
        sys.exit(0)

    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, AttributeError, OSError):
        sys.exit(0)

    parsed = parse_hook_command(data)
    cmd = parsed.command or ""

    if not cmd:
        sys.exit(0)

    body_path_strs = _extract_create_body_paths(cmd)
    if body_path_strs is None or not body_path_strs:
        sys.exit(0)

    project_root = resolve_state_root(parsed.payload_cwd)
    for body_path_str in body_path_strs:
        if not body_path_str or body_path_str == "-":
            _deny("every gh pr create must name a resolvable --body-file")
            sys.exit(0)
        body_path = Path(body_path_str)
        if not body_path.is_absolute():
            body_path = project_root / body_path
        pair = _read_bound_pair(body_path)
        if pair is None:
            _deny(f"{body_path} and its sibling metadata must be readable and exact")
            sys.exit(0)
        body, metadata = pair
        valid = (
            _valid_ordinary_pair(body, metadata)
            if skill_name == "compose-pr"
            else _valid_integration_pair(body, metadata)
        )
        if not valid:
            _deny(f"{body_path} does not match its required provenance schema")
            sys.exit(0)
    sys.exit(0)


if __name__ == "__main__":
    main()
