"""Tests for the Codex CLI limit verification-record registry.

The registry (`CODEX_LIMIT_VERIFICATION_REGISTRY`) is the durable, machine-readable
record of what was checked against a pinned Codex CLI revision and what was found.
`CODEX_LIMITS_LAST_VERIFIED_VERSION` is derived from it — it cannot be bumped
without updating the evidence sitting alongside it in the same registry entry.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from autoskillit.execution.backends import ensure_codex_mcp_registered
from autoskillit.execution.backends._codex_config import (
    CODEX_AUTO_COMPACT_LIMIT,
    CODEX_LIMIT_VERIFICATION_REGISTRY,
    CODEX_LIMIT_VERIFICATION_REGISTRY_DIGEST,
    CODEX_LIMITS_LAST_VERIFIED_VERSION,
    CodexLimitVerificationDef,
    _read_codex_config,
    validate_codex_limit_verification,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def test_pin_is_derived_from_the_verification_registry() -> None:
    assert CODEX_LIMIT_VERIFICATION_REGISTRY
    assert CODEX_LIMITS_LAST_VERIFIED_VERSION == min(
        entry.checked_at_cli_version for entry in CODEX_LIMIT_VERIFICATION_REGISTRY.values()
    )
    for entry in CODEX_LIMIT_VERIFICATION_REGISTRY.values():
        assert len(entry.checked_at_cli_version) == 3
        assert all(isinstance(v, int) for v in entry.checked_at_cli_version)


def test_every_top_level_codex_config_key_has_a_verification_record(tmp_path) -> None:
    p = tmp_path / "config.toml"
    ensure_codex_mcp_registered(config_path=p)
    config = _read_codex_config(p).data
    written = set(config) - {"mcp_servers"}
    governed = {
        entry.codex_config_key
        for entry in CODEX_LIMIT_VERIFICATION_REGISTRY.values()
        if entry.codex_config_key is not None
    }
    assert written >= governed, f"registry names keys the writer never wrote: {governed - written}"
    assert written == governed, (
        "top-level Codex config keys with no verification record: "
        f"{written - governed} — add a CODEX_LIMIT_VERIFICATION_REGISTRY entry"
    )
    for entry in CODEX_LIMIT_VERIFICATION_REGISTRY.values():
        if entry.codex_config_key is not None:
            assert config[entry.codex_config_key] == entry.configured_value


def test_declared_status_matches_the_recorded_numbers() -> None:
    for key, entry in CODEX_LIMIT_VERIFICATION_REGISTRY.items():
        validate_codex_limit_verification(entry, key=key)
        assert entry.governed_symbol == key
        assert entry.upstream_revision
        assert entry.upstream_sources
        assert len(entry.finding) >= 80
        if entry.codex_config_key is not None:
            assert entry.configured_value is not None


def test_status_invariant_has_teeth() -> None:
    inconsistent = CodexLimitVerificationDef(
        governed_symbol="CODEX_AUTO_COMPACT_LIMIT",
        checked_at_cli_version=(0, 145, 0),
        upstream_revision="25af12f7e61572b0bc18ddb1008be543b91519b0",
        upstream_sources=("codex-rs/protocol/src/openai_models.rs",),
        status="upstream_honored",
        codex_config_key="model_auto_compact_token_limit",
        configured_value=999_999_999,
        upstream_effective_value=244_800,
        finding="x" * 100,
    )
    with pytest.raises(ValueError):
        validate_codex_limit_verification(inconsistent, key="CODEX_AUTO_COMPACT_LIMIT")


def test_auto_compact_sentinel_is_recorded_as_neutralized_at_the_clamped_threshold() -> None:
    entry = CODEX_LIMIT_VERIFICATION_REGISTRY["CODEX_AUTO_COMPACT_LIMIT"]
    assert entry.status == "upstream_neutralized"
    assert entry.configured_value == CODEX_AUTO_COMPACT_LIMIT == 999_999_999
    assert entry.upstream_effective_value == (272_000 * 9) // 10 == 244_800
    assert entry.checked_at_cli_version == (0, 145, 0)
    assert entry.upstream_revision == "25af12f7e61572b0bc18ddb1008be543b91519b0"
    assert "auto_compact_token_limit" in entry.finding
    assert "#4271" in entry.finding


def test_unverifiable_surface_is_recorded_not_omitted() -> None:
    entry = CODEX_LIMIT_VERIFICATION_REGISTRY["CODEX_RECIPE_DELIVERY_BUDGET"]
    assert entry.status == "locally_unreachable"
    assert entry.upstream_effective_value is None
    assert "SUPPORTED_CODEX_RECIPE_EVIDENCE_REGISTRY" in entry.finding
    assert "CODEX_SMOKE_TEST" in entry.finding


def test_registry_digest_changes_when_a_finding_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    import autoskillit.execution.backends._codex_config as mod

    key = "CODEX_HISTORY_RETENTION_TOKEN_LIMIT"
    original = mod.CODEX_LIMIT_VERIFICATION_REGISTRY[key]
    mutated_registry = dict(mod.CODEX_LIMIT_VERIFICATION_REGISTRY)
    mutated_registry[key] = original._replace(finding=original.finding + " mutated")
    monkeypatch.setattr(mod, "CODEX_LIMIT_VERIFICATION_REGISTRY", mutated_registry)

    mutated_digest = mod._codex_limit_verification_registry_digest()

    assert mutated_digest != CODEX_LIMIT_VERIFICATION_REGISTRY_DIGEST
    # Sanity check that the helper is doing real canonicalisation, not returning a constant.
    canonical = [(k, e._asdict()) for k, e in sorted(mutated_registry.items())]
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    assert mutated_digest == hashlib.sha256(payload.encode("ascii")).hexdigest()
