"""PostToolUse hook: write skill-loaded flag for non-Anthropic providers.

Fires on ``Skill`` tool calls.  When ``AUTOSKILLIT_PROVIDER_PROFILE`` is
non-empty, writes ``.autoskillit/temp/skill_guard_{session_id}.flag``
containing the loaded skill name.  The companion PreToolUse guard
(``guards/skill_load_guard.py``) checks this flag before allowing native
tool calls.

Stdlib-only — no autoskillit imports.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

_HOOKS_DIR = str(Path(__file__).resolve().parent)
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

from _hook_utils import find_project_root  # type: ignore[import-not-found]  # noqa: E402


def _atomic_write(path: Path, content: str) -> None:
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


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
    except Exception:
        sys.exit(0)

    if data.get("agent_id"):
        sys.exit(0)

    backend = os.environ.get("AUTOSKILLIT_AGENT_BACKEND", "").strip()
    if backend == "codex":
        sys.exit(0)

    if not os.environ.get("AUTOSKILLIT_PROVIDER_PROFILE", "").strip():
        sys.exit(0)

    if data.get("tool_name") != "Skill":
        sys.exit(0)

    tool_input: dict = data.get("tool_input", {}) or {}
    skill_name: str = tool_input.get("skill", "")
    session_id: str = data.get("session_id", "")

    if not session_id:
        sys.exit(0)

    flag_path = find_project_root() / ".autoskillit" / "temp" / f"skill_guard_{session_id}.flag"
    try:
        _atomic_write(flag_path, skill_name)
    except Exception as exc:
        sys.stderr.write(f"skill_load_post_hook: failed to write flag {flag_path}: {exc}\n")

    marker = os.environ.get("AUTOSKILLIT_COMPLETION_MARKER", "").strip()
    if marker:
        reminder = (
            "COMPLETION REMINDER: After completing your task, your final text output "
            f"MUST end with exactly: {marker}\n"
            "This is mandatory regardless of what the skill's Output section specifies."
        )
        payload = json.dumps({"additionalContext": reminder})
        sys.stdout.write(payload + "\n")

    sys.exit(0)


if __name__ == "__main__":
    main()
