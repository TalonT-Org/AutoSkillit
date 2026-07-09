"""Validates that every symbol in autoskillit.core.__all__ is importable via the public gateway."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]


def test_all_public_symbols_importable() -> None:
    """Every symbol in autoskillit.core.__all__ must be importable via the public gateway."""
    import autoskillit.core as core_module

    failures: list[str] = []
    for symbol in core_module.__all__:
        try:
            getattr(core_module, symbol)
        except (ImportError, AttributeError) as exc:
            failures.append(f"{symbol}: {exc}")
    assert not failures, "Public API surface broken:\n" + "\n".join(failures)


def test_new_coding_agent_backend_names_importable() -> None:
    """Each CodingAgentBackend-extraction public name is importable and is a class."""
    import inspect

    from autoskillit.core import (
        CmdSpec,
        CodingAgentBackend,
        EnvPolicy,
        ResultParser,
        SessionEvent,
        SessionLocator,
        StreamParser,
    )

    assert inspect.isclass(CmdSpec)
    assert inspect.isclass(CodingAgentBackend)
    assert inspect.isclass(EnvPolicy)
    assert inspect.isclass(ResultParser)
    assert inspect.isclass(SessionEvent)
    assert inspect.isclass(SessionLocator)
    assert inspect.isclass(StreamParser)
