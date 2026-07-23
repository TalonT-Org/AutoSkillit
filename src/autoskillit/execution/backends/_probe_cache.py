"""Versioned probe result cache with 24-hour TTL.

IL-1 module: imports only stdlib and `autoskillit.core`. Stores a per-cli-version
`ProbeResult` keyed by `cli_version` and validated against the current output
discipline policy, surviving across runs while staying under `PROBE_CACHE_TTL`.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from autoskillit.core import (
    OUTPUT_DISCIPLINE_COMBINED_SHA256,
    OUTPUT_DISCIPLINE_POLICY_VERSION,
    RECIPE_DELIVERY_SURFACE_REGISTRY_DIGEST,
    RECIPE_SECTION_PAGINATION_POLICY_DIGEST,
    RECIPE_SECTION_REGISTRY_DIGEST,
    RESPONSE_BACKSTOP_EXEMPTION_REGISTRY_DIGEST,
    get_logger,
    read_versioned_json,
    write_versioned_json,
)
from autoskillit.execution.backends._codex_config import (
    CODEX_LIMITS_LAST_VERIFIED_VERSION,
    CODEX_RECIPE_DELIVERY_BUDGET,
    CODEX_RECIPE_DELIVERY_CALLING_CONTRACT_DIGEST,
    SUPPORTED_CODEX_RECIPE_EVIDENCE_REGISTRY,
)

logger = get_logger(__name__)

PROBE_CACHE_TTL: timedelta = timedelta(hours=24)
PROBE_SUITE_CONTRACT: tuple[str, ...] = (
    "generated-codex-child-v1",
    "deep-investigate-codex-v2",
    "deep-investigate-claude-200k-v2",
    "codex-recipe-delivery-v2",
)
PROBE_SUITE_CONTRACT_DIGEST: str = hashlib.sha256(
    "\n".join(PROBE_SUITE_CONTRACT).encode("utf-8")
).hexdigest()
CODEX_RECIPE_PROBE_MODEL_IDENTITY = "gpt-5.6-sol"
_SUPPORTED_RECIPE_EVIDENCE_DIGEST = hashlib.sha256(
    json.dumps(
        {
            identity: definition._asdict()
            for identity, definition in sorted(SUPPORTED_CODEX_RECIPE_EVIDENCE_REGISTRY.items())
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
).hexdigest()
CODEX_RECIPE_PROBE_POLICY_COMPONENTS: tuple[str, ...] = (
    f"budget:{CODEX_RECIPE_DELIVERY_BUDGET.contract_digest}",
    f"parser:{CODEX_RECIPE_DELIVERY_BUDGET.parser_version}",
    f"evidence-schema:{CODEX_RECIPE_DELIVERY_BUDGET.evidence_version}",
    f"attestation-registry:{_SUPPORTED_RECIPE_EVIDENCE_DIGEST}",
    f"surface-registry:{RECIPE_DELIVERY_SURFACE_REGISTRY_DIGEST}",
    f"section-registry:{RECIPE_SECTION_REGISTRY_DIGEST}",
    f"pagination-policy:{RECIPE_SECTION_PAGINATION_POLICY_DIGEST}",
    f"prompt:{CODEX_RECIPE_DELIVERY_CALLING_CONTRACT_DIGEST}",
    f"response-exemptions:{RESPONSE_BACKSTOP_EXEMPTION_REGISTRY_DIGEST}",
    "cli-pin:" + ".".join(str(value) for value in CODEX_LIMITS_LAST_VERIFIED_VERSION),
    f"model:{CODEX_RECIPE_PROBE_MODEL_IDENTITY}",
    "fixtures:protected-v1+diagnostic-v1",
    "attestation-provider:null-protected-host-v1",
)
CODEX_RECIPE_PROBE_POLICY_DIGEST: str = hashlib.sha256(
    "\n".join(CODEX_RECIPE_PROBE_POLICY_COMPONENTS).encode("utf-8")
).hexdigest()
PROBE_POLICY_IDENTITY: str = (
    f"v{OUTPUT_DISCIPLINE_POLICY_VERSION}-{OUTPUT_DISCIPLINE_COMBINED_SHA256}-"
    f"{RESPONSE_BACKSTOP_EXEMPTION_REGISTRY_DIGEST}-{PROBE_SUITE_CONTRACT_DIGEST}-"
    f"{CODEX_RECIPE_PROBE_POLICY_DIGEST}"
)
_SCHEMA_VERSION: int = 3


@dataclass(frozen=True, slots=True)
class ProbeResult:
    cli_version: str
    policy_identity: str
    passed: bool
    failure_detail: str | None
    probe_timestamp: str


def read_probe_cache(
    cache_path: Path,
    cli_version: str,
    policy_identity: str,
) -> ProbeResult | None:
    raw = read_versioned_json(cache_path, _SCHEMA_VERSION, logger=logger)
    if raw is None:
        return None
    entries: dict[str, dict] = raw.get("entries", {})
    entry = entries.get(cli_version)
    if entry is None:
        return None
    if entry.get("policy_identity") != policy_identity:
        return None
    try:
        ts = datetime.fromisoformat(entry["probe_timestamp"])
        if (datetime.now(UTC) - ts) > PROBE_CACHE_TTL:
            return None
    except (KeyError, ValueError, TypeError):
        return None
    return ProbeResult(
        cli_version=cli_version,
        policy_identity=policy_identity,
        passed=entry.get("passed", False),
        failure_detail=entry.get("failure_detail"),
        probe_timestamp=entry["probe_timestamp"],
    )


def write_probe_cache(cache_path: Path, result: ProbeResult) -> None:
    try:
        existing = read_versioned_json(cache_path, _SCHEMA_VERSION, logger=logger)
        entries: dict[str, dict] = (existing or {}).get("entries", {})
        entries[result.cli_version] = {
            "policy_identity": result.policy_identity,
            "passed": result.passed,
            "failure_detail": result.failure_detail,
            "probe_timestamp": result.probe_timestamp,
        }
        write_versioned_json(cache_path, {"entries": entries}, _SCHEMA_VERSION)
    except OSError:
        logger.debug("probe_cache_write_failed", path=str(cache_path), exc_info=True)
