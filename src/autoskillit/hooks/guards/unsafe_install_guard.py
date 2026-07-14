"""
PreToolUse hook — blocks run_cmd and Bash tool calls that would install an editable
package into system Python without an explicit --python .venv target.

This guards the interactive orchestrator path (skill sessions cannot call
run_cmd at all — they are blocked by skill_orchestration_guard.py).
"""

import json
import os
import sys
from pathlib import Path

_HOOKS_DIR = str(Path(__file__).resolve().parent.parent)
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

from _command_classification import (  # type: ignore[import-not-found]  # noqa: E402
    command_verb_and_args,
    extract_interpreter_command_payloads,
    extract_shell_command_payloads,
    strip_heredoc_bodies,
    tokenize_command_segments,
)

UNSAFE_INSTALL_DENY_TRIGGER: str = "Blocked: editable install without --python .venv"


def _basename_lower(token: str) -> str:
    return os.path.basename(token).lower()


def _is_pip_executable(token: str) -> bool:
    base = _basename_lower(token)
    if base == "pip":
        return True
    if base.startswith("pip") and base[3:].replace(".", "").isdigit():
        return True
    return False


def _is_uv_executable(token: str) -> bool:
    return _basename_lower(token) == "uv"


def _is_maturin_executable(token: str) -> bool:
    return _basename_lower(token) == "maturin"


def _is_python_executable(token: str) -> bool:
    base = _basename_lower(token)
    if base == "python" or base == "python3":
        return True
    if base.startswith("python") and base[len("python") :].replace(".", "").isdigit():
        return True
    return False


def _is_venv_path(value: str) -> bool:
    """Return True if *value* identifies a `.venv` Python interpreter path.

    A safe value has a path component exactly equal to `.venv`. `.venv-poison`
    and unrelated outer text do not qualify.
    """
    if not value:
        return False
    if value == ".venv" or value.startswith(".venv/") or value.startswith(".venv\\"):
        return True
    if "/.venv/" in ("/" + value) or value.startswith("/.venv"):
        return True
    parts = value.replace("\\", "/").split("/")
    return any(part == ".venv" for part in parts)


def _python_target_value(args: list[str]) -> str | None:
    """Return the value of `--python` if present, else None."""
    for i, token in enumerate(args):
        if token == "--python" and i + 1 < len(args):
            return args[i + 1]
        if token.startswith("--python="):
            return token[len("--python=") :]
    return None


def _has_system_flag(args: list[str]) -> bool:
    return any(token == "--system" for token in args)


def _is_editable_marker(token: str) -> bool:
    return (
        token == "-e"
        or token == "--editable"
        or token.startswith("-e=")
        or token.startswith("--editable=")
    )


def _find_pip_install(args: list[str]) -> tuple[list[str], list[str]] | None:
    """Find `pip install` in *args* and return (pre_install, post_install)."""
    i = 0
    while i < len(args):
        token = args[i]
        if token == "--":
            i += 1
            break
        if token.startswith("-"):
            if "=" in token:
                i += 1
                continue
            if token in {"-r", "-c", "-t", "-b", "--requirement", "--constraint"} and i + 1 < len(
                args
            ):
                i += 2
                continue
            i += 1
            continue
        break
    if i >= len(args) or args[i] != "install":
        return None
    return (args[: i + 1], args[i + 1 :])


def _classify_install_invocation(
    segment: list[str],
) -> tuple[str, list[str], list[str]] | None:
    """Return (kind, install_args, post_install_args) for matched installs.

    Recognizes ordered grammars:
    - pip / pip3 / pip3.X install ...
    - uv pip install ...
    - python / python3 / python3.X -m pip install ...
    - maturin develop ...
    Returns None when the segment is not a matching install invocation.
    """
    verb, args = command_verb_and_args(segment)
    if not verb:
        return None
    if _is_pip_executable(verb):
        match = _find_pip_install(args)
        if match is None:
            return None
        install_args, post_install = match
        return ("pip", install_args, post_install)
    if _is_uv_executable(verb):
        if len(args) < 2 or args[0] != "pip":
            return None
        match = _find_pip_install(args[1:])
        if match is None:
            return None
        install_args, post_install = match
        return ("uv-pip", install_args, post_install)
    if _is_python_executable(verb):
        if len(args) < 3 or args[0] != "-m" or args[1] != "pip":
            return None
        match = _find_pip_install(args[2:])
        if match is None:
            return None
        install_args, post_install = match
        return ("module-pip", install_args, post_install)
    if _is_maturin_executable(verb):
        if not args or args[0] != "develop":
            return None
        return ("maturin", ["develop"], args[1:])
    return None


def _iter_install_segments(command: str):
    """Yield (kind, install_args, post_install) for every matched invocation.

    Walks the top-level command, then nested shell payloads (recursively),
    then Python subprocess payloads. Each invocation is classified once.
    """
    seen: set[str] = set()
    queue: list[str] = [command]

    while queue:
        current = queue.pop(0)
        if current in seen:
            continue
        seen.add(current)

        try:
            stripped = strip_heredoc_bodies(current)
            segments = tokenize_command_segments(stripped)
        except (ValueError, TypeError):
            segments = []

        for segment in segments:
            result = _classify_install_invocation(segment)
            if result is not None:
                yield result

        for payload in extract_shell_command_payloads(current):
            if payload not in seen:
                queue.append(payload)

        argv_payloads, has_unresolved = extract_interpreter_command_payloads(current)
        for payload in argv_payloads:
            if isinstance(payload, list):
                result = _classify_install_invocation(payload)
                if result is not None:
                    yield result
            elif isinstance(payload, str):
                if payload not in seen:
                    queue.append(payload)
        if has_unresolved:
            yield ("unresolved-subprocess", [], [])


def _is_unsafe_editable_install(cmd: str) -> bool:
    """Return True if *cmd* runs an editable install not targeting .venv Python."""
    for kind, _install_args, post_install in _iter_install_segments(cmd):
        if kind == "maturin":
            return True
        if kind == "unresolved-subprocess":
            # A matching process-launch call could not be statically resolved.
            return True
        if any(_is_editable_marker(t) for t in post_install):
            python_target = _python_target_value(post_install)
            if python_target is not None and _is_venv_path(python_target):
                continue
            return True
    return False


def _is_system_install(cmd: str) -> bool:
    """Return True if *cmd* runs a pip/uv-pip install with --system."""
    for kind, install_args, post_install in _iter_install_segments(cmd):
        if kind in ("pip", "uv-pip", "module-pip") and _has_system_flag(
            install_args + post_install
        ):
            return True
    return False


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
        tool_input = data.get("tool_input", {})
        cmd = tool_input.get("command", "") or tool_input.get("cmd", "")
    except (json.JSONDecodeError, AttributeError, OSError):
        sys.exit(0)

    if not isinstance(cmd, str) or not cmd:
        sys.exit(0)

    if _is_unsafe_editable_install(cmd):
        payload = json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        "Blocked: editable install without --python .venv. "
                        "Use `task install-worktree` or add `--python .venv/bin/python`. "
                        "Installing into system Python creates dangling entry points when "
                        "the worktree is deleted."
                    ),
                }
            }
        )
        sys.stdout.write(payload + "\n")
        sys.exit(0)

    if _is_system_install(cmd):
        payload = json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        "Blocked: --system install from worktree contaminates global environment. "
                        "Use `task install-worktree` or add `--python .venv/bin/python`."
                    ),
                }
            }
        )
        sys.stdout.write(payload + "\n")
        sys.exit(0)

    sys.exit(0)


if __name__ == "__main__":
    main()
