"""Typed recipe-delivery budget, provenance, and decision contracts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import NamedTuple

__all__ = [
    "RECIPE_DELIVERY_ATTESTATION_AUDIENCE",
    "RECIPE_ARTIFACT_DESCRIPTOR_VERSION",
    "RECIPE_ARTIFACT_MAX_BLOB_BYTES",
    "RECIPE_ARTIFACT_MAX_DESCRIPTOR_BYTES",
    "RECIPE_ARTIFACT_SCHEMA_VERSION",
    "RECIPE_FLOW_SCHEMA_VERSION",
    "HostClientAttestation",
    "RecipeArtifactGeneration",
    "RecipeDeliveryAttestation",
    "RecipeDeliveryBudgetDef",
    "RecipeDeliveryDecision",
    "RecipeDeliveryEvidenceDef",
    "RecipeDeliveryMode",
    "RecipeDeliveryRequest",
    "RecipeFlowGeneration",
]

RECIPE_DELIVERY_ATTESTATION_AUDIENCE = "autoskillit.recipe-delivery"
RECIPE_ARTIFACT_DESCRIPTOR_VERSION = 2
RECIPE_ARTIFACT_SCHEMA_VERSION = 2
RECIPE_ARTIFACT_MAX_BLOB_BYTES = 1_000_000
RECIPE_ARTIFACT_MAX_DESCRIPTOR_BYTES = 16_384
RECIPE_FLOW_SCHEMA_VERSION = 1


class RecipeDeliveryMode(StrEnum):
    """Wire-shaping decision for one finalized recipe response."""

    ORDINARY_INLINE = "ordinary_inline"
    ATTESTED_INLINE = "attested_inline"
    ENVELOPE = "envelope"


def _qualified_sha256(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _is_sha256_identity(value: object) -> bool:
    prefix = "sha256:"
    return (
        isinstance(value, str)
        and value.startswith(prefix)
        and len(value) == len(prefix) + 64
        and all(character in "0123456789abcdef" for character in value[len(prefix) :])
    )


def _flow_generation_bytes(records: tuple[str, ...]) -> bytes:
    generated = bytearray()
    for record in records:
        encoded = record.encode("utf-8")
        generated.extend(len(encoded).to_bytes(8, "big"))
        generated.extend(encoded)
    return bytes(generated)


@dataclass(frozen=True, slots=True)
class RecipeFlowGeneration:
    """Canonical, ordered recipe-flow records with derived immutable identity."""

    schema_version: int
    records: tuple[str, ...]
    flow_sha256: str = ""
    flow_size_bytes: int = 0
    record_count: int = 0

    def __post_init__(self) -> None:
        if self.schema_version != RECIPE_FLOW_SCHEMA_VERSION:
            raise ValueError("unsupported recipe flow schema version")
        records = tuple(self.records)
        if not records:
            raise ValueError("recipe flow generation must contain records")
        for record in records:
            if not isinstance(record, str):
                raise TypeError("recipe flow records must be strings")
            try:
                parsed = json.loads(record)
            except json.JSONDecodeError as exc:
                raise ValueError("recipe flow record is not valid JSON") from exc
            canonical = json.dumps(
                parsed,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            if not isinstance(parsed, dict) or canonical != record:
                raise ValueError("recipe flow record is not canonical")
        generated = _flow_generation_bytes(records)
        expected_digest = _qualified_sha256(b"autoskillit.recipe-flow.v1\0" + generated)
        expected_size = len(generated)
        expected_count = len(records)
        for supplied, expected, label in (
            (self.flow_sha256, expected_digest, "flow digest"),
            (self.flow_size_bytes, expected_size, "flow size"),
            (self.record_count, expected_count, "flow record count"),
        ):
            if supplied not in ("", 0) and supplied != expected:
                raise ValueError(f"{label} mismatch")
        object.__setattr__(self, "records", records)
        object.__setattr__(self, "flow_sha256", expected_digest)
        object.__setattr__(self, "flow_size_bytes", expected_size)
        object.__setattr__(self, "record_count", expected_count)

    def identity(self) -> dict[str, str | int]:
        return {
            "flow_schema_version": self.schema_version,
            "flow_sha256": self.flow_sha256,
            "flow_size_bytes": self.flow_size_bytes,
            "flow_record_count": self.record_count,
        }


@dataclass(frozen=True, slots=True)
class RecipeArtifactGeneration:
    """Exact identities for one immutable canonical recipe payload."""

    producer_tool: str
    recipe_name: str
    descriptor_version: int
    schema_version: int
    payload_sha256: str
    artifact_blob_sha256: str
    artifact_blob_size_bytes: int
    body_sha256: str
    body_size_bytes: int
    flow_schema_version: int
    flow_sha256: str
    flow_size_bytes: int
    flow_record_count: int

    def __post_init__(self) -> None:
        for field_name in ("producer_tool", "recipe_name"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{field_name} must be a non-empty string")
        for field_name, expected in (
            ("descriptor_version", RECIPE_ARTIFACT_DESCRIPTOR_VERSION),
            ("schema_version", RECIPE_ARTIFACT_SCHEMA_VERSION),
            ("flow_schema_version", RECIPE_FLOW_SCHEMA_VERSION),
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value != expected:
                raise ValueError(f"unsupported {field_name}")
        for field_name in (
            "payload_sha256",
            "artifact_blob_sha256",
            "body_sha256",
            "flow_sha256",
        ):
            if not _is_sha256_identity(getattr(self, field_name)):
                raise ValueError(f"{field_name} must be an algorithm-qualified sha256 digest")
        for field_name in (
            "artifact_blob_size_bytes",
            "body_size_bytes",
            "flow_size_bytes",
            "flow_record_count",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{field_name} must be an integer")
        if not 0 < self.artifact_blob_size_bytes <= RECIPE_ARTIFACT_MAX_BLOB_BYTES:
            raise ValueError("artifact_blob_size_bytes is outside supported bounds")
        if not 0 <= self.body_size_bytes <= self.artifact_blob_size_bytes:
            raise ValueError("body_size_bytes must fit within the artifact blob")
        if self.flow_size_bytes <= 0:
            raise ValueError("flow_size_bytes must be positive")
        if self.flow_record_count <= 0:
            raise ValueError("flow_record_count must be positive")

    def has_valid_read_bounds(self) -> bool:
        """Return whether caller-provided sizes stay within server ceilings."""
        return (
            self.descriptor_version == RECIPE_ARTIFACT_DESCRIPTOR_VERSION
            and self.schema_version == RECIPE_ARTIFACT_SCHEMA_VERSION
            and self.flow_schema_version == RECIPE_FLOW_SCHEMA_VERSION
            and 0 < self.artifact_blob_size_bytes <= RECIPE_ARTIFACT_MAX_BLOB_BYTES
            and 0 <= self.body_size_bytes <= self.artifact_blob_size_bytes
            and self.flow_size_bytes > 0
            and self.flow_record_count > 0
        )

    def pull_identity(self) -> dict[str, str | int]:
        return {
            "producer_tool": self.producer_tool,
            "recipe_name": self.recipe_name,
            "descriptor_version": self.descriptor_version,
            "schema_version": self.schema_version,
            "payload_sha256": self.payload_sha256,
            "artifact_blob_sha256": self.artifact_blob_sha256,
            "artifact_blob_size_bytes": self.artifact_blob_size_bytes,
            "body_sha256": self.body_sha256,
            "body_size_bytes": self.body_size_bytes,
            "flow_schema_version": self.flow_schema_version,
            "flow_sha256": self.flow_sha256,
            "flow_size_bytes": self.flow_size_bytes,
            "flow_record_count": self.flow_record_count,
            "pull_tool": "get_recipe_section",
        }


class RecipeDeliveryBudgetDef(NamedTuple):
    """Backend-selected limits whose byte and token domains remain distinct."""

    ordinary_omitted_result_token_limit: int
    authoritative_attested_recipe_result_token_limit: int
    history_retention_token_limit: int
    measured_recipe_exemption_max_utf8_bytes: int
    headroom_tokens: int
    contract_version: int
    parser_version: int
    evidence_version: int
    contract_digest: str


class RecipeDeliveryEvidenceDef(NamedTuple):
    """Version-pinned identity of a protected host evidence channel."""

    identity: str
    host_channel: str
    evidence_schema_version: int
    parser_version: int
    cli_identity: str
    selected_limit_derivation: str
    selected_result_token_limit: int
    contract_digest: str


@dataclass(frozen=True, slots=True)
class RecipeDeliveryRequest:
    """Immutable nested request; caller fields are claims, not attestation."""

    audience: str
    delivery_call_id: str
    contract_version: int
    contract_digest: str
    caller_requested_outer_tokens: int
    code_digest: str


@dataclass(frozen=True, slots=True)
class RecipeDeliveryAttestation:
    """Host observation bound to one protected outer and nested call."""

    audience: str
    thread_id: str
    turn_id: str
    outer_call_id: str
    code_mode_cell_id: str
    delivery_call_id: str
    host_observed_requested_outer_tokens: int
    selected_result_token_limit: int
    code_digest: str
    request_digest: str
    nonce: str
    expires_at_unix: int
    contract_version: int
    contract_digest: str
    parser_version: int
    evidence_version: int
    evidence_identity: str


@dataclass(frozen=True, slots=True)
class HostClientAttestation:
    """Launcher-sourced host capabilities, read once at server startup.

    Absent/malformed env → None → conservative defaults (ENVELOPE).
    """

    attested_client_gate_tokens: int
    annotation_support: bool

    def __post_init__(self) -> None:
        if (
            isinstance(self.attested_client_gate_tokens, bool)
            or not isinstance(self.attested_client_gate_tokens, int)
            or self.attested_client_gate_tokens <= 0
        ):
            raise ValueError("attested_client_gate_tokens must be a positive integer")


@dataclass(frozen=True, slots=True)
class RecipeDeliveryDecision:
    """Resolved recipe delivery outcome with explicit provenance domains."""

    mode: RecipeDeliveryMode
    caller_requested_outer_tokens: int | None
    host_observed_requested_outer_tokens: int | None
    required_outer_tokens: int
    unnegotiated_tool_result_token_limit: int
    selected_result_token_limit: int
    contract_digest: str
    evidence_identity: str | None
    reason: str
    producer: str
    payload_sha256: str
    receipt_status: str
