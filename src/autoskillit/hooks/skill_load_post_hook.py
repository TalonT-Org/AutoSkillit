"""PostToolUse hook: write the session binding after a Skill call.

Fires on ``Skill`` tool calls and writes the shared session-binding artifact.
The companion PreToolUse guard (``guards/skill_load_guard.py``) checks this
artifact before allowing native tool calls.

Stdlib-only — no autoskillit imports.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import UTC, datetime
from pathlib import Path

_HOOKS_DIR = str(Path(__file__).resolve().parent)
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

from _hook_payload import normalize_payload_cwd  # type: ignore[import-not-found]  # noqa: E402
from _hook_settings import (  # noqa: E402
    resolve_quota_log_dir,
    write_quota_log_event,
)
from _session_binding import (  # type: ignore[import-not-found]  # noqa: E402
    SESSION_BINDING_SCHEMA_VERSION,
    SessionBinding,
    SessionBindingError,
    loaded_skill_from_manifest,
    merge_binding,
    normalize_skill_name,
    read_binding,
    read_manifest,
    resolve_binding_path,
    resolve_projection_manifest_path,
    unresolved_loaded_skill,
    write_binding,
)


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

    if data.get("tool_name") != "Skill":
        sys.exit(0)

    tool_input: dict = data.get("tool_input", {}) or {}
    skill_name = normalize_skill_name(tool_input.get("skill", ""))
    session_id: str = data.get("session_id", "")

    if not session_id:
        sys.exit(0)

    payload_cwd = normalize_payload_cwd(data.get("cwd"))
    flag_path = resolve_binding_path(payload_cwd, session_id)

    try:
        existing = read_binding(flag_path)
    except SessionBindingError as exc:
        sys.stderr.write(
            f"skill_load_post_hook: failed to read existing flag {flag_path}: {exc}\n"
        )
        existing = SessionBinding(
            schema_version=SESSION_BINDING_SCHEMA_VERSION,
            session_id=session_id,
            join_required=True,
            binding_valid=False,
            artifact_digest="",
            loaded_skills=(),
        )

    ts = datetime.now(UTC).isoformat()
    artifact_digest = ""
    binding_error: str | None = None
    try:
        manifest_path = resolve_projection_manifest_path(Path(__file__))
        if manifest_path is None:
            raise SessionBindingError("projection manifest not found")
        manifest = read_manifest(manifest_path)
        new_entry = loaded_skill_from_manifest(manifest, skill_name, ts)
        artifact_digest = str(manifest["artifact_digest"])
    except SessionBindingError as exc:
        binding_error = str(exc)
        new_entry = unresolved_loaded_skill(skill_name, ts, binding_error)

    merged = merge_binding(
        existing,
        session_id=session_id,
        new_entry=new_entry,
        artifact_digest=artifact_digest,
    )
    binding_written = False
    try:
        write_binding(flag_path, merged)
        binding_written = True
    except Exception:
        sys.stderr.write(
            f"skill_load_post_hook: failed to write flag {flag_path}:\n{traceback.format_exc()}"
        )

    if binding_error is not None:
        log_dir = resolve_quota_log_dir(caller="skill_load_post_hook")
        write_quota_log_event(
            {
                "ts": ts,
                "event": "skill_load_binding_unresolved",
                "session_id": session_id,
                "skill_name": skill_name,
                "binding_error": binding_error,
            },
            log_dir,
            caller="skill_load_post_hook",
        )

    context_parts: list[str] = []
    if binding_written and new_entry.binding_valid and new_entry.join_required:
        context_parts.append(
            "JOIN DECLARATION AUTHORITY: Call declare_join_batch with the normalized bare "
            f"skill_name='{new_entry.skill_name}' and exact session_id='{session_id}' delivered "
            "by this Skill PostToolUse hook."
        )

    marker = os.environ.get("AUTOSKILLIT_COMPLETION_MARKER", "").strip()
    if marker:
        context_parts.append(
            "COMPLETION REMINDER: After completing your task, your final text output "
            f"MUST end with exactly: {marker}\n"
            "This is mandatory regardless of what the skill's Output section specifies."
        )
    if context_parts:
        payload = json.dumps({"additionalContext": "\n\n".join(context_parts)})
        sys.stdout.write(payload + "\n")

    sys.exit(0)


if __name__ == "__main__":
    main()
