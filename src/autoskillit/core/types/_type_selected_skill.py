"""IL-0 selected-skill authority.

SelectedSkill is the canonical, immutable, slotted, project- and source-resolved
binding of a single skill name to its normalized contract. It is the IL-0
authority that recipe admission, the canonical binder, the compiler, and
execution-side freshness checks all consume.

Design contract (per #4185 Step 1.7):

- Frozen/slotted IL-0 type — zero `autoskillit.*` imports, safe to import from
  hook subprocesses and admission paths.
- Backing data is the normalized contract value (plain ``dict[str, object]``)
  never the mutable recipe-layer ``SkillContract`` dataclass. The normalized
  payload survives ``deepcopy`` and dict-literal copy semantics, and its
  fingerprint hash is independent of any single backend, project layout, or
  ordering of insertion.
- A project-local winner fully replaces bundled authority. Missing or
  malformed local contracts surface as ``UNKNOWN_SKILL`` or
  ``MALFORMED_CONTRACT`` denials — there is no implicit fallback to bundled.
- Zero-input skills must declare ``inputs: []`` explicitly; a missing
  ``inputs`` field is rejected so callers cannot accidentally accept an
  undeclared contract shape.
- Cache key is winning-source path + content hash + contract identity, never
  skill name alone.
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
    "SelectedSkill",
    "normalize_skill_contract",
    "compute_selected_skill_fingerprint",
    "build_selected_skill",
    "EMPTY_SELECTED_SKILL",
    "ZERO_INPUT_KEY",
]


# Marker distinguishing an explicitly-declared zero-input contract from an
# undeclared one. ``EMPTY_SELECTED_SKILL`` is the unique fingerprint base for
# all such entries; their identity is still per-name because the same skill
# could legitimately be declared empty by name in different projects.
ZERO_INPUT_KEY: Final[str] = "ZERO_INPUT_DECLARED"

# Sentinel fingerprint for the empty-but-valid contract used by the rare
# caller that probes for resolver availability without yet knowing a name.
EMPTY_SELECTED_SKILL_FINGERPRINT: Final[str] = (
    "0000000000000000000000000000000000000000000000000000000000000000"
)


@dataclass(frozen=True, slots=True)
class SelectedSkill:
    """Immutable IL-0 binding of a skill name to its normalized contract.

    Fields are exactly the values that downstream consumers must consume;
    no further derivation or mutation is permitted. ``contract_identity`` is
    the SHA-256 hex of the canonicalized normalized payload, allowing cache
    equality to short-circuit full payload comparison.

    The ``capabilities``, ``dependencies``, ``output_metadata``,
    ``write_behavior``, ``path_metadata``, and ``recovery_metadata`` fields
    are sealed at construction time so admission can never re-resolve them
    against a backend that differs from the one used at selection time.
    """

    name: str
    source: SkillSource
    source_path: Path
    project_dir: Path
    content_hash: str
    contract_identity: str
    inputs: tuple[Mapping[str, Any], ...]
    outputs: tuple[Mapping[str, Any], ...]
    capabilities: frozenset[str]
    dependencies: frozenset[str]
    output_metadata: Mapping[str, Any]
    write_behavior: Mapping[str, Any] | None
    path_metadata: Mapping[str, Any]
    recovery_metadata: Mapping[str, Any]
    is_zero_input: bool

    def __post_init__(self) -> None:
        is_empty_sentinel = (
            self.name == ""
            and self.content_hash == EMPTY_SELECTED_SKILL_FINGERPRINT
            and self.contract_identity == EMPTY_SELECTED_SKILL_FINGERPRINT
        )
        if not is_empty_sentinel:
            if not self.name:
                raise ValueError("SelectedSkill.name must be non-empty")
            if not self.content_hash:
                raise ValueError("SelectedSkill.content_hash must be non-empty")
            if not self.contract_identity:
                raise ValueError("SelectedSkill.contract_identity must be non-empty")
        if self.is_zero_input and self.inputs:
            raise ValueError(f"SelectedSkill {self.name!r} is zero-input but declared inputs")
        # Wrap mutable mappings into MappingProxyType so the dataclass is hashable
        # and downstream consumers cannot mutate the inner payload.
        object.__setattr__(self, "output_metadata", MappingProxyType(dict(self.output_metadata)))
        if self.write_behavior is not None:
            object.__setattr__(self, "write_behavior", MappingProxyType(dict(self.write_behavior)))
        object.__setattr__(self, "path_metadata", MappingProxyType(dict(self.path_metadata)))
        object.__setattr__(
            self, "recovery_metadata", MappingProxyType(dict(self.recovery_metadata))
        )

    def __hash__(self) -> int:
        return hash((self.name, self.source, self.contract_identity, self.content_hash))

    def __deepcopy__(self, memo: dict[int, Any] | None = None) -> SelectedSkill:
        # MappingProxyType is not picklable / not deepcopy-able directly.
        # Reconstruct with plain dicts so consumers that deep-copy the
        # SelectedSkill (e.g. serialization layers) keep working.
        return SelectedSkill(
            name=self.name,
            source=self.source,
            source_path=Path(self.source_path),
            project_dir=Path(self.project_dir),
            content_hash=self.content_hash,
            contract_identity=self.contract_identity,
            inputs=tuple(dict(m) for m in self.inputs),
            outputs=tuple(dict(m) for m in self.outputs),
            capabilities=frozenset(self.capabilities),
            dependencies=frozenset(self.dependencies),
            output_metadata=dict(self.output_metadata),
            write_behavior=dict(self.write_behavior) if self.write_behavior is not None else None,
            path_metadata=dict(self.path_metadata),
            recovery_metadata=dict(self.recovery_metadata),
            is_zero_input=self.is_zero_input,
        )


def normalize_skill_contract(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    """Normalize a raw contract payload into a canonical IL-0 value.

    The normalized form:

    * Always declares ``inputs`` and ``outputs`` as tuples of mappings.
    * Promotes an undeclared ``inputs`` field to an empty tuple only when the
      caller has explicitly marked the contract as zero-input via the
      ``ZERO_INPUT_KEY`` sentinel under ``metadata``.
    * Lower-cases capability strings and de-duplicates via frozenset material
      so two semantically equivalent contracts share one identity.
    """
    if raw is None:
        raise ValueError("normalize_skill_contract requires a non-None payload")

    inputs_raw: Any = raw.get("inputs") if "inputs" in raw else None
    outputs_raw: Any = raw.get("outputs") if "outputs" in raw else None

    if inputs_raw is None:
        metadata = raw.get("metadata", {})
        if isinstance(metadata, Mapping) and metadata.get(ZERO_INPUT_KEY) is True:
            inputs: tuple[Mapping[str, Any], ...] = ()
        else:
            raise ValueError(
                "Contract must declare inputs: [] for zero-input skills "
                "(set metadata.ZERO_INPUT_DECLARED=true or explicit empty list)"
            )
    elif isinstance(inputs_raw, list):
        inputs = tuple(_freeze_mapping(item) for item in inputs_raw)
    else:
        raise ValueError(f"Contract inputs must be a list, got {type(inputs_raw).__name__}")

    if outputs_raw is None:
        outputs: tuple[Mapping[str, Any], ...] = ()
    elif isinstance(outputs_raw, list):
        outputs = tuple(_freeze_mapping(item) for item in outputs_raw)
    else:
        raise ValueError(f"Contract outputs must be a list, got {type(outputs_raw).__name__}")

    return {
        "inputs": inputs,
        "outputs": outputs,
        "capabilities": frozenset(str(c).lower() for c in raw.get("capabilities", []) or []),
        "dependencies": frozenset(str(d) for d in raw.get("dependencies", []) or []),
        "output_metadata": _freeze_mapping(raw.get("output_metadata", {})),
        "write_behavior": _freeze_mapping(raw.get("write_behavior", {}))
        if raw.get("write_behavior") is not None
        else None,
        "path_metadata": _freeze_mapping(raw.get("path_metadata", {})),
        "recovery_metadata": _freeze_mapping(raw.get("recovery_metadata", {})),
        "is_zero_input": len(inputs) == 0,
    }


def compute_selected_skill_fingerprint(normalized: Mapping[str, Any]) -> str:
    """Return the canonical contract-identity fingerprint.

    Two contracts that survive this normalization and produce the same
    fingerprint are semantically equivalent; downstream caches can key on
    the fingerprint alone and skip deep payload equality.
    """
    canonical = json.dumps(_canonicalize(normalized), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _freeze_mapping(value: Any) -> Mapping[str, Any]:
    """Recursively freeze a mapping into a JSON-friendly, order-independent dict."""
    if not isinstance(value, Mapping):
        raise ValueError(f"Expected Mapping, got {type(value).__name__}")
    return {str(k): _canonicalize(v) for k, v in value.items()}


def _canonicalize(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _canonicalize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonicalize(v) for v in value]
    if isinstance(value, frozenset):
        return sorted(_canonicalize(v) for v in value)
    if isinstance(value, set):
        return sorted(_canonicalize(v) for v in value)
    if isinstance(value, Path):
        return str(value)
    return value


def build_selected_skill(
    *,
    name: str,
    source: SkillSource,
    source_path: Path,
    project_dir: Path,
    raw_content: str | bytes,
    raw_contract: Mapping[str, Any],
) -> SelectedSkill:
    """Construct a SelectedSkill from raw frontmatter material.

    ``raw_content`` is hashed verbatim to produce the content identity used
    in cache invalidation. ``raw_contract`` is the contract block from
    ``metadata.autoskillit.contract`` (or the bundled equivalent), which
    is normalized via :func:`normalize_skill_contract` before fingerprinting.
    """
    if isinstance(raw_content, str):
        raw_content_bytes = raw_content.encode("utf-8")
    else:
        raw_content_bytes = raw_content

    content_hash = hashlib.sha256(raw_content_bytes).hexdigest()
    normalized = normalize_skill_contract(raw_contract)
    fingerprint = compute_selected_skill_fingerprint(normalized)

    return SelectedSkill(
        name=name,
        source=source,
        source_path=Path(source_path),
        project_dir=Path(project_dir),
        content_hash=content_hash,
        contract_identity=fingerprint,
        inputs=normalized["inputs"],
        outputs=normalized["outputs"],
        capabilities=normalized["capabilities"],
        dependencies=normalized["dependencies"],
        output_metadata=normalized["output_metadata"],
        write_behavior=normalized["write_behavior"],
        path_metadata=normalized["path_metadata"],
        recovery_metadata=normalized["recovery_metadata"],
        is_zero_input=normalized["is_zero_input"],
    )


# Re-export so consumers can construct a typed empty SelectedSkill for
# resolver-availability probes without needing to know the internals.
EMPTY_SELECTED_SKILL: Final[SelectedSkill] = SelectedSkill(
    name="",
    source=SkillSource.BUNDLED,
    source_path=Path(""),
    project_dir=Path(""),
    content_hash=EMPTY_SELECTED_SKILL_FINGERPRINT,
    contract_identity=EMPTY_SELECTED_SKILL_FINGERPRINT,
    inputs=(),
    outputs=(),
    capabilities=frozenset(),
    dependencies=frozenset(),
    output_metadata={},
    write_behavior=None,
    path_metadata={},
    recovery_metadata={},
    is_zero_input=True,
)
