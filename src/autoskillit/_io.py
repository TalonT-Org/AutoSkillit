"""Atomic filesystem write primitive for AutoSkillit.

This module owns low-level, process-agnostic file I/O. Its single
public primitive, ``_atomic_write``, guarantees crash-safe writes by
writing to a temp file and using ``os.replace`` for an atomic rename.

It is intentionally separate from ``process_lifecycle.py``, whose I/O
utilities (``_temp_output_file``, ``_read_and_cleanup``) are specific
to subprocess pipe management and are not general filesystem primitives.

Callers: ``failure_store.py`` (``_atomic_write``), and any module that
needs safe YAML loading via ``_load_yaml``.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import yaml


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


def _load_yaml(source: os.PathLike[str] | str) -> Any:
    """Load YAML from a file path or raw string.

    Pass any ``os.PathLike`` (including ``pathlib.Path``) to read from disk,
    or a ``str`` to parse directly. Uses binary mode for portable UTF-8/BOM
    handling regardless of system locale. Raises ``yaml.YAMLError`` on invalid
    YAML; ``FileNotFoundError`` if path not found.
    """
    if isinstance(source, os.PathLike):
        with open(source, "rb") as fh:  # binary: PyYAML handles UTF-8/BOM portably
            return yaml.safe_load(fh)
    return yaml.safe_load(source)


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
