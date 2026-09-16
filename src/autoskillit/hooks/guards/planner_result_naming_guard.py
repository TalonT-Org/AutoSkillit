#!/usr/bin/env python3
"""PreToolUse hook — blocks Write/Edit calls that produce non-canonical planner result filenames.

Planner result files must follow strict naming conventions:
  - Phases:     P{N}_result.json     (e.g. P1_result.json, P12_result.json)
  - Assignments: P{N}-A{N}_result.json  (e.g. P1-A1_result.json, P3-A12_result.json)
  - Work Packages: P{N}-A{N}-WP{N}_result.json  (e.g. P1-A1-WP1_result.json)

An LLM may emit a non-canonical ID (e.g. "WP2a" or "A2b"), producing a file like
P1-A1-WP2a_result.json. This guard intercepts Write/Edit calls in planner result
directories and validates the filename against the corresponding tier regex before
the file is written, denying with a correction hint if it does not match.

Stdlib-only — runs under any Python interpreter without the autoskillit package.
"""

from __future__ import annotations

import json
import os
import re
import sys

PLANNER_NAMING_DENY_TRIGGER: str = "Non-canonical planner result filename"

# Tier regex patterns (stdlib re, inlined to keep this guard self-contained).
# Canonical counterparts: PHASE_RESULT_FILE_RE, ASSIGN_RESULT_FILE_RE, WP_RESULT_FILE_RE
# in autoskillit.planner.schema — update both if the naming contract changes.
_PHASE_RE = re.compile(r"^P\d+_result\.json$")
_ASSIGN_RE = re.compile(r"^P\d+-A\d+_result\.json$")
_WP_RE = re.compile(r"^P\d+-A\d+-WP\d+_result\.json$")

# Directories that contain tier result files
_TIER_DIRS = ("phases", "assignments", "work_packages")


def _build_deny(corrector: str) -> str:
    return json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": corrector,
            }
        }
    )


def _invalid_planner_result_reason(file_path: str) -> str | None:
    path_parts = file_path.replace("\\", "/").split("/")
    if ".autoskillit" not in path_parts:
        return None
    try:
        dir_idx = next(i for i, part in enumerate(path_parts) if part in _TIER_DIRS)
    except StopIteration:
        return None

    tier_dir = path_parts[dir_idx]
    filename = path_parts[-1]
    if not filename.endswith("_result.json"):
        return None

    subdir_parts = path_parts[dir_idx + 1 : -1]
    # `any(part for part in subdir_parts if part)` (not `any(subdir_parts)`)
    # because double-slash paths like `//result.json` produce an empty
    # string segment where `any([""])` would be False — allowing the
    # subdirectory check to be silently bypassed. An empty-string segment
    # is not a real subdirectory, so we explicitly skip it here.
    if any(part for part in subdir_parts if part):
        return None

    if tier_dir == "phases":
        pattern = _PHASE_RE
        reason = (
            f"{PLANNER_NAMING_DENY_TRIGGER} (phase): {filename!r}. "
            "Phase result files must match P<N>_result.json "
            "(e.g. P1_result.json, P12_result.json). "
            "The phase ID inside the file must be numeric only (e.g. P1, P2)."
        )
    elif tier_dir == "assignments":
        pattern = _ASSIGN_RE
        reason = (
            f"{PLANNER_NAMING_DENY_TRIGGER} (assignment): {filename!r}. "
            "Assignment result files must match P<N>-A<N>_result.json "
            "(e.g. P1-A1_result.json, P3-A12_result.json). "
            "The assignment ID inside the file must be numeric only (e.g. P1-A1, P2-A3)."
        )
    else:
        pattern = _WP_RE
        reason = (
            f"{PLANNER_NAMING_DENY_TRIGGER} (work package): {filename!r}. "
            r"Work package result files must match pattern P\d+-A\d+-WP\d+_result.json "
            "(e.g. P1-A1-WP1_result.json, P3-A2-WP12_result.json). "
            "The work package ID inside the file must be numeric only "
            "(e.g. P1-A1-WP1, P2-A3-WP4)."
        )
    if pattern.match(filename):
        return None
    return reason


def main() -> None:
    if os.environ.get("AUTOSKILLIT_HEADLESS") != "1":
        sys.exit(0)  # only enforce in headless planner sessions

    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError, OSError):
        sys.exit(0)  # fail-open on malformed input

    tool_name = data.get("tool_name", "")
    if tool_name not in ("Write", "Edit"):
        sys.exit(0)

    tool_input = data.get("tool_input") or {}
    file_path = tool_input.get("file_path", "")
    if not file_path:
        sys.exit(0)

    reason = _invalid_planner_result_reason(file_path)
    if reason is None:
        sys.exit(0)
    sys.stdout.write(_build_deny(reason))
    sys.stdout.flush()
    sys.exit(0)


if __name__ == "__main__":
    main()
