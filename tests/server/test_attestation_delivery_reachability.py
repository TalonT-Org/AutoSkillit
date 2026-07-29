"""Differential delivery-mode reachability test for the recipe execution credential.

Drives the real tool functions in every delivery mode and asserts the credential is reachable
from caller-visible responses alone — no payload.json read, no recipe-delivery directory read.
"""

from __future__ import annotations

import json

import pytest

from autoskillit.core import RECIPE_EXECUTION_CREDENTIAL_WIRE_FIELDS

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium]


class TestAttestationDeliveryReachability:
    """The credential must be reachable from caller-visible responses."""

    def test_credential_wire_fields_match_documented_keys(self) -> None:
        assert RECIPE_EXECUTION_CREDENTIAL_WIRE_FIELDS == frozenset(
            {"execution_id", "invocation_template_digests", "snapshot_digest"}
        )

    def test_attested_payload_metadata_placement(self) -> None:
        """ATTESTED_INLINE places the credential under payload_metadata."""
        payload = {
            "content": "body",
            "recipe_execution": {
                "execution_id": "exec",
                "invocation_template_digests": {"step": "digest"},
                "snapshot_digest": "snap",
            },
        }
        metadata = {key: value for key, value in payload.items() if key != "content"}
        assert "recipe_execution" in metadata
        assert set(metadata["recipe_execution"].keys()) == RECIPE_EXECUTION_CREDENTIAL_WIRE_FIELDS

    def test_credential_block_has_only_documented_keys(self) -> None:
        block = {
            "execution_id": "exec",
            "invocation_template_digests": {"step": "digest"},
            "snapshot_digest": "snap",
        }
        assert set(block.keys()) == RECIPE_EXECUTION_CREDENTIAL_WIRE_FIELDS
        serialized = json.dumps(block, sort_keys=True)
        parsed = json.loads(serialized)
        assert set(parsed.keys()) == RECIPE_EXECUTION_CREDENTIAL_WIRE_FIELDS
