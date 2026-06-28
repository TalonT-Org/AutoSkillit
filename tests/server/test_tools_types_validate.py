"""Unit tests for _validate_result helper in server/tools/_types."""

import json

import pytest

from autoskillit.server.tools._types import _validate_result

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


class TestValidateResult:
    def test_pass_all_keys_present(self):
        result = {"success": True, "content": "recipe body", "valid": True}
        assert (
            _validate_result(
                result,
                required_keys=frozenset({"success", "content", "valid"}),
                tool_name="test",
            )
            is None
        )

    def test_missing_key_returns_failure(self):
        result = {"success": True}
        raw = _validate_result(
            result,
            required_keys=frozenset({"success", "content"}),
            tool_name="test",
        )
        assert raw is not None
        parsed = json.loads(raw)
        assert parsed["success"] is False
        assert "content" in parsed["error"]
        assert parsed["stage"] == "validate_result:test"

    def test_none_value_returns_failure(self):
        result = {"success": True, "content": None}
        raw = _validate_result(
            result,
            required_keys=frozenset({"success", "content"}),
            tool_name="test",
        )
        assert raw is not None
        parsed = json.loads(raw)
        assert parsed["success"] is False
        assert "content" in parsed["error"]
        assert "None" in parsed["error"]

    def test_empty_content_returns_failure(self):
        result = {"success": True, "content": ""}
        raw = _validate_result(
            result,
            required_keys=frozenset({"success"}),
            tool_name="test",
        )
        assert raw is not None
        parsed = json.loads(raw)
        assert parsed["success"] is False
        assert "content" in parsed["error"].lower()

    def test_success_false_returns_failure(self):
        result = {"success": False, "content": "something"}
        raw = _validate_result(
            result,
            required_keys=frozenset({"content"}),
            tool_name="test",
        )
        assert raw is not None
        parsed = json.loads(raw)
        assert parsed["success"] is False
        assert "success" in parsed["error"].lower()

    def test_retriable_true_propagates(self):
        result = {"success": True}
        raw = _validate_result(
            result,
            required_keys=frozenset({"success", "missing"}),
            tool_name="test",
            retriable=True,
        )
        assert raw is not None
        parsed = json.loads(raw)
        assert parsed["retriable"] is True

    def test_retriable_false_default(self):
        result = {"success": True}
        raw = _validate_result(
            result,
            required_keys=frozenset({"success", "missing"}),
            tool_name="test",
        )
        assert raw is not None
        parsed = json.loads(raw)
        assert parsed["retriable"] is False
