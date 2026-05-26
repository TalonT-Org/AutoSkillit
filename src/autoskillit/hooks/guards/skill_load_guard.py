"""PreToolUse guard: deny native tools until Skill has been called.

Gates on three conditions (ALL must be true):
- ``AUTOSKILLIT_PROVIDER_PROFILE`` is non-empty and not ``anthropic`` (case-insensitive)
- ``AUTOSKILLIT_HEADLESS == "1"``
- ``AUTOSKILLIT_SESSION_TYPE == "skill"``

When gated, checks for ``.autoskillit/temp/skill_guard_{session_id}.flag``.
If absent, denies with a directive message instructing the model to call
the Skill tool first.

Bypass conditions (early-exit before the gate):
- ``agent_id`` present in hook payload — subagent exemption
- ``AUTOSKILLIT_AGENT_BACKEND == "codex"`` (case-insensitive) — Codex backends
  cannot respond to Skill tool directives and would deadlock

Known limitation: when the model invokes Skill + native tools in a single
parallel message, the native tools fire PreToolUse before the Skill PostToolUse
writes the flag. This costs one wasted turn but self-resolves on the next turn.
This is inherent to the PreToolUse/PostToolUse timing model.

Stdlib-only — no autoskillit imports.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

_HOOKS_DIR = str(Path(__file__).resolve().parent.parent)
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

from _hook_utils import find_project_root  # type: ignore[import-not-found]  # noqa: E402

SKILL_LOAD_DENY_TRIGGER: str = "SKILL LOADING REQUIRED"

DENY_THRESHOLD: int = 5


def _check_deny_count(temp_dir: Path, session_id: str) -> bool:
    deny_dir = temp_dir / f"skill_guard_{session_id}_denials"
    if not deny_dir.exists():
        return False
    try:
        return sum(1 for p in deny_dir.iterdir() if p.is_file()) >= DENY_THRESHOLD
    except OSError:
        return False


def _record_denial(temp_dir: Path, session_id: str) -> None:
    deny_dir = temp_dir / f"skill_guard_{session_id}_denials"
    deny_dir.mkdir(parents=True, exist_ok=True)
    deny_file = deny_dir / f"{os.getpid()}_{time.monotonic_ns()}"
    try:
        fd = os.open(str(deny_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        os.close(fd)
    except (FileExistsError, OSError):
        pass


def _atomic_write_flag(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


_DENY_MESSAGE: str = (
    "SKILL LOADING REQUIRED. You MUST call the Skill tool to load the skill "
    "instructions before using any other tools. Call ToolSearch with query "
    '"select:Skill" to load the Skill tool schema, then invoke Skill with the '
    "slash command name from your prompt. Do NOT use Read, Write, Edit, Bash, or "
    "any other tool until the skill is loaded. This is a MANDATORY step — skipping "
    "it will cause the session to fail."
)


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
    except Exception:
        sys.exit(0)

    if data.get("agent_id"):
        sys.exit(0)

    backend = os.environ.get("AUTOSKILLIT_AGENT_BACKEND", "").strip()
    if backend.casefold() == "codex":
        sys.exit(0)

    profile = os.environ.get("AUTOSKILLIT_PROVIDER_PROFILE", "").strip()
    if not profile or profile.casefold() == "anthropic":
        sys.exit(0)

    if os.environ.get("AUTOSKILLIT_HEADLESS") != "1":
        sys.exit(0)

    if os.environ.get("AUTOSKILLIT_SESSION_TYPE") != "skill":
        sys.exit(0)

    session_id: str = data.get("session_id", "")
    if not session_id:
        sys.exit(0)

    project_root = find_project_root()
    temp_dir = project_root / ".autoskillit" / "temp"
    flag_path = temp_dir / f"skill_guard_{session_id}.flag"
    if flag_path.exists():
        sys.exit(0)

    if _check_deny_count(temp_dir, session_id):
        try:
            _atomic_write_flag(flag_path, "__auto_exempt__")
            sys.stderr.write(
                f"skill_load_guard: auto-exempted session {session_id} after "
                f"{DENY_THRESHOLD} denials (possible deadlock)\n"
            )
        except Exception as exc:
            sys.stderr.write(f"skill_load_guard: flag write failed: {exc}\n")
        sys.exit(0)

    _record_denial(temp_dir, session_id)

    payload = json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": _DENY_MESSAGE,
            }
        }
    )
    sys.stdout.write(payload + "\n")
    sys.exit(0)


if __name__ == "__main__":
    main()
