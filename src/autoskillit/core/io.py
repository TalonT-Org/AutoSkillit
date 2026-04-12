"""Filesystem and YAML I/O primitives for the autoskillit package.

Zero autoskillit imports. Provides atomic filesystem writes, project temp directory
management, and YAML load/dump helpers.

All NEW on-disk JSON artifacts SHOULD use ``write_versioned_json`` so schema drift
is detectable. Existing artifacts are tracked in
``tests/infra/test_schema_version_convention.py`` (landed in a later phase).
"""

from __future__ import annotations

import json
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
    "resolve_temp_dir",
    "temp_dir_display_str",
    "write_versioned_json",
]


def resolve_temp_dir(project_dir: Path, override: str | None = None) -> Path:
    """Resolve the autoskillit temp directory for a project.

    Canonical default literal: ``.autoskillit/temp`` — do not change without
    updating ``_TEMP_PATH_WHITELIST`` in ``tests/python_no_hardcoded_temp.py``.

    Precedence:
    - ``override`` absolute: returned as-is.
    - ``override`` relative: anchored to ``project_dir``.
    - ``override`` None: default to ``project_dir/.autoskillit/temp``.

    ``override == ""`` raises ``ValueError``; empty strings must be normalized to
    ``None`` at the ``AutomationConfig.from_dynaconf`` dataclass boundary.
    """
    if override is None:
        return project_dir / ".autoskillit" / "temp"
    if override == "":
        raise ValueError(
            "resolve_temp_dir received empty string; "
            "normalize empty to None at the dataclass boundary"
        )
    p = Path(override)
    return p if p.is_absolute() else project_dir / p


def temp_dir_display_str(override: str | None) -> str:
    """Return the string placed into SKILL.md/recipe YAML for ``override``.

    Mirrors ``resolve_temp_dir`` for string-facing sites (skill content,
    recipe YAML substitution). ``None`` yields the canonical default literal.
    """
    return override or ".autoskillit/temp"


def atomic_write(path: Path, content: str) -> None:
    """Crash-safe write: write to a temp file then os.replace.

    Includes data fsync and directory fsync for durability on ext4/xfs.
    The directory fsync is skipped on Windows (no O_RDONLY semantics).
    """
    import sys as _sys

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())  # durable data write
        os.replace(tmp, path)
        # Durable rename: fsync the parent directory on POSIX
        if _sys.platform != "win32":
            dir_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_versioned_json(path: Path, payload: dict[str, Any], schema_version: int) -> None:
    """Write a dict JSON artifact enriched with ``schema_version``.

    Covers **write atomicity only** (single-writer semantics via
    ``atomic_write``). Callers performing read-modify-write composites
    (e.g. the clone registry) must layer their own ``fcntl.flock`` —
    this helper does not serialize concurrent mutators.

    Raises ``TypeError`` if ``payload`` is not a dict (wrap bare arrays
    as ``{"items": [...]}`` at the call site).
    """
    if not isinstance(payload, dict):
        raise TypeError("write_versioned_json requires a dict payload")
    enriched = {**payload, "schema_version": schema_version}
    atomic_write(path, json.dumps(enriched))


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


def ensure_project_temp(project_dir: Path, override: str | None = None) -> Path:
    """Create the autoskillit temp directory with a self-gitignore; idempotent.

    Uses the pytest/mypy self-gitignoring directory pattern: the temp directory
    owns a ``.gitignore`` containing ``*`` — no mutation of the project root
    ``.gitignore``. Works identically for default, custom-relative, and absolute
    external overrides.

    Also maintains ``.autoskillit/.gitignore`` covering session artefacts that
    land alongside ``config.yaml`` (``.secrets.yaml``, ``.onboarded``,
    ``sync_manifest.json``) when the project uses the canonical ``.autoskillit``
    directory.
    """
    temp_dir = resolve_temp_dir(project_dir, override)
    temp_dir.mkdir(parents=True, exist_ok=True)
    # Race-safe ordering: .gitignore is the FIRST file written after mkdir,
    # before any session content lands. See pytest #12167 / mypy #12442.
    gitignore_path = temp_dir / ".gitignore"
    if not gitignore_path.exists():
        atomic_write(
            gitignore_path,
            "# Created by autoskillit automatically.\n*\n",
        )
    autoskillit_dir = project_dir / ".autoskillit"
    if autoskillit_dir.is_dir():
        autoskillit_gitignore = autoskillit_dir / ".gitignore"
        if not autoskillit_gitignore.exists():
            atomic_write(
                autoskillit_gitignore,
                "\n".join(_AUTOSKILLIT_GITIGNORE_ENTRIES) + "\n",
            )
        else:
            existing = autoskillit_gitignore.read_text(encoding="utf-8")
            missing = [e for e in _AUTOSKILLIT_GITIGNORE_ENTRIES if e not in existing.splitlines()]
            if missing:
                atomic_write(
                    autoskillit_gitignore,
                    existing.rstrip("\n") + "\n" + "\n".join(missing) + "\n",
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
