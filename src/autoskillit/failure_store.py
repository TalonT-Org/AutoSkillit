from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from autoskillit.core.io import _atomic_write


@dataclass
class MigrationFailure:
    name: str
    file_path: str
    file_type: str
    timestamp: str
    error: str
    retries_attempted: int


class FailureStore:
    def __init__(self, store_path: Path) -> None:
        self._path = store_path
        self._state: dict[str, MigrationFailure] = self._load()

    def _load(self) -> dict[str, MigrationFailure]:
        if not self._path.exists():
            return {}
        raw = json.loads(self._path.read_text())
        return {k: MigrationFailure(**v) for k, v in raw.items()}

    def load(self) -> dict[str, MigrationFailure]:
        return dict(self._state)

    def record(
        self,
        name: str,
        file_path: Path,
        file_type: str,
        error: str,
        retries_attempted: int,
    ) -> None:
        candidate = {
            **self._state,
            name: MigrationFailure(
                name=name,
                file_path=str(file_path),
                file_type=file_type,
                timestamp=datetime.now(UTC).isoformat(),
                error=error,
                retries_attempted=retries_attempted,
            ),
        }
        _atomic_write(
            self._path,
            json.dumps({k: asdict(v) for k, v in candidate.items()}, indent=2),
        )
        self._state = candidate

    def clear(self, name: str) -> None:
        if name not in self._state:
            return
        candidate = {k: v for k, v in self._state.items() if k != name}
        _atomic_write(
            self._path,
            json.dumps({k: asdict(v) for k, v in candidate.items()}, indent=2),
        )
        self._state = candidate

    def has_failure(self, name: str) -> bool:
        return name in self._state


def default_store_path(project_dir: Path) -> Path:
    return project_dir / ".autoskillit" / "temp" / "migrations" / "failures.json"


def record_from_skill(
    name: str,
    file_path: str,
    file_type: str,
    error: str,
    retries_attempted: int,
) -> None:
    """Entry point for migrate-recipes SKILL.md via run_python when retries exhausted."""
    store_path = default_store_path(Path.cwd())
    FailureStore(store_path).record(name, Path(file_path), file_type, error, retries_attempted)
