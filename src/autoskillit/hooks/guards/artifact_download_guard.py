#!/usr/bin/env python3
"""PreToolUse hook: block gh run/release download without --dir via run_cmd or Bash tool.

Prevents CI artifact downloads from dumping files into the project root.
All sessions are covered — no skill or session-type exemptions exist because
no legitimate autoskillit skill uses gh run/release download without --dir.

stdlib-only; no autoskillit imports.
"""

from __future__ import annotations

import json
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
    all_evaluated_segments,
    command_verb_and_args,
)
from _hook_payload import parse_hook_command  # type: ignore[import-not-found]  # noqa: E402

ARTIFACT_DOWNLOAD_DENY_TRIGGER: str = "gh artifact download without --dir is prohibited"

_DOWNLOAD_SUBCOMMANDS: frozenset[tuple[str, str]] = frozenset(
    {("run", "download"), ("release", "download")}
)

_DENY_REASON = (
    "gh {sub1} download without --dir is prohibited. "
    "Artifact downloads without --dir dump files into the project root. "
    "Use --dir <path> or -D <path> to specify an explicit output directory."
)


def _deny_subcommand(cmd: str) -> str | None:
    """Return the subcommand name if an unguarded download is detected, else None.

    Reads *cmd* exclusively through `all_evaluated_segments` (rectify #4941
    Part B): a direct invocation, one delivered via `bash -c`/`eval`, one fed
    through a heredoc/herestring/pipe to a shell, and a literal-argv
    `subprocess.run(["gh", "run", "download", ...])` are all seen the same
    way, at every command-position candidate span. Returns `None` when
    `all_evaluated_segments` cannot tokenize *cmd* (fail-open).
    """
    segments = all_evaluated_segments(cmd)
    if segments is None:
        return None
    for segment in segments:
        for start, end in _command_position_candidate_spans(segment):
            verb, args = command_verb_and_args(segment[start:end])
            if verb != "gh" or len(args) < 2:
                continue
            pair = (args[0], args[1])
            if pair in _DOWNLOAD_SUBCOMMANDS:
                rest = args[2:]
                if "--dir" not in rest and "-D" not in rest:
                    return args[0]  # e.g. "run" or "release"
    return None


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, AttributeError, OSError):
        sys.exit(0)

    cmd = parse_hook_command(data).command or ""

    if not cmd:
        sys.exit(0)

    sub1 = _deny_subcommand(cmd)
    if sub1 is None:
        sys.exit(0)

    payload = json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": _DENY_REASON.format(sub1=sub1),
            }
        }
    )
    sys.stdout.write(payload + "\n")
    sys.exit(0)


if __name__ == "__main__":
    main()
