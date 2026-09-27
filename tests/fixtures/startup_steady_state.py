"""Production-shaped startup state shared by hook-repair, install-state and quota tests."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from autoskillit.core import (
    RetiringAppendResult,
    managed_home_for,
    new_plugin_artifact_incarnation_id,
)


def plant_stale_projection(
    home: Path,
    semantic_key: str,
    *,
    incarnation_id: str | None = None,
) -> tuple[Path, Path, Path, Path, Path]:
    """Plant one projection whose hooks point at a deleted dispatcher.

    Returns ``(projections_root, projection, hooks_path, manifest_path, lease_path)``.
    """
    from autoskillit.workspace._installed._projection_cache import (
        PROJECTION_ARTIFACT_MANIFEST_SCHEMA_VERSION,
        projected_artifact_lease_path,
        projected_artifact_manifest_path,
        projected_plugin_artifact_digest,
    )

    projections_root = home / ".autoskillit" / "plugin-projections"
    projection = projections_root / semantic_key
    hooks_dir = projection / "hooks"
    hooks_dir.mkdir(parents=True)
    (hooks_dir / "_dispatch.py").write_text("# dispatcher\n")
    hooks_path = hooks_dir / "hooks.json"
    hooks_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Read",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "python3 /deleted/hooks/_dispatch.py foo",
                                }
                            ],
                        }
                    ]
                }
            },
            indent=2,
        )
        + "\n"
    )
    manifest_path = projected_artifact_manifest_path(projection)
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": PROJECTION_ARTIFACT_MANIFEST_SCHEMA_VERSION,
                "artifact_kind": "projection",
                "projection_version": 2,
                "semantic_key": semantic_key,
                "incarnation_id": incarnation_id or new_plugin_artifact_incarnation_id(),
                "artifact_digest": projected_plugin_artifact_digest(projection),
                "skills": {},
            },
            indent=2,
        )
        + "\n"
    )
    lease_path = projected_artifact_lease_path(projection)
    lease_path.parent.mkdir(parents=True, exist_ok=True)
    return projections_root, projection, hooks_path, manifest_path, lease_path


def enqueue_projection_retirement(
    home: Path,
    projection: Path,
    *,
    not_before: datetime,
) -> RetiringAppendResult | None:
    """Queue *projection*'s exact current identity in ``home``'s retirement queue."""
    from autoskillit.workspace import ProjectedPluginRetirementOwner

    owner = ProjectedPluginRetirementOwner(projection.parent, home=managed_home_for(home))
    return owner.enqueue_retirement(owner.identity_for_path(projection), not_before)


def write_migrated_legacy_evidence(home: Path, legacy_path: Path) -> None:
    """Migrate one path-only v1 retirement entry for ``legacy_path`` into v2 legacy evidence."""
    from autoskillit.core import (
        PluginArtifactKind,
        migrate_retiring_cache_v1,
        write_versioned_json,
    )

    write_versioned_json(
        home / ".autoskillit" / "retiring_cache.json",
        {
            "retiring": [
                {
                    "version": "legacy",
                    "path": str(legacy_path),
                    "retired_at": "2025-01-01T00:00:00+00:00",
                }
            ]
        },
        schema_version=1,
    )
    migrate_retiring_cache_v1(
        {PluginArtifactKind.INSTALLED_PLUGIN: legacy_path.parent},
        home=managed_home_for(home),
    )


DORMANT_QUOTA_WINDOW = {"utilization": 0.0, "resets_at": None}


def fake_quota_http_client(api_response: dict[str, Any]) -> Any:
    """Return a fake ``httpx.AsyncClient`` that serves ``api_response`` for GET requests."""

    class FakeResponse:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return api_response

        def raise_for_status(self) -> None:
            pass

    class FakeClient:
        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *a: object) -> None:
            pass

        async def get(self, *a: object, **kw: object) -> FakeResponse:
            return FakeResponse()

    return FakeClient()
