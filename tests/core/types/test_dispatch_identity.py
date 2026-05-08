"""T1: DispatchIdentity value object and prompt sentinel assertion."""

from __future__ import annotations

import pytest

from autoskillit.core.types._type_dispatch_identity import (
    DispatchIdentity,
    PromptContractError,
    assert_prompt_sentinel,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


class TestDispatchIdentity:
    def test_fresh_identity_has_consistent_markers(self) -> None:
        identity = DispatchIdentity.fresh()
        assert identity.dispatch_id in identity.sentinel_open
        assert identity.dispatch_id in identity.sentinel_close
        assert identity.dispatch_id[:8] in identity.completion_marker

    def test_from_dispatch_id_preserves_id(self) -> None:
        original_id = "aaaabbbb-cccc-dddd-eeee-ffffffffffff"
        identity = DispatchIdentity.from_dispatch_id(original_id)
        assert identity.dispatch_id == original_id
        assert original_id in identity.sentinel_open
        assert original_id in identity.sentinel_close

    def test_identity_is_frozen(self) -> None:
        identity = DispatchIdentity.fresh()
        with pytest.raises(AttributeError):
            identity.dispatch_id = "tampered"

    def test_sentinel_open_format(self) -> None:
        identity = DispatchIdentity.from_dispatch_id("deadbeef-dead-beef-dead-beefcafebabe")
        assert identity.sentinel_open == "---l3-result::deadbeef-dead-beef-dead-beefcafebabe---"

    def test_sentinel_close_format(self) -> None:
        identity = DispatchIdentity.from_dispatch_id("deadbeef-dead-beef-dead-beefcafebabe")
        assert (
            identity.sentinel_close == "---end-l3-result::deadbeef-dead-beef-dead-beefcafebabe---"
        )

    def test_completion_marker_format(self) -> None:
        identity = DispatchIdentity.from_dispatch_id("deadbeef-dead-beef-dead-beefcafebabe")
        assert identity.completion_marker == "%%L3_DONE::deadbeef%%"

    def test_sentinel_contract_contains_all_markers(self) -> None:
        identity = DispatchIdentity.fresh()
        assert identity.sentinel_open in identity.sentinel_contract
        assert identity.sentinel_close in identity.sentinel_contract
        assert identity.completion_marker in identity.sentinel_contract


class TestAssertPromptSentinel:
    def test_rejects_missing_sentinel_open(self) -> None:
        identity = DispatchIdentity.fresh()
        bad_prompt = f"just some text {identity.sentinel_close} {identity.completion_marker}"
        with pytest.raises(PromptContractError) as exc_info:
            assert_prompt_sentinel(bad_prompt, identity)
        assert "sentinel open" in str(exc_info.value)

    def test_rejects_missing_sentinel_close(self) -> None:
        identity = DispatchIdentity.fresh()
        bad_prompt = f"just some text {identity.sentinel_open} {identity.completion_marker}"
        with pytest.raises(PromptContractError) as exc_info:
            assert_prompt_sentinel(bad_prompt, identity)
        assert "sentinel close" in str(exc_info.value)

    def test_rejects_missing_completion_marker(self) -> None:
        identity = DispatchIdentity.fresh()
        bad_prompt = f"{identity.sentinel_open} body {identity.sentinel_close}"
        with pytest.raises(PromptContractError) as exc_info:
            assert_prompt_sentinel(bad_prompt, identity)
        assert "completion marker" in str(exc_info.value)

    def test_accepts_valid_prompt(self) -> None:
        identity = DispatchIdentity.fresh()
        good_prompt = (
            f"prefix {identity.sentinel_open} body "
            f"{identity.sentinel_close} suffix {identity.completion_marker}"
        )
        assert_prompt_sentinel(good_prompt, identity)  # should not raise
