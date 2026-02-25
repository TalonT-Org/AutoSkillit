from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass
class MigrationFailure:
    name: str
    file_path: str
    file_type: str
    timestamp: str
    error: str
    retries_attempted: int


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class FailureStore:
    def __init__(self, store_path: Path) -> None:
        self._path = store_path

    def load(self) -> dict[str, MigrationFailure]:
        if not self._path.exists():
            return {}
        raw = json.loads(self._path.read_text())
        return {k: MigrationFailure(**v) for k, v in raw.items()}

    def record(
        self,
        name: str,
        file_path: Path,
        file_type: str,
        error: str,
        retries_attempted: int,
    ) -> None:
        failures = self.load()
        failures[name] = MigrationFailure(
            name=name,
            file_path=str(file_path),
            file_type=file_type,
            timestamp=datetime.now(UTC).isoformat(),
            error=error,
            retries_attempted=retries_attempted,
        )
        _atomic_write(
            self._path, json.dumps({k: asdict(v) for k, v in failures.items()}, indent=2)
        )

    def clear(self, name: str) -> None:
        failures = self.load()
        if name not in failures:
            return
        del failures[name]
        _atomic_write(
            self._path, json.dumps({k: asdict(v) for k, v in failures.items()}, indent=2)
        )

    def has_failure(self, name: str) -> bool:
        return name in self.load()


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
