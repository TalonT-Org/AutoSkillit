"""Shared test fixtures for autoskillit."""

from collections.abc import Generator

import pytest
import structlog


@pytest.fixture(autouse=True)
def _enable_tools_for_tests(monkeypatch):
    """Enable AutoSkillit tools for all tests (mirrors production activation).

    Tests that need the disabled state should use a local fixture to override.
    """
    from autoskillit import server

    monkeypatch.setattr(server, "_tools_enabled", True)


@pytest.fixture(autouse=True)
def _test_config(monkeypatch):
    """Provide a default test config for all tests."""
    from autoskillit import config, server

    test_cfg = config.AutomationConfig()
    monkeypatch.setattr(server, "_config", test_cfg)


def _flush_logger_proxy_caches() -> None:
    """Reconnect autoskillit module-level loggers to the current structlog config.

    Two separate caching mechanisms break capture_logs() after configure_logging():

    1. BoundLoggerLazyProxy: configure_logging() (cache_logger_on_first_use=True)
       replaces proxy.bind with a finalized_bind closure. reset_defaults() creates
       a new processor list but does NOT remove the closure. Fix: pop "bind" from
       the proxy's __dict__ so the next call re-evaluates from global config.

    2. BoundLoggerFilteringAtNotset (returned by proxy.bind()):
       Holds _processors as a reference to the processor list at bind() time.
       reset_defaults() creates a new list — _processors is orphaned. Fix: reset
       _processors to the current default processor list (which capture_logs()
       modifies in-place).
    """
    import sys

    import structlog._config as _sc

    current_procs = structlog.get_config()["processors"]

    for mod_name in list(sys.modules):
        if not mod_name.startswith("autoskillit"):
            continue
        mod = sys.modules.get(mod_name)
        if mod is None:
            continue
        lg = getattr(mod, "logger", None)
        if lg is None:
            continue
        if isinstance(lg, _sc.BoundLoggerLazyProxy):
            lg.__dict__.pop("bind", None)
        elif hasattr(lg, "_processors"):
            # Resolved bound logger — reconnect to current processor list
            lg._processors = current_procs


@pytest.fixture(autouse=True)
def _reset_structlog():
    """Reset structlog config before each test.

    cache_logger_on_first_use=True caches the processor chain on first call.
    Tests that call configure_logging() must call _flush_logger_proxy_caches()
    to clear instance-level bind overrides from module-level proxies, because
    reset_defaults() creates a new processor list but does not remove the
    cached finalized_bind closure from existing BoundLoggerLazyProxy instances.
    """
    structlog.reset_defaults()
    _flush_logger_proxy_caches()
    yield
    structlog.reset_defaults()
    _flush_logger_proxy_caches()


@pytest.fixture(autouse=True)
def _reset_audit_log():
    """Clear the module-level _audit_log singleton before each test.

    Without this, failures recorded in one test class bleed into assertions
    in the next. The singleton is process-global — autouse ensures isolation.
    """
    from autoskillit._audit import _audit_log

    _audit_log.clear()
    yield
    _audit_log.clear()


@pytest.fixture(autouse=True)
def _reset_token_log() -> Generator[None, None, None]:
    """Clear the module-level _token_log singleton before each test."""
    from autoskillit._token_log import _token_log

    _token_log.clear()
    yield
    _token_log.clear()
