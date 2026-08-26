"""Shared projection fixtures for session-binding hook tests."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

_HOOKS_SOURCE = Path(__file__).resolve().parents[2] / "src" / "autoskillit" / "hooks"
_PROJECTED_HOOK_FILES = (
    "skill_load_post_hook.py",
    "_hook_payload.py",
    "_hook_settings.py",
    "_session_binding.py",
)


def copy_projected_hook(tmp_path: Path, name: str = "join-plugin") -> tuple[Path, Path]:
    """Copy the stdlib-only hook runtime under a projected plugin root."""
    projection_root = tmp_path / name
    hooks_dir = projection_root / "hooks"
    hooks_dir.mkdir(parents=True)
    for filename in _PROJECTED_HOOK_FILES:
        shutil.copy2(_HOOKS_SOURCE / filename, hooks_dir / filename)
    return projection_root, hooks_dir / "skill_load_post_hook.py"


def write_projection_manifest(
    projection_root: Path,
    *,
    skill_name: str = "join-bearing",
    join_required: bool = True,
    schema_version: int = 2,
    artifact_digest: str = "artdigest-1",
    semantic_digest: str = "sem-1",
    adaptation_digest: str = "adapt-1",
    projected_digest: str = "proj-1",
    canonical_digest: str = "canon-1",
    child_spawn_cardinality: dict[str, object] | None = None,
) -> Path:
    """Write a schema-versioned projection sidecar beside a projected hook root."""
    manifest_path = projection_root.parent / (
        f".{projection_root.name}.autoskillit-projection.json"
    )
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": schema_version,
                "artifact_kind": "projection",
                "projection_version": 1,
                "semantic_key": "autoskillit@session-binding-test:1",
                "incarnation_id": "00000000000040008000000000000001",
                "artifact_digest": artifact_digest,
                "skills": {
                    skill_name: {
                        "join_required": join_required,
                        "child_spawn_cardinality": (
                            child_spawn_cardinality
                            if child_spawn_cardinality is not None
                            else {"explicit_slots": 4}
                        ),
                        "semantic_digest": semantic_digest,
                        "adaptation_digest": adaptation_digest,
                        "projected_digest": projected_digest,
                        "canonical_digest": canonical_digest,
                        "artifact_digest": "",
                        "artifact_incarnation": "",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return manifest_path
