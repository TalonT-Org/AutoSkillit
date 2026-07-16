#!/usr/bin/env python3
"""PreToolUse guard for high-confidence unbounded shell output shapes.

The guard is deliberately enumerated: recursive search (R1), JSONL producers
(R2), and directory traversal with find (R3).  Shell syntax and descriptor
flow are delegated to the shared stdlib-only command classifier.

stdlib-only; no autoskillit imports.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

_HOOKS_DIR = str(Path(__file__).resolve().parent.parent)
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

from _command_classification import (  # type: ignore[import-not-found]  # noqa: E402
    CommandBudgetDisposition,
    classify_command_output_budget,
    command_tokens_without_output_redirections,
    command_verb_and_args,
)
from _hook_settings import read_merged_hook_config  # type: ignore[import-not-found]  # noqa: E402

OUTPUT_BUDGET_DENY_TRIGGER: str = "Unbounded command output is prohibited"

# Must stay in sync with the output_budget_guard HookDef in hook_registry.py.
_EXEMPT_SKILLS: frozenset[str] = frozenset()

_DEFAULT_SMALL_FILE_MAX_BYTES = 5_000
_DEFAULT_SHELL_MAX_INLINE_BYTES = 12_000
_JSONL_PRODUCERS: frozenset[str] = frozenset({"cat", "rg", "grep", "sed", "awk", "jq"})
_GLOB_CHARS = frozenset("*?[]{}")
_RISKY_SURFACE_RE = re.compile(r"\b(?:rg|grep|cat|sed|awk|jq|find)\b")

_RG_FLAGS_WITH_VALUE: frozenset[str] = frozenset(
    {
        "-e",
        "--regexp",
        "-f",
        "--file",
        "-g",
        "--glob",
        "--iglob",
        "-t",
        "--type",
        "-T",
        "--type-not",
        "-M",
        "--max-columns",
        "-m",
        "--max-count",
        "-A",
        "--after-context",
        "-B",
        "--before-context",
        "-C",
        "--context",
        "--encoding",
        "--engine",
        "--max-depth",
        "--path-separator",
        "--sort",
        "--sortr",
        "--threads",
        "--type-add",
        "--type-clear",
    }
)


def _positive_int(value: object, default: int) -> int:
    return (
        value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else default
    )


def _read_policy() -> tuple[bool, int, int]:
    try:
        config = read_merged_hook_config()
        section = config.get("output_budget_policy", {})
        if not isinstance(section, dict):
            section = {}
    except (OSError, AttributeError, TypeError, json.JSONDecodeError):
        section = {}
    return (
        section.get("disabled") is True,
        _positive_int(section.get("small_file_max_bytes"), _DEFAULT_SMALL_FILE_MAX_BYTES),
        _positive_int(section.get("shell_max_inline_bytes"), _DEFAULT_SHELL_MAX_INLINE_BYTES),
    )


def _has_flag(args: list[str], *names: str) -> bool:
    for arg in args:
        if arg in names:
            return True
        if any(arg.startswith(f"{name}=") for name in names if name.startswith("--")):
            return True
        if "-q" in names and arg.startswith("-") and not arg.startswith("--") and "q" in arg[1:]:
            return True
    return False


def _rg_targets(args: list[str]) -> list[str]:
    """Return literal rg search targets after skipping patterns and option values."""
    positionals: list[str] = []
    explicit_pattern = False
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--":
            positionals.extend(args[i + 1 :])
            break
        if arg in _RG_FLAGS_WITH_VALUE:
            if arg in {"-e", "--regexp"}:
                explicit_pattern = True
            i += 2
            continue
        if arg.startswith("--regexp=") or (arg.startswith("-e") and len(arg) > 2):
            explicit_pattern = True
            i += 1
            continue
        if arg.startswith("-"):
            i += 1
            continue
        positionals.append(arg)
        i += 1
    return positionals if explicit_pattern else positionals[1:]


def _looks_like_directory(path: str, cwd: Path) -> bool:
    if path in {".", "..", "/"} or path.endswith(("/", os.sep)):
        return True
    if "$" in path or any(char in path for char in _GLOB_CHARS):
        return True
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = cwd / candidate
    try:
        if candidate.is_dir():
            return True
    except OSError:
        return True
    # A path with no filename suffix is conservatively directory-shaped.  This
    # catches not-yet-existing search roots while preserving the explicit
    # non-JSONL single-file surface accepted by the protocol.
    return not Path(path).suffix


def _is_recursive_search(verb: str, args: list[str], cwd: Path) -> bool:
    if verb == "grep":
        return _has_flag(args, "-r", "-R", "--recursive", "--dereference-recursive")
    if verb != "rg":
        return False
    targets = _rg_targets(args)
    return not targets or any(_looks_like_directory(target, cwd) for target in targets)


def _jsonl_targets(args: list[str]) -> list[str]:
    return [arg for arg in args if ".jsonl" in arg.lower()]


def _literal_small_jsonl_files(targets: list[str], cwd: Path, max_bytes: int) -> bool:
    if not targets:
        return False
    total = 0
    root = cwd.resolve()
    for raw in targets:
        if raw == "-" or "$" in raw or any(char in raw for char in _GLOB_CHARS):
            return False
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = root / candidate
        try:
            if candidate.is_symlink() or not candidate.is_file():
                return False
            resolved = candidate.resolve(strict=True)
            if os.path.commonpath((str(root), str(resolved))) != str(root):
                return False
            total += resolved.stat().st_size
        except (OSError, RuntimeError, ValueError):
            return False
        if total > max_bytes:
            return False
    return True


def _is_plain_cat(args: list[str], targets: list[str]) -> bool:
    remaining = [arg for arg in args if arg != "--"]
    return bool(targets) and remaining == targets


def _is_wc_lines(args: list[str], targets: list[str]) -> bool:
    return bool(targets) and all(arg in {"-l", "--lines"} or arg in targets for arg in args)


def _producer_classifier(
    tokens: list[str], *, cwd: Path, small_file_max_bytes: int
) -> tuple[bool, bool] | None:
    argv = command_tokens_without_output_redirections(tokens)
    if argv is None:
        # The shared classifier will separately turn ambiguous descriptor
        # syntax into UNKNOWN.  Preserve the risky match when possible.
        argv = tokens
    raw_verb, args = command_verb_and_args(argv)
    verb = os.path.basename(raw_verb)
    if not verb:
        return None

    profiles: list[tuple[bool, bool]] = []
    if _is_recursive_search(verb, args, cwd):
        profiles.append((_has_flag(args, "-q", "--quiet"), False))

    targets = _jsonl_targets(args)
    if verb in _JSONL_PRODUCERS and targets:
        if verb == "rg" and _has_flag(args, "-q", "--quiet"):
            profiles.append((True, False))
        elif (
            verb == "cat"
            and _is_plain_cat(args, targets)
            and _literal_small_jsonl_files(targets, cwd, small_file_max_bytes)
        ):
            profiles.append((True, True))
        else:
            profiles.append((False, False))

    if verb == "wc" and targets and _is_wc_lines(args, targets):
        profiles.append((True, True))

    if verb == "find" and args and _looks_like_directory(args[0], cwd):
        profiles.append(("-quit" in args, False))

    if not profiles:
        return None
    return (all(profile[0] for profile in profiles), all(profile[1] for profile in profiles))


def _classify_command(
    command: str, *, cwd: Path, small_file_max_bytes: int, shell_max_inline_bytes: int
) -> CommandBudgetDisposition:
    if not _RISKY_SURFACE_RE.search(command):
        return CommandBudgetDisposition.BOUNDED

    def classify(tokens: list[str]) -> tuple[bool, bool] | None:
        return _producer_classifier(tokens, cwd=cwd, small_file_max_bytes=small_file_max_bytes)

    return classify_command_output_budget(
        command,
        classify,
        max_inline_bytes=shell_max_inline_bytes,
        cwd=str(cwd),
    )


def main() -> None:
    skill_name = os.environ.get("AUTOSKILLIT_SKILL_NAME", "")
    if skill_name in _EXEMPT_SKILLS:
        sys.exit(0)

    try:
        data = json.loads(sys.stdin.read())
        tool_input = data.get("tool_input", {})
        command = tool_input.get("command", "") or tool_input.get("cmd", "")
    except (json.JSONDecodeError, AttributeError, OSError, TypeError):
        sys.exit(0)
    if not isinstance(command, str) or not command:
        sys.exit(0)

    disabled, small_file_max_bytes, shell_max_inline_bytes = _read_policy()
    if disabled:
        sys.exit(0)

    disposition = _classify_command(
        command,
        cwd=Path.cwd(),
        small_file_max_bytes=small_file_max_bytes,
        shell_max_inline_bytes=shell_max_inline_bytes,
    )
    if disposition is CommandBudgetDisposition.BOUNDED:
        sys.exit(0)

    reason = (
        f"{OUTPUT_BUDGET_DENY_TRIGGER}: this R1-R3 producer has no proven byte-bounded "
        "stdout/stderr path. Run `rg -l ... 2>&1 | head -c 4000` for bounded "
        "discovery, or redirect both descriptors under `.autoskillit/temp/` then read "
        "slices."
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
    sys.exit(0)


if __name__ == "__main__":
    main()
