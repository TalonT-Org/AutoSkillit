"""Version-bound conformance evidence for specialized Codex explorer children."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from autoskillit.core import (
    AgentDef,
    CodexAgentProjectionDef,
    agent_definition_digest,
    atomic_write,
)
from autoskillit.execution.backends._probe_cache import PROBE_POLICY_IDENTITY

EXPLORER_ATTESTATION_SCHEMA_VERSION = 6
EXPLORER_PROBE_CONTRACT = "generated-codex-child-v7"
EXPLORER_ATTESTATION_FILENAME = "codex-explorer-conformance-v6.json"
EXPLORER_ATTESTATION_SHA256_FILENAME = f"{EXPLORER_ATTESTATION_FILENAME}.sha256"
EXPLORER_ATTESTATION_MAX_AGE_SECONDS = 24 * 60 * 60
EXPLORER_ATTESTATION_FUTURE_SKEW_SECONDS = 5 * 60
EXPLORER_PARENT_MODEL = "gpt-5.6-sol"
EXPLORER_MODEL = "gpt-5.6-luna"
EXPLORER_REASONING_EFFORT = "max"
EXPLORER_SANDBOX_MODE = "read-only"
EXPLORER_PROBE_ROLE = "semantic-code-navigator"
EXPLORER_PROBE_TASK_NAME = "capability_probe"
_LUNA_BUNDLED_TOOL_MODE = "code_mode_only"
_LUNA_EFFECTIVE_TOOL_MODE = "direct"
_LUNA_BUNDLED_APPLY_PATCH_TOOL_TYPE = "freeform"
_LUNA_EFFECTIVE_APPLY_PATCH_TOOL_TYPE = None
EXPLORER_DISABLED_FEATURES = (
    "apps",
    "apps_mcp_path_override",
    "browser_use",
    "browser_use_external",
    "browser_use_full_cdp_access",
    "code_mode",
    "code_mode_buffered_exec",
    "code_mode_host",
    "code_mode_only",
    "computer_use",
    "enable_mcp_apps",
    "goals",
    "image_generation",
    "in_app_browser",
    "js_repl",
    "js_repl_tools_only",
    "multi_agent",
    "multi_agent_v2",
    "plugin_sharing",
    "plugins",
    "remote_plugin",
    "request_permissions_tool",
    "shell_tool",
    "standalone_web_search",
    "tool_search_always_defer_mcp_tools",
    "tool_suggest",
    "unified_exec",
    "unified_exec_zsh_fork",
    "web_search_cached",
    "web_search_request",
)
EXPLORER_PARENT_DISABLED_FEATURES = tuple(
    feature
    for feature in EXPLORER_DISABLED_FEATURES
    if feature not in {"multi_agent", "multi_agent_v2"}
)
EXPLORER_MAX_SESSION_THREADS = 2
EXPLORER_MCP_TOOLS = (
    "bounded_literal_search",
    "parse_python_ast",
    "optional_capability_status",
    "deny_operations",
)
_EXPLORER_TOOL_SURFACE = {
    "child_agents_enabled": False,
    "child_disabled_features": EXPLORER_DISABLED_FEATURES,
    "mcp_servers": {
        "explorer_probe": {
            "default_tools_approval_mode": "approve",
            "enabled_tools": EXPLORER_MCP_TOOLS,
        }
    },
    "model_catalog_projection": {
        "selector": {"slug": EXPLORER_MODEL},
        "bundled_fields": {
            "apply_patch_tool_type": _LUNA_BUNDLED_APPLY_PATCH_TOOL_TYPE,
            "tool_mode": _LUNA_BUNDLED_TOOL_MODE,
        },
        "effective_fields": {
            "apply_patch_tool_type": _LUNA_EFFECTIVE_APPLY_PATCH_TOOL_TYPE,
            "tool_mode": _LUNA_EFFECTIVE_TOOL_MODE,
        },
    },
    "parent_disabled_features": EXPLORER_PARENT_DISABLED_FEATURES,
    "request_user_input_enabled": False,
    "repository_direct_mount": False,
    "session_thread_cap": EXPLORER_MAX_SESSION_THREADS,
    "web_search": "disabled",
}
EXPLORER_TOOL_SURFACE_DIGEST = (
    "sha256:"
    + hashlib.sha256(
        json.dumps(
            _EXPLORER_TOOL_SURFACE,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    ).hexdigest()
)


@dataclass(frozen=True, slots=True)
class ExplorerConformanceAttestation:
    """Authoritative observed identity and sandbox evidence for one live child."""

    schema_version: int
    cli_version: str
    model_catalog_digest: str
    probe_policy_identity: str
    probe_contract: str
    cache_miss: bool
    role: str
    agent_path: str
    parent_thread_id: str
    child_thread_id: str
    parent_model: str
    model: str
    reasoning_effort: str
    parent_sandbox_mode: str
    sandbox_mode: str
    approval_policy: str
    network_policy: str
    native_target_execution_isolation: str
    native_credential_isolation: str
    native_lsp_status: str
    native_tree_sitter_status: str
    tool_surface_digest: str
    definition_digest: str
    observed_at: str


@dataclass(frozen=True, slots=True)
class CodexLunaCatalogProjection:
    """Immutable identities and bytes for the authoritative Luna catalog projection."""

    canonical_projected_bytes: bytes
    bundled_sha256: str
    projected_sha256: str


def explorer_probe_agent_definition() -> AgentDef:
    """Return the generated read-only Luna definition used by the live probe."""
    return AgentDef(
        name=EXPLORER_PROBE_ROLE,
        description="Generated Luna/max read-only capability probe",
        tools=("Read", "Grep", "Glob"),
        model="sonnet",
        max_turns=30,
        body=(
            "You are a generated capability-probe child. Execute only the bounded commands "
            "listed in the parent message. Do not inspect repository policy or credential "
            "files except for the explicit denial checks. Never synthesize or modify source."
        ),
        codex=CodexAgentProjectionDef(
            model=EXPLORER_MODEL,
            reasoning_effort=EXPLORER_REASONING_EFFORT,
            sandbox_mode=EXPLORER_SANDBOX_MODE,
            disabled_features=EXPLORER_DISABLED_FEATURES,
            agents_enabled=False,
        ),
    )


def explorer_probe_definition_digest() -> str:
    """Return the stable digest for the generated live-probe definition."""
    return agent_definition_digest(explorer_probe_agent_definition())


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


def _attestation_sidecar_path(path: Path) -> Path:
    return path.with_name(f"{path.name}.sha256")


def _parse_explorer_attestation_payload(payload: bytes) -> ExplorerConformanceAttestation:
    try:
        raw = json.loads(
            payload,
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_nonfinite_json_constant,
        )
        if not isinstance(raw, dict) or set(raw) != set(ExplorerConformanceAttestation.__slots__):
            raise ValueError("explorer attestation fields do not match the schema")
        return ExplorerConformanceAttestation(**raw)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise ValueError("explorer attestation is unreadable") from exc


def _validate_observed_at(value: str) -> None:
    try:
        observed_at = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("explorer attestation timestamp is invalid") from exc
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("explorer attestation timestamp must be timezone-aware")
    now = datetime.now(UTC)
    observed_utc = observed_at.astimezone(UTC)
    if (observed_utc - now).total_seconds() > EXPLORER_ATTESTATION_FUTURE_SKEW_SECONDS:
        raise ValueError("explorer attestation timestamp is too far in the future")
    if (now - observed_utc).total_seconds() > EXPLORER_ATTESTATION_MAX_AGE_SECONDS:
        raise ValueError("explorer attestation is stale")


def project_codex_luna_catalog(raw: bytes) -> CodexLunaCatalogProjection:
    """Validate and canonically project the complete bundled catalog for Luna."""
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
    matching = [model for model in models if model["slug"] == EXPLORER_MODEL]
    if len(matching) != 1:
        raise ValueError(f"Codex bundled model catalog must contain exactly one {EXPLORER_MODEL}")
    efforts = matching[0].get("supported_reasoning_levels")
    if not isinstance(efforts, list) or EXPLORER_REASONING_EFFORT not in {
        entry.get("effort") for entry in efforts if isinstance(entry, dict)
    }:
        raise ValueError(f"{EXPLORER_MODEL} does not advertise max reasoning")
    luna = matching[0]
    if luna.get("tool_mode") != _LUNA_BUNDLED_TOOL_MODE:
        raise ValueError(f"{EXPLORER_MODEL} bundled tool_mode must be {_LUNA_BUNDLED_TOOL_MODE!r}")
    if luna.get("apply_patch_tool_type") != _LUNA_BUNDLED_APPLY_PATCH_TOOL_TYPE:
        raise ValueError(
            f"{EXPLORER_MODEL} bundled apply_patch_tool_type must be "
            f"{_LUNA_BUNDLED_APPLY_PATCH_TOOL_TYPE!r}"
        )

    canonical_bundled = _canonical_json_bytes(parsed)
    luna["tool_mode"] = _LUNA_EFFECTIVE_TOOL_MODE
    luna["apply_patch_tool_type"] = _LUNA_EFFECTIVE_APPLY_PATCH_TOOL_TYPE
    canonical_projected = _canonical_json_bytes(parsed)
    return CodexLunaCatalogProjection(
        canonical_projected_bytes=canonical_projected,
        bundled_sha256=_sha256_identity(canonical_bundled),
        projected_sha256=_sha256_identity(canonical_projected),
    )


def validate_codex_luna_catalog(raw: bytes) -> str:
    """Validate Luna/max in bundled CLI metadata and return its canonical digest."""
    return project_codex_luna_catalog(raw).bundled_sha256


def validate_explorer_attestation(
    attestation: ExplorerConformanceAttestation,
    *,
    expected_cli_version: str,
    expected_model_catalog_digest: str,
    expected_probe_policy_identity: str,
    expected_definition_digest: str,
    expected_role: str,
    expected_agent_path: str,
    expected_parent_thread_id: str,
    expected_child_thread_id: str,
    expected_native_target_execution_isolation: str,
    expected_native_credential_isolation: str,
    expected_native_lsp_status: str,
    expected_native_tree_sitter_status: str,
) -> None:
    """Reject stale, cached, ambiguous, or policy-incompatible evidence."""
    expected: dict[str, Any] = {
        "schema_version": EXPLORER_ATTESTATION_SCHEMA_VERSION,
        "cli_version": expected_cli_version,
        "model_catalog_digest": expected_model_catalog_digest,
        "probe_policy_identity": expected_probe_policy_identity,
        "probe_contract": EXPLORER_PROBE_CONTRACT,
        "cache_miss": True,
        "role": expected_role,
        "agent_path": expected_agent_path,
        "parent_thread_id": expected_parent_thread_id,
        "child_thread_id": expected_child_thread_id,
        "parent_model": EXPLORER_PARENT_MODEL,
        "model": EXPLORER_MODEL,
        "reasoning_effort": EXPLORER_REASONING_EFFORT,
        "parent_sandbox_mode": EXPLORER_SANDBOX_MODE,
        "sandbox_mode": EXPLORER_SANDBOX_MODE,
        "approval_policy": "never",
        "network_policy": "restricted",
        "native_target_execution_isolation": expected_native_target_execution_isolation,
        "native_credential_isolation": expected_native_credential_isolation,
        "native_lsp_status": expected_native_lsp_status,
        "native_tree_sitter_status": expected_native_tree_sitter_status,
        "tool_surface_digest": EXPLORER_TOOL_SURFACE_DIGEST,
        "definition_digest": expected_definition_digest,
    }
    for field_name, expected_value in expected.items():
        if getattr(attestation, field_name) != expected_value:
            raise ValueError(
                f"explorer attestation {field_name} mismatch: "
                f"{getattr(attestation, field_name)!r} != {expected_value!r}"
            )
    for field_name in ("role", "agent_path", "parent_thread_id", "child_thread_id"):
        if not getattr(attestation, field_name):
            raise ValueError(f"explorer attestation {field_name} is missing")
    for field_name in (
        "native_target_execution_isolation",
        "native_credential_isolation",
    ):
        if getattr(attestation, field_name) not in {"enforced", "failed-open"}:
            raise ValueError(f"explorer attestation {field_name} must be enforced or failed-open")
    for field_name in ("native_lsp_status", "native_tree_sitter_status"):
        if getattr(attestation, field_name) not in {"supported", "unsupported"}:
            raise ValueError(f"explorer attestation {field_name} must be supported or unsupported")
    if attestation.parent_thread_id == attestation.child_thread_id:
        raise ValueError("explorer attestation parent and child identities are equal")
    _validate_observed_at(attestation.observed_at)


def publish_explorer_attestation(
    output_root: Path,
    attestation: ExplorerConformanceAttestation,
    *,
    expected_cli_version: str,
    expected_model_catalog_digest: str,
    expected_probe_policy_identity: str,
    expected_definition_digest: str,
    expected_role: str,
    expected_agent_path: str,
    expected_parent_thread_id: str,
    expected_child_thread_id: str,
    expected_native_target_execution_isolation: str,
    expected_native_credential_isolation: str,
    expected_native_lsp_status: str,
    expected_native_tree_sitter_status: str,
) -> Path:
    """Validate then atomically publish a unique conformance attestation."""
    validate_explorer_attestation(
        attestation,
        expected_cli_version=expected_cli_version,
        expected_model_catalog_digest=expected_model_catalog_digest,
        expected_probe_policy_identity=expected_probe_policy_identity,
        expected_definition_digest=expected_definition_digest,
        expected_role=expected_role,
        expected_agent_path=expected_agent_path,
        expected_parent_thread_id=expected_parent_thread_id,
        expected_child_thread_id=expected_child_thread_id,
        expected_native_target_execution_isolation=expected_native_target_execution_isolation,
        expected_native_credential_isolation=expected_native_credential_isolation,
        expected_native_lsp_status=expected_native_lsp_status,
        expected_native_tree_sitter_status=expected_native_tree_sitter_status,
    )
    output_root.mkdir(parents=True, exist_ok=True)
    output_path = output_root / EXPLORER_ATTESTATION_FILENAME
    sidecar_path = _attestation_sidecar_path(output_path)
    payload = json.dumps(asdict(attestation), sort_keys=True, separators=(",", ":")) + "\n"
    atomic_write(output_path, payload, exclusive=True)
    try:
        atomic_write(
            sidecar_path,
            _sha256_identity(payload.encode("utf-8")) + "\n",
            exclusive=True,
        )
    except Exception:
        output_path.unlink(missing_ok=True)
        raise
    loaded = read_explorer_attestation(output_path)
    validate_explorer_attestation(
        loaded,
        expected_cli_version=expected_cli_version,
        expected_model_catalog_digest=expected_model_catalog_digest,
        expected_probe_policy_identity=expected_probe_policy_identity,
        expected_definition_digest=expected_definition_digest,
        expected_role=expected_role,
        expected_agent_path=expected_agent_path,
        expected_parent_thread_id=expected_parent_thread_id,
        expected_child_thread_id=expected_child_thread_id,
        expected_native_target_execution_isolation=expected_native_target_execution_isolation,
        expected_native_credential_isolation=expected_native_credential_isolation,
        expected_native_lsp_status=expected_native_lsp_status,
        expected_native_tree_sitter_status=expected_native_tree_sitter_status,
    )
    return output_path


def validate_explorer_release_readiness(
    attestation: ExplorerConformanceAttestation,
) -> None:
    """Require mandatory isolation while allowing either optional capability observation."""
    fixed_expected: dict[str, Any] = {
        "schema_version": EXPLORER_ATTESTATION_SCHEMA_VERSION,
        "probe_contract": EXPLORER_PROBE_CONTRACT,
        "cache_miss": True,
        "parent_model": EXPLORER_PARENT_MODEL,
        "model": EXPLORER_MODEL,
        "reasoning_effort": EXPLORER_REASONING_EFFORT,
        "parent_sandbox_mode": EXPLORER_SANDBOX_MODE,
        "sandbox_mode": EXPLORER_SANDBOX_MODE,
        "approval_policy": "never",
        "network_policy": "restricted",
        "tool_surface_digest": EXPLORER_TOOL_SURFACE_DIGEST,
    }
    for field_name, expected_value in fixed_expected.items():
        if getattr(attestation, field_name) != expected_value:
            raise ValueError(
                f"explorer release readiness requires {field_name}={expected_value!r}"
            )
    for field_name in ("cli_version", "parent_thread_id", "child_thread_id"):
        if not getattr(attestation, field_name):
            raise ValueError(f"explorer release readiness requires {field_name}")
    if attestation.probe_policy_identity != PROBE_POLICY_IDENTITY:
        raise ValueError("explorer release readiness requires the current probe policy")
    if attestation.role != EXPLORER_PROBE_ROLE:
        raise ValueError("explorer release readiness requires the generated probe role")
    if attestation.agent_path not in {
        EXPLORER_PROBE_TASK_NAME,
        f"/{EXPLORER_PROBE_TASK_NAME}",
    } and not attestation.agent_path.endswith(f"/{EXPLORER_PROBE_TASK_NAME}"):
        raise ValueError("explorer release readiness requires the generated probe path")
    for field_name in ("model_catalog_digest", "definition_digest"):
        value = getattr(attestation, field_name)
        if not isinstance(value, str) or len(value) != 71 or not value.startswith("sha256:"):
            raise ValueError(f"explorer release readiness requires canonical {field_name}")
    if attestation.definition_digest != explorer_probe_definition_digest():
        raise ValueError("explorer release readiness requires the generated probe definition")
    if attestation.parent_thread_id == attestation.child_thread_id:
        raise ValueError("explorer release readiness requires distinct parent and child")
    _validate_observed_at(attestation.observed_at)
    for field_name in (
        "native_target_execution_isolation",
        "native_credential_isolation",
    ):
        if getattr(attestation, field_name) != "enforced":
            raise ValueError(f"explorer release readiness requires {field_name} to be enforced")
    for field_name in ("native_lsp_status", "native_tree_sitter_status"):
        if getattr(attestation, field_name) not in {"supported", "unsupported"}:
            raise ValueError(
                f"explorer release readiness requires {field_name} to be supported or unsupported"
            )


def read_explorer_attestation(path: Path) -> ExplorerConformanceAttestation:
    """Read a published attestation through its exact versioned schema."""
    try:
        return _parse_explorer_attestation_payload(path.read_bytes())
    except OSError as exc:
        raise ValueError("explorer attestation is unreadable") from exc


def validate_published_explorer_release_readiness(
    path: Path,
) -> ExplorerConformanceAttestation:
    """Validate a published payload against its exact sidecar before release checks."""
    try:
        payload = path.read_bytes()
        sidecar = _attestation_sidecar_path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError("published explorer attestation is incomplete") from exc
    expected_digest = _sha256_identity(payload)
    if sidecar != f"{expected_digest}\n":
        raise ValueError("published explorer attestation sidecar digest mismatch")
    attestation = _parse_explorer_attestation_payload(payload)
    validate_explorer_release_readiness(attestation)
    return attestation


def new_observed_at() -> str:
    """Return the canonical UTC timestamp used by live probes."""
    return datetime.now(UTC).isoformat()
