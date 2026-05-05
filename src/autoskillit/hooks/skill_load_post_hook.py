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

    if not os.environ.get("AUTOSKILLIT_PROVIDER_PROFILE", "").strip():
        sys.exit(0)

    if data.get("tool_name") != "Skill":
        sys.exit(0)

    tool_input: dict = data.get("tool_input", {}) or {}
    skill_name: str = tool_input.get("skill", "")
    session_id: str = data.get("session_id", "")

    if not session_id:
        sys.exit(0)

    flag_path = Path.cwd() / ".autoskillit" / "temp" / f"skill_guard_{session_id}.flag"
    try:
        _atomic_write(flag_path, skill_name)
    except Exception as e:
        print(f"skill_load_post_hook: failed to write flag: {e}", file=sys.stderr)

    sys.exit(0)


if __name__ == "__main__":
    main()
