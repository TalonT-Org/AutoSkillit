"""Filesystem and YAML I/O primitives for the autoskillit package.

Zero autoskillit imports. Provides atomic filesystem writes, project temp directory
management, and YAML load/dump helpers.

All NEW on-disk JSON artifacts SHOULD use ``write_versioned_json`` so schema drift
is detectable. Existing artifacts are tracked in
``tests/infra/test_schema_version_convention.py`` (landed in a later phase).
"""

from __future__ import annotations

import os
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import yaml
from yaml import YAMLError as YAMLError  # explicit re-export for callers and type checkers

from ._json import fast_dumps as _fast_dumps
from .types._type_helpers import extract_skill_name

try:
    from yaml import CSafeLoader as _Loader
except ImportError:
    _Loader = yaml.SafeLoader  # type: ignore[misc,assignment]

try:
    from yaml import CDumper as _Dumper
except ImportError:
    from yaml import Dumper as _Dumper  # type: ignore[misc,assignment]

__all__ = [
    "ReadResult",
    "YAMLError",
    "atomic_write",
    "ensure_project_temp",
    "load_yaml",
    "dump_yaml_str",
    "read_versioned_json",
    "resolve_skill_temp_dir",
    "resolve_temp_dir",
    "safe_upsert_section",
    "temp_dir_display_str",
    "write_versioned_json",
]


@dataclass
class ReadResult:
    """Discriminated result of a config file read operation.

    Use the factory classmethods to construct instances; do not instantiate directly.
    Callers that want to write back after reading must branch on ``is_corrupt``:
    corrupt sources must use text-level operations to preserve unreadable content.
    """

    data: dict[str, Any]
    raw_bytes: bytes | None
    is_corrupt: bool

    @classmethod
    def missing(cls, default: dict[str, Any]) -> ReadResult:
        return cls(data=default, raw_bytes=None, is_corrupt=False)

    @classmethod
    def ok(cls, data: dict[str, Any]) -> ReadResult:
        return cls(data=data, raw_bytes=None, is_corrupt=False)

    @classmethod
    def corrupt(cls, raw_bytes: bytes) -> ReadResult:
        return cls(data={}, raw_bytes=raw_bytes, is_corrupt=True)


class _WarningLogger(Protocol):
    def warning(self, event: str, /, **kw: Any) -> None: ...


_SCHEMA_DRIFT_LOGGED: set[tuple[str, int]] = set()
_SCHEMA_DRIFT_LOCK = threading.Lock()


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


def resolve_skill_temp_dir(cwd: str, skill_command: str) -> Path | None:
    """Return the default write-watch directory for a skill invoked ad-hoc.

    Used when no ``output_dir`` is provided — falls back to
    ``<cwd>/.autoskillit/temp/<skill-name>/``. Returns ``None`` when the
    skill name cannot be extracted from ``skill_command``.
    """
    name = extract_skill_name(skill_command)
    if not name:
        return None
    return Path(cwd) / ".autoskillit" / "temp" / name


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
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    # Durable rename: fsync the parent directory on POSIX.
    # Best-effort: os.replace() already committed the rename; a dir_fsync
    # failure here does not undo the write.
    if _sys.platform != "win32":
        try:
            dir_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass  # Non-fatal — data is durable at path after os.replace()


def safe_upsert_section(
    path: Path,
    section_header: str,
    section_text: str,
    *,
    end_marker: str | None = None,
) -> None:
    """Replace or append a ``[single.section]`` TOML section in a text file.

    Operates on raw text — safe to use on files that fail TOML parsing. Only
    suitable for ``[single.section]`` headers; for ``[[array.of.tables]]``
    headers use a dedicated helper (``_upsert_hooks_text`` in cli/_hooks_codex.py).

    If ``section_header`` is found, the region from that header to the next
    line starting with ``[`` (or ``end_marker``, or EOF) is replaced with
    ``section_text``. If not found, ``section_text`` is appended.
    Writes back via ``atomic_write``.
    """
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = existing.splitlines(keepends=True)

    start_idx: int | None = None
    end_idx: int | None = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == section_header:
            start_idx = i
            continue
        if start_idx is not None and end_idx is None:
            if end_marker is not None and stripped == end_marker:
                end_idx = i
                break
            if stripped.startswith("[") and stripped != section_header:
                end_idx = i
                break

    if start_idx is not None:
        if end_idx is None:
            end_idx = len(lines)
        new_lines = lines[:start_idx] + [section_text] + lines[end_idx:]
    else:
        separator = "\n\n" if existing.strip() else ""
        new_lines = lines + [separator + section_text]

    atomic_write(path, "".join(new_lines))


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
    atomic_write(path, _fast_dumps(enriched, indent=True))


def read_versioned_json(
    path: Path,
    expected_version: int,
    *,
    logger: _WarningLogger | None = None,
) -> dict[str, Any] | None:
    """Read a versioned JSON artifact and validate its schema_version.

    Returns the parsed dict on version match, None on any failure
    (missing file, corrupt JSON, non-dict, missing/mismatched schema_version).
    Logs a deduped drift warning on version mismatch.
    """
    import json as _json
    import warnings

    try:
        raw = _json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, _json.JSONDecodeError, OSError):
        return None
    if not isinstance(raw, dict):
        return None
    observed = raw.get("schema_version")
    if observed != expected_version:
        cache_key = (str(path.resolve()), expected_version)
        with _SCHEMA_DRIFT_LOCK:
            if cache_key not in _SCHEMA_DRIFT_LOGGED:
                _SCHEMA_DRIFT_LOGGED.add(cache_key)
            else:
                return None
        if logger is not None:
            logger.warning(
                "schema_drift",
                path=str(path),
                expected=expected_version,
                observed=observed,
            )
        else:
            warnings.warn(
                f"schema_drift: path={path} expected={expected_version} observed={observed}",
                stacklevel=2,
            )
        return None
    return raw


def _reset_schema_drift_logged_for_tests() -> None:
    """Test-only helper: clear the once-per-process drift-log set."""
    _SCHEMA_DRIFT_LOGGED.clear()


_AUTOSKILLIT_GITIGNORE_ENTRIES = [
    "temp/",
    ".secrets.yaml",
    ".onboarded",
    "sync_manifest.json",
    "test-filter-manifest.yaml",
    "validation-errors/",
]

_COMMITTED_BY_DESIGN: frozenset[str] = frozenset(
    {
        "config.yaml",
        "recipes",
        "test-source-map.json",
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
            return yaml.load(fh, Loader=_Loader)
    return yaml.load(source, Loader=_Loader)


def dump_yaml_str(data: Any, **kwargs: Any) -> str:
    """Serialize data to a YAML string.

    Accepts ``yaml.dump`` kwargs (e.g. ``sort_keys=False``,
    ``default_flow_style=False``). Distinct from the removed ``dump_yaml`` which wrote
    to disk.
    """
    kwargs.pop("Dumper", None)
    return yaml.dump(data, Dumper=_Dumper, **kwargs)
