"""Artifact trust contract for the standalone pretty-output hook."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

# Keep in sync with the stdlib-only path constants in _hook_settings.py.
_HOOK_CONFIG_PATH_COMPONENTS = (".autoskillit", "temp", ".hook_config.json")
_RESPONSE_SPILL_METADATA_KEY = "_autoskillit_response_spill"
_RESPONSE_SPILL_SCHEMA_VERSION = 1
_RESPONSE_SPILL_METADATA_KEYS = frozenset(
    {
        "schema_version",
        "artifact_path",
        "sha256",
        "original_utf8_bytes",
        "projected_utf8_bytes",
        "omitted_chars",
        "omitted_items",
        "reason",
    }
)
_RESPONSE_SPILL_REASONS = frozenset({"oversized_values", "minimal_projection", "plain_text"})
_RESPONSE_SPILL_SCHEMA_DIGEST = hashlib.sha256(
    json.dumps(
        {
            "metadata_key": _RESPONSE_SPILL_METADATA_KEY,
            "metadata_keys": sorted(_RESPONSE_SPILL_METADATA_KEYS),
            "reasons": sorted(_RESPONSE_SPILL_REASONS),
            "schema_version": _RESPONSE_SPILL_SCHEMA_VERSION,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
).hexdigest()
_RESPONSE_SPILL_NUMERIC_KEYS = (
    "original_utf8_bytes",
    "projected_utf8_bytes",
    "omitted_chars",
    "omitted_items",
)
_RESPONSE_BACKSTOP_EXEMPTION_REGISTRY = {
    "load_recipe": {
        "max_chars": 179_000,
        "max_utf8_bytes": 179_000,
        "measurement_id": "bundled-recipes-all-modes-2026-07-15/load-recipe",
    },
    "open_kitchen": {
        "max_chars": 180_000,
        "max_utf8_bytes": 180_000,
        "measurement_id": "bundled-recipes-all-modes-2026-07-15/open-kitchen",
    },
}
_RESPONSE_BACKSTOP_EXEMPTION_REGISTRY_DIGEST = hashlib.sha256(
    json.dumps(
        _RESPONSE_BACKSTOP_EXEMPTION_REGISTRY,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
).hexdigest()


def _response_temp_root() -> Path:
    default = Path.cwd().joinpath(".autoskillit", "temp").resolve()
    try:
        config = json.loads(Path.cwd().joinpath(*_HOOK_CONFIG_PATH_COMPONENTS).read_text())
        configured = config.get("response_temp_root") if isinstance(config, dict) else None
        if isinstance(configured, str) and Path(configured).is_absolute():
            return Path(configured).resolve(strict=True)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError):
        pass
    return default


def _validate_response_spill_metadata(value: object) -> dict[str, Any] | None:
    """Return trusted spill metadata only after verifying its published artifact."""
    if not isinstance(value, dict) or set(value) != _RESPONSE_SPILL_METADATA_KEYS:
        return None
    if type(value["schema_version"]) is not int:
        return None
    if value["schema_version"] != _RESPONSE_SPILL_SCHEMA_VERSION:
        return None
    if any(type(value[key]) is not int or value[key] < 0 for key in _RESPONSE_SPILL_NUMERIC_KEYS):
        return None
    digest = value["sha256"]
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        return None
    reason = value["reason"]
    raw_path = value["artifact_path"]
    if not isinstance(reason, str) or reason not in _RESPONSE_SPILL_REASONS:
        return None
    if not isinstance(raw_path, str):
        return None
    artifact = Path(raw_path)
    try:
        project_temp = _response_temp_root()
        if not artifact.is_absolute() or artifact.is_symlink() or not artifact.is_file():
            return None
        resolved = artifact.resolve(strict=True)
        if not resolved.is_relative_to(project_temp):
            return None
        if resolved.stat().st_size != value["original_utf8_bytes"]:
            return None
        hasher = hashlib.sha256()
        with resolved.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                hasher.update(chunk)
    except (OSError, RuntimeError, ValueError):
        return None
    return value if hasher.hexdigest() == digest else None
