import pytest

from autoskillit.execution.headless import _merge_token_usage

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


class TestMergeTokenUsageCanonical:
    """Tests for _merge_token_usage canonical field name migration."""

    def test_canonical_inputs_produce_canonical_outputs(self):
        """Both dicts use canonical names → output uses canonical names."""
        base = {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_write_tokens": 10,
            "cache_read_tokens": 20,
        }
        nudge = {
            "input_tokens": 200,
            "output_tokens": 100,
            "cache_write_tokens": 5,
            "cache_read_tokens": 30,
        }
        result = _merge_token_usage(base, nudge)
        assert result["input_tokens"] == 300
        assert result["output_tokens"] == 150
        assert result["cache_write_tokens"] == 15
        assert result["cache_read_tokens"] == 50

    def test_legacy_inputs_produce_canonical_outputs(self):
        """Both dicts use legacy names → output uses canonical names."""
        base = {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_creation_input_tokens": 10,
            "cache_read_input_tokens": 20,
        }
        nudge = {
            "input_tokens": 200,
            "output_tokens": 100,
            "cache_creation_input_tokens": 5,
            "cache_read_input_tokens": 30,
        }
        result = _merge_token_usage(base, nudge)
        assert result["cache_write_tokens"] == 15
        assert result["cache_read_tokens"] == 50
        assert "cache_creation_input_tokens" not in result
        assert "cache_read_input_tokens" not in result

    def test_mixed_inputs_canonical_base_legacy_nudge(self):
        """Base uses canonical, nudge uses legacy → output uses canonical names."""
        base = {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_write_tokens": 10,
            "cache_read_tokens": 20,
        }
        nudge = {
            "input_tokens": 200,
            "output_tokens": 100,
            "cache_creation_input_tokens": 5,
            "cache_read_input_tokens": 30,
        }
        result = _merge_token_usage(base, nudge)
        assert result["cache_write_tokens"] == 15
        assert result["cache_read_tokens"] == 50

    def test_mixed_inputs_legacy_base_canonical_nudge(self):
        """Base uses legacy, nudge uses canonical → output uses canonical names."""
        base = {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_creation_input_tokens": 10,
            "cache_read_input_tokens": 20,
        }
        nudge = {
            "input_tokens": 200,
            "output_tokens": 100,
            "cache_write_tokens": 5,
            "cache_read_tokens": 30,
        }
        result = _merge_token_usage(base, nudge)
        assert result["cache_write_tokens"] == 15
        assert result["cache_read_tokens"] == 50

    def test_none_base_returns_nudge(self):
        """None base → return nudge unchanged."""
        nudge = {"input_tokens": 200, "output_tokens": 100}
        assert _merge_token_usage(None, nudge) is nudge

    def test_none_nudge_returns_base(self):
        """None nudge → return base unchanged."""
        base = {"input_tokens": 100, "output_tokens": 50}
        assert _merge_token_usage(base, None) is base

    def test_both_none_returns_none(self):
        """Both None → return None."""
        assert _merge_token_usage(None, None) is None

    def test_non_numeric_values_skipped(self):
        """Non-numeric values for token fields are not summed."""
        base = {"input_tokens": "not_a_number", "output_tokens": 50, "cache_write_tokens": 10}
        nudge = {"input_tokens": 200, "output_tokens": 100, "cache_write_tokens": 5}
        result = _merge_token_usage(base, nudge)
        assert result.get("input_tokens") == "not_a_number"
        assert result["output_tokens"] == 150
        assert result["cache_write_tokens"] == 15

    def test_extra_keys_preserved_from_base(self):
        """Extra keys in base dict are preserved in output."""
        base = {"input_tokens": 100, "output_tokens": 50, "provider": "anthropic"}
        nudge = {"input_tokens": 200, "output_tokens": 100}
        result = _merge_token_usage(base, nudge)
        assert result["provider"] == "anthropic"
