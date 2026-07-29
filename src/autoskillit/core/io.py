"""Filesystem and YAML I/O primitives for the autoskillit package.

Zero autoskillit imports. Provides atomic filesystem writes, project temp directory
management, and YAML load/dump helpers.

New on-disk JSON artifacts fall into two families. Default to ``write_versioned_json``
so schema drift is detectable; existing sites are tracked in
``tests/infra/test_schema_version_convention.py``. Use ``write_canonical_versioned_json``
instead when the artifact's reader will call
``decode_versioned_json_bytes(..., require_canonical=True)`` for tamper-evident,
hash-bound content addressing — every such producer/consumer pairing must be
registered in ``tests/infra/test_canonical_json_producer_convention.py``.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
import tempfile
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import yaml
from yaml import YAMLError as YAMLError  # explicit re-export for callers and type checkers

from ._json import fast_dumps as _fast_dumps
from .types._type_helpers import extract_skill_name
from .types._type_results import SpilledOutput, SpillSpec

try:
    from yaml import CSafeLoader as _Loader
except ImportError:
    _Loader = yaml.SafeLoader  # type: ignore[misc,assignment]


class _UniqueKeyLoader(_Loader):
    """Safe loader that rejects duplicate mapping keys before construction."""


def _construct_unique_mapping(
    loader: _UniqueKeyLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable key",
                key_node.start_mark,
            ) from exc
        if duplicate:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)

try:
    from yaml import CDumper as _Dumper
except ImportError:
    from yaml import Dumper as _Dumper  # type: ignore[misc,assignment]


class _AtomicWriteDurabilityError(OSError):
    """The target was replaced, but its parent directory was not made durable."""

    def __init__(self, path: Path, cause: OSError) -> None:
        self.path = Path(path)
        super().__init__(
            f"atomic write committed but parent durability failed for {self.path}: {cause}"
        )


__all__ = [
    "ReadResult",
    "YAMLError",
    "atomic_write",
    "compose_yaml",
    "ensure_project_temp",
    "load_yaml",
    "mapping_entry_byte_ranges_from_yaml",
    "dump_yaml_str",
    "decode_versioned_json_bytes",
    "read_versioned_json",
    "resolve_skill_temp_dir",
    "resolve_temp_dir",
    "safe_upsert_section",
    "spill_output",
    "temp_dir_display_str",
    "write_versioned_json",
    "write_canonical_versioned_json",
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

    def __post_init__(self) -> None:
        if self.is_corrupt and self.raw_bytes is None:
            raise ValueError("corrupt ReadResult must carry raw_bytes")

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


def atomic_write(
    path: Path,
    content: str,
    *,
    strict_durability: bool = False,
    exclusive: bool = False,
) -> None:
    """Crash-safe write: write to a temp file then os.replace.

    Includes data fsync and directory fsync for durability on ext4/xfs.
    The directory fsync is skipped on Windows (no O_RDONLY semantics).

    When ``exclusive`` is True, atomically claims ``path`` via
    ``os.O_CREAT | os.O_EXCL`` before writing, raising ``FileExistsError``
    with no bytes written if the destination already exists. Closes the
    TOCTOU window between a separate existence check and the write.
    """
    import sys as _sys

    path.parent.mkdir(parents=True, exist_ok=True)
    if exclusive:
        os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
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
        if exclusive:
            try:
                os.unlink(path)
            except OSError:
                pass
        raise
    # Durable rename: fsync the parent directory on POSIX.
    # Default callers retain best-effort parent durability. Identity-bearing
    # stores opt into strict mode so a failed directory fsync is observable.
    if _sys.platform != "win32":
        try:
            dir_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError as exc:
            if strict_durability:
                raise _AtomicWriteDurabilityError(path, exc) from exc


def directory_tree_digest(root: Path) -> str:
    """Hash every relative entry, kind, mode, and regular-file byte."""
    root = Path(root)
    if not root.is_dir() or root.is_symlink():
        raise ValueError(f"artifact root is not a regular directory: {root}")
    digest = hashlib.sha256()
    for current_root, directory_names, file_names in os.walk(root, followlinks=False):
        current = Path(current_root)
        directory_names.sort()
        file_names.sort()
        for name in (*directory_names, *file_names):
            path = current / name
            relative = path.relative_to(root).as_posix()
            entry_stat = path.stat(follow_symlinks=False)
            if stat.S_ISLNK(entry_stat.st_mode):
                raise ValueError(f"artifact contains a symlink: {path}")
            if stat.S_ISDIR(entry_stat.st_mode):
                kind = b"d"
            elif stat.S_ISREG(entry_stat.st_mode):
                kind = b"f"
            else:
                raise ValueError(f"artifact contains a special file: {path}")
            digest.update(kind)
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            digest.update(stat.S_IMODE(entry_stat.st_mode).to_bytes(2, "big"))
            if kind == b"f":
                with path.open("rb") as handle:
                    digest.update(hashlib.file_digest(handle, "sha256").digest())
            digest.update(b"\0")
    return digest.hexdigest()


def spill_output(
    text: str,
    artifact_dir: Path,
    name_hint: str,
    spec: SpillSpec,
) -> SpilledOutput:
    """Persist oversized text losslessly and return a bounded inline preview.

    The final artifact path is exposed only after ``atomic_write`` has published
    the complete UTF-8 payload. Small values remain byte-for-byte inline and do
    not create an artifact.
    """
    encoded = text.encode("utf-8")
    total_lines = len(text.splitlines())
    if len(text) <= spec.inline_max_chars:
        return SpilledOutput(
            spilled=False,
            text=text,
            artifact_path=None,
            total_chars=len(text),
            total_utf8_bytes=len(encoded),
            total_lines=total_lines,
        )

    safe_hint = re.sub(r"[^A-Za-z0-9._-]+", "_", name_hint).strip("._-") or "output"
    final_path = artifact_dir / f"{safe_hint}_{uuid.uuid4().hex[:8]}.log"
    atomic_write(final_path, text)
    published_path = str(final_path.resolve())
    head = text[: spec.head_chars]
    tail = text[-spec.tail_chars :] if spec.tail_chars else ""
    marker = f"[spilled {len(text)} chars -> {published_path}]"
    preview_parts = [head, marker, tail]
    preview = "\n".join(part for part in preview_parts if part)
    return SpilledOutput(
        spilled=True,
        text=preview,
        artifact_path=published_path,
        head=head,
        tail=tail,
        sha256=hashlib.sha256(encoded).hexdigest(),
        total_chars=len(text),
        total_utf8_bytes=len(encoded),
        total_lines=total_lines,
    )


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


def write_versioned_json(
    path: Path,
    payload: dict[str, Any],
    schema_version: int,
    *,
    strict_durability: bool = False,
) -> None:
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
    atomic_write(
        path,
        _fast_dumps(enriched, indent=True),
        strict_durability=strict_durability,
    )


def write_canonical_versioned_json(
    path: Path,
    payload: dict[str, Any],
    schema_version: int,
    *,
    exclusive: bool = False,
) -> None:
    """Atomically write versioned canonical JSON for hash-bound artifacts."""
    from .closure_hashing import canonical_json_bytes

    if not isinstance(payload, dict):
        raise TypeError("write_canonical_versioned_json requires a dict payload")
    enriched = {**payload, "schema_version": schema_version}
    atomic_write(path, canonical_json_bytes(enriched).decode("utf-8"), exclusive=exclusive)


def decode_versioned_json_bytes(
    data: bytes,
    expected_version: int,
    *,
    require_canonical: bool = False,
) -> dict[str, Any] | None:
    """Decode one caller-owned byte buffer and enforce its schema version."""
    import json as _json

    try:
        if require_canonical:
            from .closure_hashing import parse_canonical_json_bytes

            raw = parse_canonical_json_bytes(data)
        else:
            raw = _json.loads(data.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, _json.JSONDecodeError, ValueError):
        return None
    if not isinstance(raw, dict) or raw.get("schema_version") != expected_version:
        return None
    return raw


def read_versioned_json(
    path: Path,
    expected_version: int,
    *,
    logger: _WarningLogger | None = None,
    raise_io_errors: bool = False,
) -> dict[str, Any] | None:
    """Read a versioned JSON artifact and validate its schema_version.

    Returns the parsed dict on version match, None on a missing file, corrupt
    JSON, non-dict, or missing/mismatched schema_version. When
    ``raise_io_errors`` is true, non-missing filesystem errors are re-raised so
    artifact authorities can distinguish transient unreadability from absence.
    Logs a deduped drift warning on version mismatch.
    """
    import json as _json
    import warnings

    try:
        raw = _json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except OSError:
        if raise_io_errors:
            raise
        return None
    except _json.JSONDecodeError:
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
    "retiring_cache.lock",
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
            return yaml.load(fh, Loader=_UniqueKeyLoader)
    return yaml.load(source, Loader=_UniqueKeyLoader)


def compose_yaml(source: str) -> yaml.Node | None:
    """Parse *source* into a mark-annotated YAML node tree (not a data structure).

    Unlike :func:`load_yaml`, retains ``start_mark`` / ``end_mark`` character
    offsets on every node, which the byte-range tracker in
    ``server/tools/_serve_helpers.py`` uses to compute per-step byte spans
    of the original ``content`` text. Returns ``None`` when the source is
    empty (matches :func:`yaml.compose` semantics).
    """
    return yaml.compose(source, Loader=_Loader)


def mapping_entry_byte_ranges_from_yaml(
    content: str, mapping_path: tuple[str, ...]
) -> dict[str, tuple[int, int]]:
    """Compute UTF-8 byte ranges for entries under a YAML mapping path.

    Walks the persisted YAML ``content`` field via :func:`compose_yaml` to read
    each selected mapping entry's key/value ``start_mark`` / ``end_mark`` character
    offsets, then converts them to UTF-8 byte offsets so the result can be
    used directly to slice the payload back at the byte level.

    Fails open: returns ``{}`` on any malformed or non-mapping document. The
    guards (rather than a bare ``except YAMLError``) handle the documented
    case where ``yaml.compose`` succeeds but produces a non-mapping root
    (a bare sequence, or a ``steps:`` key whose value is a scalar) — a bare
    ``except`` would miss ``TypeError`` / ``ValueError`` raised from
    tuple-unpacking such a non-mapping node tree.

    Centralizes the yaml import: this module is the only place in the
    package that imports ``yaml`` directly (REQs in
    ``tests/arch/test_subpackage_isolation.py`` and
    ``tests/core/test_io.py::test_only_yaml_imports_yaml_directly``).
    """
    out: dict[str, tuple[int, int]] = {}
    if not content or not mapping_path:
        return out
    try:
        root = compose_yaml(content)
    except yaml.YAMLError:
        return out
    if not isinstance(root, yaml.MappingNode):
        return out
    current = root
    for segment in mapping_path:
        next_node = None
        for key_node, value_node in current.value:
            if getattr(key_node, "value", None) == segment:
                next_node = value_node
                break
        if not isinstance(next_node, yaml.MappingNode):
            return out
        current = next_node
    for entry_key, entry_value in current.value:
        start_idx = entry_key.start_mark.index
        end_idx = entry_value.end_mark.index
        out[str(entry_key.value)] = (
            len(content[:start_idx].encode("utf-8")),
            len(content[:end_idx].encode("utf-8")),
        )
    return out


def dump_yaml_str(data: Any, **kwargs: Any) -> str:
    """Serialize data to a YAML string.

    Accepts ``yaml.dump`` kwargs (e.g. ``sort_keys=False``,
    ``default_flow_style=False``). Distinct from the removed ``dump_yaml`` which wrote
    to disk.
    """
    kwargs.pop("Dumper", None)
    return yaml.dump(data, Dumper=_Dumper, **kwargs)
