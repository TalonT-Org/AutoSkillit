"""Filesystem and YAML I/O primitives for the autoskillit package.

Zero autoskillit imports. Provides atomic filesystem writes, project temp directory
management, and YAML load/dump helpers.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import yaml
from yaml import YAMLError as YAMLError  # explicit re-export for callers and type checkers

__all__ = [
    "YAMLError",
    "atomic_write",
    "ensure_project_temp",
    "load_yaml",
    "dump_yaml_str",
]


def atomic_write(path: Path, content: str) -> None:
    """Crash-safe write: write to a temp file then os.replace."""
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


_AUTOSKILLIT_GITIGNORE_ENTRIES = ["temp/", ".secrets.yaml", ".onboarded", "sync_manifest.json"]

_ROOT_GITIGNORE_ENTRIES = [
    ".autoskillit/.secrets.yaml",
    ".autoskillit/temp/",
    ".autoskillit/.onboarded",
    ".autoskillit/sync_manifest.json",
]

_COMMITTED_BY_DESIGN: frozenset[str] = frozenset(
    {
        "config.yaml",
        "recipes",
    }
)


def ensure_project_temp(project_dir: Path) -> Path:
    """Create .autoskillit/temp/ with a .gitignore; idempotent. Returns the path."""
    autoskillit_dir = project_dir / ".autoskillit"
    temp_dir = autoskillit_dir / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    gitignore_path = autoskillit_dir / ".gitignore"
    if not gitignore_path.exists():
        atomic_write(gitignore_path, "\n".join(_AUTOSKILLIT_GITIGNORE_ENTRIES) + "\n")
    else:
        existing = gitignore_path.read_text(encoding="utf-8")
        missing = [e for e in _AUTOSKILLIT_GITIGNORE_ENTRIES if e not in existing]
        if missing:
            atomic_write(gitignore_path, existing.rstrip("\n") + "\n" + "\n".join(missing) + "\n")
    root_gitignore = project_dir / ".gitignore"
    if not root_gitignore.exists():
        atomic_write(root_gitignore, "\n".join(_ROOT_GITIGNORE_ENTRIES) + "\n")
    else:
        existing_root = root_gitignore.read_text(encoding="utf-8")
        missing_root = [e for e in _ROOT_GITIGNORE_ENTRIES if e not in existing_root]
        if missing_root:
            atomic_write(
                root_gitignore,
                existing_root.rstrip("\n") + "\n" + "\n".join(missing_root) + "\n",
            )
    return temp_dir


def load_yaml(source: os.PathLike[str] | str) -> Any:
    """Load YAML from a file path or raw string.

    Pass any ``os.PathLike`` (including ``pathlib.Path``) to read from disk,
    or a ``str`` to parse directly. Uses binary mode for portable UTF-8/BOM
    handling when reading from a path.
    """
    if isinstance(source, os.PathLike):
        with open(source, "rb") as fh:
            return yaml.safe_load(fh)
    return yaml.safe_load(source)


def dump_yaml_str(data: Any, **kwargs: Any) -> str:
    """Serialize data to a YAML string.

    Accepts ``yaml.dump`` kwargs (e.g. ``sort_keys=False``,
    ``default_flow_style=False``). Distinct from the removed ``dump_yaml`` which wrote
    to disk.
    """
    return yaml.dump(data, **kwargs)
