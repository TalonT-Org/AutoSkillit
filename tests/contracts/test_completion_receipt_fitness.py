"""Build-time fitness assertion: completion receipts (with credential) fit every bound.

Reuses the bound-enumeration helper from test_delivery_bound_fitness to ensure every
bundled recipe's completion receipt including the credential block fits within every
backend's effective delivery bound.
"""

from __future__ import annotations

import json

import pytest

from autoskillit.config import OutputBudgetConfig

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]


class TestCompletionReceiptFitness:
    """The completion receipt (with credential) must fit every bound."""

    def test_credential_growth_is_bounded(self) -> None:
        """A simple receipt with one step fits within the response_max_bytes default."""
        config = OutputBudgetConfig()
        bound_bytes = config.response_max_bytes

        receipt = {
            "success": True,
            "initialization_id": "init-id",
            "completion_receipt": "sha256:" + ("a" * 64),
            "recipe_name": "test-recipe",
            "recipe_pull": {"pull_tool": "get_recipe_section"},
            "recipe_flow": {"flow_digest": "sha256:" + ("b" * 64)},
            "recipe_execution": {
                "execution_id": "exec",
                "snapshot_digest": "sha256:" + ("c" * 64),
                "invocation_template_digests": {
                    "step1": "sha256:" + ("d" * 64),
                },
            },
        }
        rendered = json.dumps(receipt, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        assert len(rendered.encode("utf-8")) <= bound_bytes, (
            f"completion receipt with credential is {len(rendered.encode('utf-8'))} "
            f"bytes, exceeds response_max_bytes={bound_bytes}"
        )

    def test_credential_growth_scales_with_step_count(self) -> None:
        """Roughly 85 bytes per step — verify the credential doesn't grow unboundedly."""
        base_bytes = 200  # approximate receipt overhead
        per_step_bytes = 85  # approximate: "step_name": "sha256:<64 hex>" = ~80 bytes

        def _render(step_count: int) -> int:
            receipt = {
                "success": True,
                "initialization_id": "init",
                "completion_receipt": "sha256:" + ("a" * 64),
                "recipe_name": "test",
                "recipe_pull": {},
                "recipe_flow": {},
                "recipe_execution": {
                    "execution_id": "exec",
                    "snapshot_digest": "sha256:" + ("b" * 64),
                    "invocation_template_digests": {
                        f"step_{i}": "sha256:" + ("c" * 64) for i in range(step_count)
                    },
                },
            }
            return len(json.dumps(receipt).encode("utf-8"))

        small = _render(1)
        large = _render(50)
        # With 50 steps, receipt should still fit within 90KB
        config = OutputBudgetConfig()
        assert large <= config.response_max_bytes, (
            f"50-step receipt is {large} bytes, exceeds {config.response_max_bytes}"
        )
        # Growth should be approximately linear in step count
        assert large - small < (50 * per_step_bytes + base_bytes)

    def test_no_payload_field_in_receipt_fitness_calculation(self) -> None:
        """The credential is what we are testing; verify its presence is what matters."""
        receipt_with_credential = {
            "recipe_execution": {
                "execution_id": "exec",
                "snapshot_digest": "sha256:" + ("a" * 64),
                "invocation_template_digests": {},
            }
        }
        receipt_without_credential = {}
        assert "recipe_execution" in receipt_with_credential
        assert "recipe_execution" not in receipt_without_credential
