"""Premises shared by SessionStart resume advice and interactive boot."""

from __future__ import annotations

import pytest

from autoskillit.execution.backends import all_backends

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def test_reminder_premise_every_backend_pre_reveals() -> None:
    failure = (
        "session_start_hook no longer advises a no-argument open_kitchen because every "
        "interactive boot pre-reveals the kitchen (server/lifecycle/_lifespan/_session_boots.py "
        "_pre_reveal_kitchen); a backend with supports_tool_list_changed=True invalidates "
        "that premise"
    )
    for backend in all_backends():
        assert backend.capabilities.supports_tool_list_changed is False, (
            f"{backend.name}: {failure}"
        )
