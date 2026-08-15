"""Validated projection of an installed Codex bundled model catalog."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

_BUNDLED_TOOL_MODE = "code_mode_only"
_DIRECT_TOOL_MODE = "direct"
_BUNDLED_APPLY_PATCH_TOOL_TYPE = "freeform"
_DISABLED_APPLY_PATCH_TOOL_TYPE = None


@dataclass(frozen=True, slots=True)
class CodexCatalogProjection:
    """Canonical bytes and identities for one validated catalog projection."""

    canonical_projected_bytes: bytes
    bundled_sha256: str
    projected_sha256: str


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonfinite_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")


def _sha256_identity(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def project_codex_catalog(
    raw: bytes,
    *,
    expected_model: str,
    expected_reasoning_effort: str,
) -> CodexCatalogProjection:
    """Validate and project one installed model to direct MCP-only tool dispatch.

    The bundled catalog must expose the known ``code_mode_only``/``freeform``
    surface for exactly one requested model and advertise the requested effort.
    The projection changes only that model's tool mode and built-in apply-patch
    type, preserving every other catalog byte semantically through canonical JSON.
    """
    if not expected_model or not expected_reasoning_effort:
        raise ValueError("Codex catalog projection requires a model and reasoning effort")
    try:
        parsed = json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_nonfinite_json_constant,
        )
        models = parsed["models"]
    except (json.JSONDecodeError, KeyError, TypeError, UnicodeDecodeError, ValueError) as exc:
        raise ValueError("Codex bundled model catalog is malformed") from exc
    if not isinstance(models, list):
        raise ValueError("Codex bundled model catalog has no model list")
    if any(
        not isinstance(model, dict) or not isinstance(model.get("slug"), str) for model in models
    ):
        raise ValueError("Codex bundled model catalog has a malformed model entry")
    matching = [model for model in models if model["slug"] == expected_model]
    if len(matching) != 1:
        raise ValueError(f"Codex bundled model catalog must contain exactly one {expected_model}")
    model = matching[0]
    efforts = model.get("supported_reasoning_levels")
    if not isinstance(efforts, list) or any(
        not isinstance(entry, dict) or not isinstance(entry.get("effort"), str)
        for entry in efforts
    ):
        raise ValueError(f"{expected_model} has malformed supported reasoning levels")
    if expected_reasoning_effort not in {entry["effort"] for entry in efforts}:
        raise ValueError(
            f"{expected_model} does not advertise {expected_reasoning_effort} reasoning"
        )
    if model.get("tool_mode") != _BUNDLED_TOOL_MODE:
        raise ValueError(f"{expected_model} bundled tool_mode must be {_BUNDLED_TOOL_MODE!r}")
    if model.get("apply_patch_tool_type") != _BUNDLED_APPLY_PATCH_TOOL_TYPE:
        raise ValueError(
            f"{expected_model} bundled apply_patch_tool_type must be "
            f"{_BUNDLED_APPLY_PATCH_TOOL_TYPE!r}"
        )

    canonical_bundled = _canonical_json_bytes(parsed)
    model["tool_mode"] = _DIRECT_TOOL_MODE
    model["apply_patch_tool_type"] = _DISABLED_APPLY_PATCH_TOOL_TYPE
    canonical_projected = _canonical_json_bytes(parsed)
    return CodexCatalogProjection(
        canonical_projected_bytes=canonical_projected,
        bundled_sha256=_sha256_identity(canonical_bundled),
        projected_sha256=_sha256_identity(canonical_projected),
    )
