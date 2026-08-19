"""PreToolUse hook — denies Bash/run_cmd commands matching a known
resource-exhaustion shape: a backgrounded infinite loop, or `kill %N`
job-control syntax in a non-interactive shell payload.

Documented threat model
------------------------
This guard matches raw command text *before* the shell performs expansion —
the exact surface catalogued as GuardFall (Adversa AI, 2026-06-30, "a
universal shell injection vulnerability in open-source AI agents"): five
bypass classes exist for any textual command matcher — quote removal merging
tokens, `$IFS` expansion, command substitution computing the binary name,
base64 piped to an interpreter, and alternative argv shapes for the same
effect. For this guard concretely, `while` is reachable via `$'\\x77hile'`,
`$(echo while)`, `eval`, line continuations, or base64 — all deliberately out
of reach of a textual matcher.

Adding more literal patterns does not close this class; the tether ceiling
(process-tether registry) and host resource limits are the structural
backstop. Tokenize-and-evaluate designs (e.g. the `continuedev/continue`
reference implementation: `shell-quote` tokenization, variable-expansion
detection, recursive substitution evaluation, pipe-destination checks) are
the only known way to raise this tier, and would be a separate deliberate
project — not an incremental pattern addition.

Incident B (issue #4678): a busy-loop leak spawned via
`sh -c 'for j in 1 2 3 4; do (while :; do :; done) & done; sleep 25;
kill %1 %2 %3 %4 2>/dev/null'` — the backgrounded infinite loops survive
because non-interactive `sh` disables job control, so `kill %1` silently
fails and `2>/dev/null` swallows the error.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_HOOKS_DIR = str(Path(__file__).resolve().parent.parent)
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

from _command_classification import (  # type: ignore[import-not-found]  # noqa: E402
    extract_shell_command_payloads,
    strip_heredoc_bodies,
)
from _hook_payload import parse_hook_command  # type: ignore[import-not-found]  # noqa: E402

RESOURCE_EXHAUSTION_DENY_TRIGGER: str = "Blocked: resource-exhaustion command pattern"

# `while :` / `while true` ... `done`, optionally parenthesized, immediately
# backgrounded with `&` (not `&&`). Non-greedy up to the first `done` — a
# best-effort match, not a shell parser; see module docstring. `:` is
# non-word, so `\b` never fires after it — a lookahead on the delimiter that
# follows is used instead of `:\b`.
_BACKGROUNDED_INFINITE_LOOP_RE = re.compile(
    r"\bwhile\s+(?::(?=[\s;)&|]|$)|true\b).*?\bdone\b\s*\)?\s*&(?!&)",
    re.DOTALL,
)

# `kill` followed, within the same simple statement, by at least one `%N`
# job-control spec. Bounded to `;`, `&`, `|`, and newline so an unrelated
# later command's `%` token is never captured.
_KILL_JOBSPEC_RE = re.compile(r"\bkill\b[^;&|\n]*%\d+")


def _iter_scan_texts(command: str) -> list[str]:
    """Return the top-level command plus every recursively-extracted shell
    payload (`sh -c`, `bash -c`, `eval`, and `$(...)`/backtick substitutions).

    Mirrors the BFS-with-seen-set traversal already used by
    unsafe_install_guard.py's `_iter_install_segments` for the same reason:
    `extract_shell_command_payloads` is single-level per call.
    """
    seen: set[str] = set()
    texts: list[str] = []
    queue: list[str] = [command]
    while queue:
        current = queue.pop(0)
        if current in seen:
            continue
        seen.add(current)
        texts.append(current)
        try:
            payloads = extract_shell_command_payloads(current)
        except (ValueError, TypeError):
            payloads = []
        for payload in payloads:
            if payload not in seen:
                queue.append(payload)
    return texts


def _matches_resource_exhaustion_pattern(command: str) -> str | None:
    """Return a denial reason if *command* matches a known pattern, else None."""
    try:
        stripped = strip_heredoc_bodies(command)
    except (ValueError, TypeError):
        stripped = command
    for text in _iter_scan_texts(stripped):
        if _BACKGROUNDED_INFINITE_LOOP_RE.search(text):
            return (
                "a backgrounded infinite loop (`while :`/`while true` ... `done` "
                "followed by `&`) — this is the exact leak shape behind issue #4678 "
                "Incident B"
            )
        if _KILL_JOBSPEC_RE.search(text):
            return (
                "`kill %N` job-control syntax — job control is disabled in "
                "non-interactive shells, so this silently fails to kill anything "
                "and any `2>/dev/null` hides the failure"
            )
    return None


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, AttributeError, OSError):
        sys.exit(0)  # fail-open on malformed input

    cmd = parse_hook_command(data).command or ""
    if not cmd:
        sys.exit(0)

    reason = _matches_resource_exhaustion_pattern(cmd)
    if reason is None:
        sys.exit(0)

    payload = json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"{RESOURCE_EXHAUSTION_DENY_TRIGGER}: {reason}. "
                    "Capture the PID with `kill $!` (or a named PID) instead of a "
                    "job-control spec, wrap long-running loops with `timeout`, and "
                    "run them in the foreground where the caller can bound them."
                ),
            }
        }
    )
    sys.stdout.write(payload + "\n")
    sys.exit(0)


if __name__ == "__main__":
    main()
