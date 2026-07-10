"""IL-0 compilation result and key types.

Step 2.2 / 2.5 of #4185. Provides the typed transport values the sole
recipe compiler uses to publish its result and to key its cache:

- :class:`RecipeCompilationKey` — deeply immutable cache key built from
  every authority the compiler observed (project identity, content
  identities, registry hashes, configured/effective backends, etc.).
- :class:`RecipeCompilationResult` — successful compilation result. The
  payload itself is opaque (``object``); consumers must consume the
  declared/effective views via the compiler's typed accessors rather
  than reach into the raw dict.
- :class:`RecipeCompilationFailure` — failure result, carrying the
  reason, the partial key (so the cache does not store it), and the
  raw diagnostics payload.
- :class:`KitchenInstanceId` — typed process-local identity reserved
  before compilation so the lifecycle work in Step 3 consumes the
  final identity rather than changing the compiler key.

The result type is a ``TaggedUnion`` so callers cannot accidentally
mix success and failure payloads: the compiler either returns a
result with a populated payload or a failure with reason+diagnostics.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

from ._type_enums import SkillSource

__all__ = [
    "RecipeCompilationKey",
    "RecipeCompilationResult",
    "RecipeCompilationFailure",
    "KitchenInstanceId",
    "compute_compilation_key_fingerprint",
    "EMPTY_COMPILATION_KEY_FINGERPRINT",
]


EMPTY_COMPILATION_KEY_FINGERPRINT: Final[str] = (
    "0000000000000000000000000000000000000000000000000000000000000000"
)


@dataclass(frozen=True, slots=True)
class KitchenInstanceId:
    """Typed process-local identity for one open kitchen.

    The instance id is reserved **before** compilation so the compiler
    can include it in its cache key. Lifecycle publication (Step 3)
    consumes this same value rather than minting a new one.
    """

    value: str
    process_id: int

    def __post_init__(self) -> None:
        if not self.value:
            raise ValueError("KitchenInstanceId.value must be non-empty")
        if self.process_id <= 0:
            raise ValueError("KitchenInstanceId.process_id must be positive")

    def __hash__(self) -> int:
        return hash((self.value, self.process_id))


@dataclass(frozen=True, slots=True)
class RecipeCompilationKey:
    """Deeply immutable cache key for one compilation.

    Every field on this key is the value the compiler observed at the
    moment it ran. If any field mutates (raw content identity, registry
    hash, suppressed-rule set, etc.) the resulting fingerprint changes
    and the cache lookup misses. The key does not carry the
    compilation's *result* — that lives on
    :class:`RecipeCompilationResult`.
    """

    project_dir: Path
    recipe_name: str
    raw_content_identity: str
    composite_content_identity: str
    sub_recipe_content_identities: tuple[str, ...]
    selected_skill_identities: tuple[str, ...]
    resolved_defaults: Mapping[str, str]
    caller_overrides: Mapping[str, str]
    session_overrides: Mapping[str, str]
    suppressed_rules: frozenset[str]
    defer_unresolved: bool
    backend_name: str | None
    effective_backend_map: Mapping[str, str]
    bundled_contract_version: str
    project_contract_identity: str | None
    tool_registry_hash: str
    rule_view_registry_hash: str
    kitchen_instance_id: KitchenInstanceId | None
    feature_inputs: Mapping[str, bool]

    def __post_init__(self) -> None:
        if not self.recipe_name:
            raise ValueError("RecipeCompilationKey.recipe_name must be non-empty")
        if not self.raw_content_identity:
            raise ValueError("RecipeCompilationKey.raw_content_identity must be non-empty")
        if not self.tool_registry_hash:
            raise ValueError("RecipeCompilationKey.tool_registry_hash must be non-empty")
        if not self.rule_view_registry_hash:
            raise ValueError("RecipeCompilationKey.rule_view_registry_hash must be non-empty")

    def __hash__(self) -> int:
        return hash(
            (
                self.recipe_name,
                self.raw_content_identity,
                self.composite_content_identity,
                self.tool_registry_hash,
                self.rule_view_registry_hash,
                self.backend_name,
                self.bundled_contract_version,
                self.project_contract_identity,
                self.defer_unresolved,
            )
        )

    @property
    def fingerprint(self) -> str:
        """SHA-256 fingerprint over the canonical JSON form of this key."""
        return compute_compilation_key_fingerprint(self)


def compute_compilation_key_fingerprint(key: RecipeCompilationKey) -> str:
    """Canonical SHA-256 fingerprint of a :class:`RecipeCompilationKey`.

    Two keys with identical field values produce identical fingerprints,
    regardless of the in-memory representation. Cache stores index by
    fingerprint; equality on the dataclass itself is exhaustive and
    available for callers that need to compare structurally.
    """
    canonical = {
        "project_dir": str(key.project_dir),
        "recipe_name": key.recipe_name,
        "raw_content_identity": key.raw_content_identity,
        "composite_content_identity": key.composite_content_identity,
        "sub_recipe_content_identities": list(key.sub_recipe_content_identities),
        "selected_skill_identities": list(key.selected_skill_identities),
        "resolved_defaults": sorted(dict(key.resolved_defaults).items()),
        "caller_overrides": sorted(dict(key.caller_overrides).items()),
        "session_overrides": sorted(dict(key.session_overrides).items()),
        "suppressed_rules": sorted(key.suppressed_rules),
        "defer_unresolved": key.defer_unresolved,
        "backend_name": key.backend_name,
        "effective_backend_map": sorted(dict(key.effective_backend_map).items()),
        "bundled_contract_version": key.bundled_contract_version,
        "project_contract_identity": key.project_contract_identity,
        "tool_registry_hash": key.tool_registry_hash,
        "rule_view_registry_hash": key.rule_view_registry_hash,
        "kitchen_instance_id": (
            (key.kitchen_instance_id.value, key.kitchen_instance_id.process_id)
            if key.kitchen_instance_id is not None
            else None
        ),
        "feature_inputs": sorted(dict(key.feature_inputs).items(), key=lambda kv: kv[0]),
    }
    return hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class RecipeCompilationResult:
    """Successful compilation result, opaque payload + sealed identities.

    The compiler owns the raw ``payload`` and produces typed accessor
    methods (``declared_view``, ``effective_view``, etc.) — callers
    must consume the typed views rather than reach into ``payload``
    directly. Identity fields (recipe name/kind/version, content /
    composite / manifest / invocation fingerprints) are sealed at
    construction time so downstream consumers cannot drift from the
    compilation authority.
    """

    key: RecipeCompilationKey
    recipe_name: str
    recipe_kind: str
    recipe_version: str
    content_fingerprint: str
    composite_fingerprint: str
    manifest_fingerprint: str
    invocation_fingerprint: str
    selected_skill_source: SkillSource
    payload: Any

    def __post_init__(self) -> None:
        if not self.recipe_name:
            raise ValueError("RecipeCompilationResult.recipe_name must be non-empty")
        if not self.recipe_kind:
            raise ValueError("RecipeCompilationResult.recipe_kind must be non-empty")
        if not self.recipe_version:
            raise ValueError("RecipeCompilationResult.recipe_version must be non-empty")
        for fname in (
            "content_fingerprint",
            "composite_fingerprint",
            "manifest_fingerprint",
            "invocation_fingerprint",
        ):
            if not getattr(self, fname):
                raise ValueError(f"RecipeCompilationResult.{fname} must be non-empty")

    def __hash__(self) -> int:
        return hash(
            (
                self.recipe_name,
                self.recipe_kind,
                self.recipe_version,
                self.content_fingerprint,
                self.composite_fingerprint,
                self.manifest_fingerprint,
                self.invocation_fingerprint,
            )
        )


@dataclass(frozen=True, slots=True)
class RecipeCompilationFailure:
    """Failure result emitted by the compiler.

    The compiler never silently discards a failure — every unsuccessful
    compilation produces a :class:`RecipeCompilationFailure` with a
    stable reason and a diagnostics payload. Cache stores reject
    failures (the ``key`` is captured for traceability only).
    """

    key: RecipeCompilationKey
    reason: str
    diagnostics: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not self.reason:
            raise ValueError("RecipeCompilationFailure.reason must be non-empty")
        # Wrap diagnostics into a MappingProxyType so downstream consumers
        # cannot mutate the captured snapshot.
        object.__setattr__(self, "diagnostics", MappingProxyType(dict(self.diagnostics)))

    @property
    def is_publishable(self) -> bool:
        """Failures are never publishable — only success results are."""
        return False
