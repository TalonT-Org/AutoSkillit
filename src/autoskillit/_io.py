from __future__ import annotations

import os
import tempfile
from pathlib import Path


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


def ensure_project_temp(project_dir: Path) -> Path:
    """Ensure .autoskillit/temp/ exists with .gitignore.

    Called defensively by any code that needs temp space. Idempotent.
    """
    autoskillit_dir = project_dir / ".autoskillit"
    temp_dir = autoskillit_dir / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    gitignore_path = autoskillit_dir / ".gitignore"
    if not gitignore_path.exists():
        gitignore_path.write_text("temp/\n")
    return temp_dir
