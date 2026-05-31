"""Unit tests for _is_parent_assistant_record predicate."""

from __future__ import annotations

import pytest

from autoskillit.execution.session._session_model import _is_parent_assistant_record

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def test_parent_record_identified():
    obj = {
        "type": "assistant",
        "message": {
            "model": "claude-opus-4-6",
            "usage": {"input_tokens": 100, "output_tokens": 50},
        },
    }
    assert _is_parent_assistant_record(obj) is True


def test_subagent_record_excluded():
    obj = {
        "type": "assistant",
        "subagent_type": "Explore",
        "message": {
            "model": "claude-sonnet-4-6",
            "usage": {"input_tokens": 100, "output_tokens": 50},
        },
    }
    assert _is_parent_assistant_record(obj) is False


def test_synthetic_model_excluded():
    obj = {
        "type": "assistant",
        "message": {"model": "<synthetic>", "usage": {"input_tokens": 0, "output_tokens": 0}},
    }
    assert _is_parent_assistant_record(obj) is False


def test_non_assistant_excluded():
    obj = {"type": "result", "usage": {"input_tokens": 100, "output_tokens": 50}}
    assert _is_parent_assistant_record(obj) is False


def test_empty_subagent_type_treated_as_parent():
    """Empty string subagent_type is falsy — record is treated as parent."""
    obj = {
        "type": "assistant",
        "subagent_type": "",
        "message": {
            "model": "claude-opus-4-6",
            "usage": {"input_tokens": 100, "output_tokens": 50},
        },
    }
    assert _is_parent_assistant_record(obj) is True
