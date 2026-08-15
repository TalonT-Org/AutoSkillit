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


def _resolve_manifest_path(project_root: Path) -> Path | None:
    """Locate the projection manifest sidecar for the active plugin install.

    Resolution order:
      1. ``AUTOSKILLIT_PROJECTION_MANIFEST_PATH`` env var (explicit override).
      2. Sibling ``.{plugin_dir}.autoskillit-projection.json`` files under the
         project's ``.claude/plugins/installed/`` and ``.claude/`` trees, plus
         the project root itself.
    """
    explicit = os.environ.get("AUTOSKILLIT_PROJECTION_MANIFEST_PATH", "").strip()
    if explicit:
        candidate = Path(explicit)
        if candidate.exists():
            return candidate
    candidates: list[Path] = []
    for base in (
        project_root / ".claude",
        project_root / ".autoskillit" / "plugins",
    ):
        if not base.exists():
            continue
        for installed in base.rglob("*"):
            if not installed.is_dir():
                continue
            manifest = installed.parent / f".{installed.name}.autoskillit-projection.json"
            if manifest.exists():
                candidates.append(manifest)
        manifest = base / f".{base.name}.autoskillit-projection.json"
        if manifest.exists():
            candidates.append(manifest)
    return candidates[0] if candidates else None


def _read_manifest_entry(
    manifest_path: Path,
    skill_name: str,
) -> dict[str, object] | None:
    """Return the manifest entry for ``skill_name`` or ``None`` when absent/invalid."""
    try:
        raw = manifest_path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return None
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    skills = parsed.get("skills")
    if not isinstance(skills, dict):
        return None
    entry = skills.get(skill_name)
    return entry if isinstance(entry, dict) else None


def _build_entry_from_manifest(
    skill_name: str,
    manifest_entry: dict[str, object] | None,
    ts: str,
) -> dict[str, object]:
    """Build one loaded-skill entry from the projection manifest.

    When the manifest entry is present, every documented field is sourced
    from it verbatim. When absent, all semantic/identity fields are forced
    to ``join_required: true`` and the binding is marked invalid so
    downstream guards fail closed.
    """
    if manifest_entry is None:
        return {
            "skill_name": skill_name,
            "ts": ts,
            "join_required": True,
            "child_spawn_cardinality": {},
            "semantic_digest": "",
            "adaptation_digest": "",
            "artifact_digest": "",
            "artifact_incarnation": "",
            "binding_valid": False,
            "binding_error": "manifest entry not found",
        }
    cardinality_raw = manifest_entry.get("child_spawn_cardinality", {})
    cardinality: dict[str, object] = (
        dict(cardinality_raw) if isinstance(cardinality_raw, dict) else {}
    )
    return {
        "skill_name": skill_name,
        "ts": ts,
        "join_required": bool(manifest_entry.get("join_required", False)),
        "child_spawn_cardinality": cardinality,
        "semantic_digest": str(manifest_entry.get("semantic_digest", "")),
        "adaptation_digest": str(manifest_entry.get("adaptation_digest", "")),
        "projected_digest": str(manifest_entry.get("projected_digest", "")),
        "canonical_digest": str(manifest_entry.get("canonical_digest", "")),
        "artifact_digest": str(manifest_entry.get("artifact_digest", "")),
        "artifact_incarnation": str(manifest_entry.get("artifact_incarnation", "")),
        "binding_valid": True,
    }


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

    project_root = find_project_root()
    flag_path = project_root / ".autoskillit" / "temp" / f"skill_guard_{session_id}.flag"

    existing = _read_existing_flag(flag_path)

    ts = datetime.now(UTC).isoformat()
    manifest_path = _resolve_manifest_path(project_root)
    if manifest_path is not None:
        manifest_entry = _read_manifest_entry(manifest_path, skill_name)
    else:
        manifest_entry = None
    new_entry = _build_entry_from_manifest(skill_name, manifest_entry, ts)

    merged = (
        _merge_existing_entry(existing or {}, new_entry)
        if existing
        else {
            "schema_version": 1,
            "session_id": session_id,
            "join_required": bool(new_entry.get("join_required", False)),
            "binding_valid": bool(new_entry.get("binding_valid", False)),
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
