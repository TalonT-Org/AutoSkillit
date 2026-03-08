"""Filesystem and YAML I/O primitives for the autoskillit package.

Zero autoskillit imports. Provides atomic filesystem writes, project temp directory
management, and YAML load/dump helpers.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any

import yaml
from yaml import YAMLError as YAMLError  # explicit re-export for callers and type checkers

__all__ = [
    "YAMLError",
    "atomic_write",
    "_atomic_write",
    "ensure_project_temp",
    "load_yaml",
    "dump_yaml",
    "dump_yaml_str",
    "_parse_issue_ref",
]

_FULL_URL_RE = re.compile(r"https?://github\.com/([^/]+)/([^/]+)/issues/(\d+)")
_SHORTHAND_RE = re.compile(r"^([^/]+)/([^#]+)#(\d+)$")


def _parse_issue_ref(issue_ref: str) -> tuple[str, str, int]:
    """Parse owner, repo, number from a GitHub issue reference.

    Accepts:
    - Full URL: https://github.com/owner/repo/issues/42
    - Shorthand: owner/repo#42

    Raises ValueError for unrecognised formats (including bare numbers).
    Bare number resolution is the caller's responsibility.
    """
    m = _FULL_URL_RE.match(issue_ref.strip())
    if m:
        return m.group(1), m.group(2), int(m.group(3))
    m = _SHORTHAND_RE.match(issue_ref.strip())
    if m:
        return m.group(1), m.group(2), int(m.group(3))
    raise ValueError(
        f"Cannot parse GitHub issue reference: {issue_ref!r}. "
        "Expected a full URL (https://github.com/owner/repo/issues/N) "
        "or shorthand (owner/repo#N)."
    )


def _atomic_write(path: Path, content: str) -> None:
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


#: Public alias for atomic_write — use this in L1+ code; _atomic_write is the canonical impl.
atomic_write = _atomic_write


def ensure_project_temp(project_dir: Path) -> Path:
    """Create .autoskillit/temp/ with a .gitignore; idempotent. Returns the path."""
    autoskillit_dir = project_dir / ".autoskillit"
    temp_dir = autoskillit_dir / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    gitignore_path = autoskillit_dir / ".gitignore"
    if not gitignore_path.exists():
        _atomic_write(gitignore_path, "temp/\n")
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


def dump_yaml(data: Any, path: Path) -> None:
    """Write data as YAML to path."""
    _atomic_write(path, yaml.dump(data, default_flow_style=False, allow_unicode=True))


def dump_yaml_str(data: Any, **kwargs: Any) -> str:
    """Serialize data to a YAML string.

    Accepts ``yaml.dump`` kwargs (e.g. ``sort_keys=False``,
    ``default_flow_style=False``). Distinct from ``dump_yaml`` which writes
    to disk.
    """
    return yaml.dump(data, **kwargs)
