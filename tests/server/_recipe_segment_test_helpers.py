"""Shared stubs for handler-local recipe-segment branch tests."""

from __future__ import annotations

from types import ModuleType

import pytest

from autoskillit.server._recipe_segment_delivery import PreparedRecipeSegmentDelivery


def install_prepared_recipe_segment(
    monkeypatch: pytest.MonkeyPatch,
    module: ModuleType,
    *,
    step_name: str,
) -> PreparedRecipeSegmentDelivery:
    prepared = PreparedRecipeSegmentDelivery(
        step_name=step_name,
        success_carrier={
            "kind": "success",
            "source_step": step_name,
            "bodies": [{"step": "next", "body": "next:\n  action: stop\n"}],
        },
        recovery_carrier={
            "kind": "recovery",
            "source_step": step_name,
            "target_steps": ["next"],
            "pull_closure": ["next"],
            "pull_requests": [{"section": "next", "part": 0}],
            "recipe_pull": {
                "pull_tool": "get_recipe_section",
                "payload_sha256": "sha256:" + ("0" * 64),
            },
        },
    )

    def _prepare(_tool_ctx, actual_step_name):
        assert actual_step_name == step_name
        return prepared

    monkeypatch.setattr(module, "prepare_recipe_segment_delivery", _prepare)
    return prepared


def assert_recovery_recipe_segment(result: dict[str, object], *, step_name: str) -> None:
    segment = result["recipe_segment"]
    assert isinstance(segment, dict)
    assert segment == {
        "kind": "recovery",
        "source_step": step_name,
        "target_steps": ["next"],
        "pull_closure": ["next"],
        "pull_requests": [{"section": "next", "part": 0}],
        "recipe_pull": {
            "pull_tool": "get_recipe_section",
            "payload_sha256": "sha256:" + ("0" * 64),
        },
    }
    assert "bodies" not in segment
