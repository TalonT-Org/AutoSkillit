"""Contract: Codex MCP forwarding covers ordinary server environment reads."""

from __future__ import annotations

import pytest

from autoskillit.core import (
    AUTOSKILLIT_ATTESTED_CLIENT_GATE_TOKENS,
    AUTOSKILLIT_ATTESTED_META_SUPPORT,
    AUTOSKILLIT_PRIVATE_ENV_VARS,
    CAMPAIGN_ID_ENV_VAR,
    CODEX_MCP_ENV_FORWARD_VARS,
    CODEX_MCP_ENV_SERVER_EXCLUDED_VARS,
    DISPATCH_ID_ENV_VAR,
    FLEET_MODE_ENV_VAR,
    KITCHEN_SESSION_ID_ENV_VAR,
)
from tests._ambient_env_surface import (
    _PRODUCTION_SRC_ROOT,
    EnvRead,
    production_env_read_surface,
)

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_ADMITTED_ATTESTATION_READS: frozenset[tuple[str, str]] = frozenset(
    {
        (
            "server/recipe/_recipe_delivery_helpers.py",
            AUTOSKILLIT_ATTESTED_CLIENT_GATE_TOKENS,
        ),
        (
            "server/recipe/_recipe_delivery_helpers.py",
            AUTOSKILLIT_ATTESTED_META_SUPPORT,
        ),
    }
)


def _server_process_private_reads() -> tuple[EnvRead, ...]:
    return tuple(
        read
        for read in production_env_read_surface(_PRODUCTION_SRC_ROOT).reads
        if read.rule in {"R1", "R2"}
        and not read.file.startswith("hooks/")
        and read.var in AUTOSKILLIT_PRIVATE_ENV_VARS
    )


def _format_reads(reads: tuple[EnvRead, ...]) -> str:
    return "\n".join(f"  {read.file}:{read.line} {read.var}" for read in reads)


def test_forward_set_is_derived_from_private_set() -> None:
    assert CODEX_MCP_ENV_FORWARD_VARS == (
        AUTOSKILLIT_PRIVATE_ENV_VARS - CODEX_MCP_ENV_SERVER_EXCLUDED_VARS
    )
    assert CODEX_MCP_ENV_SERVER_EXCLUDED_VARS <= AUTOSKILLIT_PRIVATE_ENV_VARS


def test_only_attestation_exclusions_are_directly_read_by_server() -> None:
    excluded_reads = tuple(
        read
        for read in _server_process_private_reads()
        if read.var in CODEX_MCP_ENV_SERVER_EXCLUDED_VARS
    )
    excluded_pairs = {(read.file, read.var) for read in excluded_reads}

    assert excluded_pairs == _ADMITTED_ATTESTATION_READS, (
        "Only host-client attestation probes may be excluded from Codex MCP forwarding. "
        "Excluded server reads:\n"
        f"{_format_reads(excluded_reads)}"
    )


def test_every_server_process_private_read_is_forwarded() -> None:
    missing = tuple(
        read
        for read in _server_process_private_reads()
        if (read.file, read.var) not in _ADMITTED_ATTESTATION_READS
        and read.var not in CODEX_MCP_ENV_FORWARD_VARS
    )

    assert not missing, (
        "Server private environment reads missing from Codex MCP forwarding:\n"
        f"{_format_reads(missing)}"
    )


def test_dispatch_identity_channel_regression() -> None:
    dispatch_identity_vars = {
        DISPATCH_ID_ENV_VAR,
        CAMPAIGN_ID_ENV_VAR,
        KITCHEN_SESSION_ID_ENV_VAR,
        FLEET_MODE_ENV_VAR,
        "AUTOSKILLIT_SESSION_DEADLINE",
        "AUTOSKILLIT_CAMPAIGN_STATE_PATH",
        "AUTOSKILLIT_PROJECT_DIR",
    }

    assert dispatch_identity_vars <= CODEX_MCP_ENV_FORWARD_VARS
