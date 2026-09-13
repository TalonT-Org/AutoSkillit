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
import sys
from pathlib import Path

_HOOKS_DIR = str(Path(__file__).resolve().parent.parent)
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)
_RUNTIME_DIR = str(Path(_HOOKS_DIR) / "_runtime")
if _RUNTIME_DIR not in sys.path:
    sys.path.insert(0, _RUNTIME_DIR)


from _command_classification import (  # type: ignore[import-not-found]  # noqa: E402
    _command_position_candidate_spans,
    command_verb_and_args,
    extract_shell_command_payloads,
    tokenize_command_segments,
)
from _hook_payload import (  # type: ignore[import-not-found]  # noqa: E402
    parse_hook_command,
    resolve_state_root,
)

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


def _collect_depth0_assignments(
    segments: list[list[str]], before_segment_index: int
) -> dict[str, str]:
    """Collect simple variable assignments at nesting depth 0 before *before_segment_index*.

    *segments* is one evaluated payload's own ordered segment list (rectify
    #4941 Part B) -- never a flattened mix of an outer command and a nested
    `bash -c`/heredoc/pipe body's segments, so a $VAR lookup for one payload
    can never resolve from a sibling payload's assignment. Only assignments
    with safe values (no command substitution, backticks, or shell operators
    in the value) are included. Simple $VAR references in values are left
    as-is for downstream resolution. Depth tracks loop/conditional bodies
    (while/for/until/if/case ... done/fi/esac) across segment boundaries the
    same way the token-level walk this replaces did.
    """
    assignments: dict[str, str] = {}
    depth = 0
    for segment in segments[:before_segment_index]:
        for tok in segment:
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


def _resolve_variable_body_path(
    raw_token: str, segments: list[list[str]], gh_segment_index: int
) -> str | None:
    """Resolve a $VAR or ${VAR} body-file token from depth-0 assignments in the

    same payload's segments before *gh_segment_index*. Returns the resolved
    path string, or None if resolution fails (fail-open).
    """
    m = _VAR_REF_RE.match(raw_token)
    if not m:
        return None
    var_name = m.group(1)

    assignments = _collect_depth0_assignments(segments, gh_segment_index)
    if var_name not in assignments:
        return None

    raw_value = assignments[var_name]
    if "$" not in raw_value:
        return raw_value
    return _resolve_nested_vars(raw_value, assignments)


def _iter_evaluated_payload_segments(command: str) -> list[list[list[str]]] | None:
    """Return each evaluated payload's own ordered segment list.

    The outer command is always the first entry. Each recursively
    discovered SHELL payload (a `bash -c`/`eval` argument, or a
    heredoc/herestring/pipe body bound to a shell consumer) is tokenized
    independently and appended as its own entry -- an inert heredoc body
    (bound to a non-executing consumer like `cat`) is never a SHELL payload
    and so is never visited here, closing the false positive where the
    old newline-rewriting pre-pass corrupted an inert body's own newlines
    into command boundaries and scanned its prose as real commands.
    Returns `None` when the outer command cannot be tokenized (fail-open,
    matching the historic `shlex.ValueError -> None` contract).
    """
    outer_segments = tokenize_command_segments(command)
    if not outer_segments and command.strip():
        return None

    payload_segment_lists: list[list[list[str]]] = [outer_segments]
    seen: set[str] = {command}
    queue: list[str] = list(extract_shell_command_payloads(command))
    while queue:
        payload = queue.pop(0)
        if payload in seen:
            continue
        seen.add(payload)
        payload_segment_lists.append(tokenize_command_segments(payload))
        queue.extend(extract_shell_command_payloads(payload))
    return payload_segment_lists


def _body_path_from_gh_create_segment(
    segment: list[str], segments: list[list[str]], segment_index: int
) -> str | None:
    """Return the --body-file value of one `gh pr create` segment occurrence."""
    _verb, args = command_verb_and_args(segment)
    for j, t in enumerate(args[2:], start=2):
        if t == "--body-file" and j + 1 < len(args):
            raw = args[j + 1]
            return (
                _resolve_variable_body_path(raw, segments, segment_index)
                if raw.startswith("$")
                else raw
            )
        if t.startswith("--body-file="):
            raw = t.split("=", 1)[1]
            return (
                _resolve_variable_body_path(raw, segments, segment_index)
                if raw.startswith("$")
                else raw
            )
    return None


def _extract_create_body_paths(cmd: str) -> list[str | None] | None:
    """Return each `gh pr create --body-file` occurrence's value, or None.

    Reads *cmd* via `_iter_evaluated_payload_segments` (rectify #4941 Part B)
    rather than a private newline-rewrite + flat `shlex.split` pass: each
    evaluated payload is tokenized independently, so a $VAR lookup for one
    payload's occurrence is resolved only from that same payload's own
    assignments -- never a sibling payload's, even one that runs earlier in
    the outer command. `None` propagates when the outer command cannot be
    tokenized (fail-open, unchanged contract).
    """
    payload_segment_lists = _iter_evaluated_payload_segments(cmd)
    if payload_segment_lists is None:
        return None

    body_paths: list[str | None] = []
    for segments in payload_segment_lists:
        for segment_index, segment in enumerate(segments):
            for start, end in _command_position_candidate_spans(segment):
                verb, args = command_verb_and_args(segment[start:end])
                if verb != "gh" or args[:2] != ["pr", "create"]:
                    continue
                body_paths.append(
                    _body_path_from_gh_create_segment(segment[start:end], segments, segment_index)
                )
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
    if closing_issue is None:
        return issue_url is None
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
    return body_issue_urls == issue_urls


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
    if not body_path_strs:
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
