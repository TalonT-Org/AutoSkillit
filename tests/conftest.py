"""Shared test fixtures for automation-mcp."""

import pytest


@pytest.fixture(autouse=True)
def _enable_tools_for_tests():
    """Enable bugfix-loop tools for all tests (mirrors production activation).

    Tests that need the disabled state should use a local fixture to override.
    """
    from automation_mcp import server

    server._tools_enabled = True
    yield
    server._tools_enabled = False


@pytest.fixture(autouse=True)
def _test_config(monkeypatch):
    """Provide a default test config for all tests."""
    from automation_mcp import config, server

    test_cfg = config.AutomationConfig()
    monkeypatch.setattr(server, "_config", test_cfg)
