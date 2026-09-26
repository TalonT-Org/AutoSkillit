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
_RUNTIME_DIR = str(Path(_HOOKS_DIR) / "_runtime")
if _RUNTIME_DIR not in sys.path:
    sys.path.insert(0, _RUNTIME_DIR)


from _guard_decision_diagnostics import (  # noqa: E402
    record_guard_decision,
)
from _hook_payload import normalize_payload_cwd  # type: ignore[import-not-found]  # noqa: E402
from _hook_settings import (  # noqa: E402
    bridge_session_registry,
    hook_join_applicability,
    resolve_quota_log_dir,
    write_quota_log_event,
)
from _session_binding import (  # noqa: E402
    SESSION_BINDING_SCHEMA_VERSION,
    LoadedSkillEntry,
    SessionBinding,
    SessionBindingError,
    binding_lock,
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


def _write_skill_binding(
    flag_path: Path, *, skill_name: str, session_id: str, ts: str
) -> tuple[LoadedSkillEntry, str | None, bool]:
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

    binding_written = False
    try:
        with binding_lock(flag_path):
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
            write_binding(
                flag_path,
                merge_binding(
                    existing,
                    session_id=session_id,
                    new_entry=new_entry,
                    artifact_digest=artifact_digest,
                ),
            )
            binding_written = True
    except Exception:
        sys.stderr.write(
            f"skill_load_post_hook: failed to write flag {flag_path}:\n{traceback.format_exc()}"
        )
    return new_entry, binding_error, binding_written


def _invocation_skill(data: dict[str, object]) -> tuple[str, object] | None:
    """Return the invocation event and unnormalized skill name for supported shapes."""
    event_name = data.get("hook_event_name")
    if event_name == "PostToolUse" and data.get("tool_name") == "Skill":
        tool_input = data.get("tool_input", {})
        return (
            ("PostToolUse", tool_input.get("skill", "")) if isinstance(tool_input, dict) else None
        )
    if event_name == "UserPromptExpansion" and data.get("expansion_type") == "slash_command":
        return "UserPromptExpansion", data.get("command_name", "")
    return None


def _join_context_parts(
    data: dict[str, object], payload_cwd: str, session_id: str, entry: LoadedSkillEntry
) -> list[str]:
    if not entry.join_required:
        return []
    parts = [
        "JOIN DECLARATION AUTHORITY: Call declare_join_batch with the normalized bare "
        f"skill_name={json.dumps(entry.skill_name)} and exact "
        f"session_id={json.dumps(session_id)} delivered "
        "by this Skill PostToolUse hook."
    ]
    if hook_join_applicability(
        data, payload_cwd, session_id, gate="skill_load_post_hook"
    ).cook_bypass:
        parts.append(
            "JOIN APPLICABILITY: this authenticated interactive cook session is outside "
            'fixed-set join enforcement. declare_join_batch will answer status "cook_bypass" '
            "without opening a wave; issue the children as ordinary Agent calls, retain "
            "every direct result, and synthesize after all of them return."
        )
    return parts


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
    except Exception:
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

    session_id_value = data.get("session_id", "")
    session_id = session_id_value if isinstance(session_id_value, str) else ""
    invocation = _invocation_skill(data)
    if data.get("agent_id") or invocation is None or not session_id:
        sys.exit(0)
    event_name, skill_name_value = invocation
    skill_name: str = (
        normalize_skill_name(skill_name_value) if isinstance(skill_name_value, str) else ""
    )
    payload_cwd = normalize_payload_cwd(data.get("cwd"))
    try:
        bridge_session_registry(session_id, payload_cwd)
    except Exception as exc:
        sys.stderr.write(f"skill_load_post_hook: registry bridge failed: {exc}\n")
    flag_path = resolve_binding_path(payload_cwd, session_id)

    ts = datetime.now(UTC).isoformat()
    new_entry, binding_error, binding_written = _write_skill_binding(
        flag_path, skill_name=skill_name, session_id=session_id, ts=ts
    )

    if not binding_written:
        record_guard_decision(
            data,
            guard="skill_load_post_hook",
            activation_source="skill_post_hook",
            scope="session_binding",
            decision="allow",
            reason="binding_write_failed",
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
        context_parts.extend(_join_context_parts(data, payload_cwd, session_id, new_entry))

    marker = os.environ.get("AUTOSKILLIT_COMPLETION_MARKER", "").strip()
    if marker:
        context_parts.append(
            "COMPLETION REMINDER: After completing your task, your final text output "
            f"MUST end with exactly: {marker}\n"
            "This is mandatory regardless of what the skill's Output section specifies."
        )
    if context_parts:
        context = "\n\n".join(context_parts)
        payload = json.dumps(
            {"additionalContext": context}
            if event_name == "PostToolUse"
            else {
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptExpansion",
                    "additionalContext": context,
                }
            }
        )
        sys.stdout.write(payload + "\n")

    sys.exit(0)


if __name__ == "__main__":
    main()
