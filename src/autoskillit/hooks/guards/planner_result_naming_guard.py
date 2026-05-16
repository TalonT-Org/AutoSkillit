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
import re
import sys

# Tier regex patterns (stdlib re, inlined to keep this guard self-contained)
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


def main() -> None:
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

    # Check if the path is inside a planner result directory
    path_parts = file_path.replace("\\", "/").split("/")
    try:
        dir_idx = next(i for i, p in enumerate(path_parts) if p in _TIER_DIRS)
    except StopIteration:
        sys.exit(0)  # not a planner result directory

    tier_dir = path_parts[dir_idx]
    filename = path_parts[-1]

    # Skip non-result files (e.g. wp_index.json, context_*.json, manifests)
    if not filename.endswith("_result.json"):
        sys.exit(0)

    # Skip files in subdirectories (e.g. wp_sentinels/P1_result.json)
    subdir_parts = path_parts[dir_idx + 1 : -1]
    if any(subdir_parts):
        sys.exit(0)

    # Validate against the appropriate tier regex
    if tier_dir == "phases":
        if _PHASE_RE.match(filename):
            sys.exit(0)
        sys.stdout.write(
            _build_deny(
                f"Non-canonical phase result filename: {filename!r}. "
                f"Phase result files must match P<N>_result.json (e.g. P1_result.json, P12_result.json). "
                f"The phase ID inside the file must be numeric only (e.g. P1, P2)."
            )
        )
        sys.exit(0)

    if tier_dir == "assignments":
        if _ASSIGN_RE.match(filename):
            sys.exit(0)
        sys.stdout.write(
            _build_deny(
                f"Non-canonical assignment result filename: {filename!r}. "
                f"Assignment result files must match P<N>-A<N>_result.json "
                f"(e.g. P1-A1_result.json, P3-A12_result.json). "
                f"The assignment ID inside the file must be numeric only (e.g. P1-A1, P2-A3)."
            )
        )
        sys.exit(0)

    if tier_dir == "work_packages":
        if _WP_RE.match(filename):
            sys.exit(0)
        sys.stdout.write(
            _build_deny(
                f"Non-canonical work package result filename: {filename!r}. "
                f"Work package result files must match P<N>-A<N>-WP<N>_result.json "
                f"(e.g. P1-A1-WP1_result.json, P3-A2-WP12_result.json). "
                f"The work package ID inside the file must be numeric only (e.g. P1-A1-WP1, P2-A3-WP4)."
            )
        )
        sys.exit(0)


if __name__ == "__main__":
    main()
