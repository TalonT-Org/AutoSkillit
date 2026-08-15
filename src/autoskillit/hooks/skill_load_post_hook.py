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
from datetime import UTC, datetime
from pathlib import Path

_HOOKS_DIR = str(Path(__file__).resolve().parent)
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

from _hook_settings import (  # noqa: E402
    resolve_quota_log_dir,
    write_quota_log_event,
)
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


def _read_existing_flag(path: Path) -> dict[str, object] | None:
    """Return the existing flag content as a parsed JSON dict, or None if absent/invalid.

    Existing single-skill flags written by the previous version of this hook
    (raw skill name strings) are migrated in place to the new JSON envelope
    so older skill loads remain visible after an upgrade.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return None
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def _merge_existing_entry(
    existing: dict[str, object],
    new_entry: dict[str, object],
) -> dict[str, object]:
    """OR-accumulate the join bit across loaded skills and append the new entry.

    Loaded-skill entries are immutable; the ``join_required`` boolean in the
    flag is the OR of every loaded skill's ``join_required`` value. A later
    join-false load does NOT downgrade an established required-join binding.
    """
    loaded_obj = existing.get("loaded_skills", [])
    loaded: list[dict[str, object]] = list(loaded_obj) if isinstance(loaded_obj, list) else []
    loaded.append(new_entry)
    existing_join = bool(existing.get("join_required", False))
    new_join = bool(new_entry.get("join_required", False))
    result = dict(existing)
    result["loaded_skills"] = loaded
    result["join_required"] = existing_join or new_join
    return result


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
    except Exception:
        sys.exit(0)

    if data.get("agent_id"):
        sys.exit(0)

    backend = os.environ.get("AUTOSKILLIT_AGENT_BACKEND", "").strip()
    if backend == "codex":
        log_dir = resolve_quota_log_dir(caller="skill_load_post_hook")
        write_quota_log_event(
            {
                "ts": datetime.now(UTC).isoformat(),
                "event": "skill_load_backend_bypass",
                "backend": backend,
            },
            log_dir,
            caller="skill_load_post_hook",
        )
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

    existing = _read_existing_flag(flag_path)

    new_entry: dict[str, object] = {
        "skill_name": skill_name,
        "ts": datetime.now(UTC).isoformat(),
        "join_required": False,
        "child_spawn_cardinality": {},
        "semantic_digest": "",
        "adaptation_digest": "",
        "artifact_digest": "",
        "artifact_incarnation": "",
    }
    merged = (
        _merge_existing_entry(existing or {}, new_entry)
        if existing
        else {
            "schema_version": 1,
            "session_id": session_id,
            "join_required": False,
            "loaded_skills": [new_entry],
        }
    )
    try:
        _atomic_write(flag_path, json.dumps(merged, sort_keys=True))
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
