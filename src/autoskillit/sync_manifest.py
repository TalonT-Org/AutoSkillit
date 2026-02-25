from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from autoskillit import recipe_parser
from autoskillit._io import _atomic_write


def compute_recipe_hash(content: str) -> str:
    return f"sha256:{hashlib.sha256(content.encode()).hexdigest()}"


@dataclass
class SyncManifestEntry:
    hash: str
    written_at: str


class SyncManifest:
    def __init__(self, store_path: Path) -> None:
        self._path = store_path

    def load(self) -> dict[str, SyncManifestEntry]:
        if not self._path.exists():
            return {}
        raw = json.loads(self._path.read_text())
        return {k: SyncManifestEntry(**v) for k, v in raw.items()}

    def record(self, recipe_name: str, content: str) -> None:
        entries = self.load()
        entries[recipe_name] = SyncManifestEntry(
            hash=compute_recipe_hash(content),
            written_at=datetime.now(UTC).isoformat(),
        )
        _atomic_write(
            self._path,
            json.dumps(
                {k: {"hash": v.hash, "written_at": v.written_at} for k, v in entries.items()},
                indent=2,
            ),
        )

    def get_hash(self, recipe_name: str) -> str | None:
        entries = self.load()
        entry = entries.get(recipe_name)
        return entry.hash if entry is not None else None


@dataclass
class SyncDecision:
    decision: str  # "accept" | "decline"
    bundled_hash: str
    timestamp: str


class SyncDecisionStore:
    def __init__(self, store_path: Path) -> None:
        self._path = store_path

    def _key(self, recipe_name: str, bundled_hash: str) -> str:
        return f"{recipe_name}::{bundled_hash}"

    def load(self) -> dict[str, SyncDecision]:
        if not self._path.exists():
            return {}
        raw = json.loads(self._path.read_text())
        return {k: SyncDecision(**v) for k, v in raw.items()}

    def record_decline(self, recipe_name: str, bundled_hash: str) -> None:
        self._record(recipe_name, bundled_hash, "decline")

    def record_accept(self, recipe_name: str, bundled_hash: str) -> None:
        self._record(recipe_name, bundled_hash, "accept")

    def _record(self, recipe_name: str, bundled_hash: str, decision: str) -> None:
        decisions = self.load()
        key = self._key(recipe_name, bundled_hash)
        decisions[key] = SyncDecision(
            decision=decision,
            bundled_hash=bundled_hash,
            timestamp=datetime.now(UTC).isoformat(),
        )
        _atomic_write(
            self._path,
            json.dumps(
                {
                    k: {
                        "decision": v.decision,
                        "bundled_hash": v.bundled_hash,
                        "timestamp": v.timestamp,
                    }
                    for k, v in decisions.items()
                },
                indent=2,
            ),
        )

    def is_declined(self, recipe_name: str, bundled_hash: str) -> bool:
        decisions = self.load()
        key = self._key(recipe_name, bundled_hash)
        entry = decisions.get(key)
        return entry is not None and entry.decision == "decline"


def default_manifest_path(project_dir: Path) -> Path:
    return project_dir / ".autoskillit" / "sync_manifest.json"


def default_decision_path(project_dir: Path) -> Path:
    return project_dir / ".autoskillit" / "sync_decisions.json"


def accept_recipe_update(recipe_name: str) -> None:
    """Accept a bundle update: overwrite local with bundled content and record the decision."""
    project_dir = Path.cwd()
    bundled_dir = recipe_parser.builtin_recipes_dir()
    src = bundled_dir / f"{recipe_name}.yaml"
    local_path = project_dir / ".autoskillit" / "recipes" / src.name
    bundled_content = src.read_text()
    local_path.write_text(bundled_content)
    manifest = SyncManifest(default_manifest_path(project_dir))
    manifest.record(recipe_name, bundled_content)
    bundled_hash = compute_recipe_hash(bundled_content)
    decisions = SyncDecisionStore(default_decision_path(project_dir))
    decisions.record_accept(recipe_name, bundled_hash)


def decline_recipe_update(recipe_name: str) -> None:
    """Decline the current bundle update. Suppresses future advisories for this bundle version."""
    project_dir = Path.cwd()
    bundled_dir = recipe_parser.builtin_recipes_dir()
    src = bundled_dir / f"{recipe_name}.yaml"
    bundled_hash = compute_recipe_hash(src.read_text())
    decisions = SyncDecisionStore(default_decision_path(project_dir))
    decisions.record_decline(recipe_name, bundled_hash)
