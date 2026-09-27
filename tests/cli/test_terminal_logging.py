"""Unit tests for the terminal log-level policy resolver and applier."""

from __future__ import annotations

import logging
import sys
from typing import Any

import pytest

from autoskillit.cli.ui._terminal_logging import (
    TerminalLogPolicy,
    apply_terminal_logging,
    resolve_terminal_logging,
)
from autoskillit.config import LoggingConfig

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]

_EXPECTED_BASELINES = {
    TerminalLogPolicy.SERVICE: logging.INFO,
    TerminalLogPolicy.INTERACTIVE: logging.WARNING,
    TerminalLogPolicy.HEADLESS: logging.INFO,
}


@pytest.mark.parametrize("policy", list(TerminalLogPolicy))
def test_policy_baselines(policy: TerminalLogPolicy) -> None:
    assert policy.baseline == _EXPECTED_BASELINES[policy]


@pytest.mark.parametrize("policy", list(TerminalLogPolicy))
def test_resolve_unset_uses_baseline(policy: TerminalLogPolicy) -> None:
    settings = resolve_terminal_logging(LoggingConfig(), policy, stderr_is_tty=True)
    assert settings.level == policy.baseline


@pytest.mark.parametrize("policy", list(TerminalLogPolicy))
def test_resolve_explicit_more_verbose_wins(policy: TerminalLogPolicy) -> None:
    settings = resolve_terminal_logging(LoggingConfig(level="DEBUG"), policy, stderr_is_tty=True)
    assert settings.level == logging.DEBUG


def test_resolve_explicit_info_raises_interactive_verbosity() -> None:
    settings = resolve_terminal_logging(
        LoggingConfig(level="INFO"), TerminalLogPolicy.INTERACTIVE, stderr_is_tty=True
    )
    assert settings.level == logging.INFO


@pytest.mark.parametrize(
    ("level", "policy", "expected"),
    [
        ("WARNING", TerminalLogPolicy.SERVICE, logging.INFO),
        ("ERROR", TerminalLogPolicy.INTERACTIVE, logging.WARNING),
        ("CRITICAL", TerminalLogPolicy.INTERACTIVE, logging.WARNING),
    ],
)
def test_resolve_explicit_never_lowers_below_baseline(
    level: str, policy: TerminalLogPolicy, expected: int
) -> None:
    settings = resolve_terminal_logging(LoggingConfig(level=level), policy, stderr_is_tty=True)
    assert settings.level == expected


@pytest.mark.parametrize("policy", list(TerminalLogPolicy))
@pytest.mark.parametrize("level", [None, "INFO", "ERROR"])
def test_resolve_verbose_forces_debug(policy: TerminalLogPolicy, level: str | None) -> None:
    settings = resolve_terminal_logging(
        LoggingConfig(level=level), policy, stderr_is_tty=True, verbose=True
    )
    assert settings.level == logging.DEBUG


@pytest.mark.parametrize(
    ("json_output", "stderr_is_tty", "expected"),
    [
        (True, True, True),
        (True, False, True),
        (False, True, False),
        (False, False, False),
        (None, True, False),
        (None, False, True),
    ],
)
def test_resolve_json_output(
    json_output: bool | None, stderr_is_tty: bool, expected: bool
) -> None:
    settings = resolve_terminal_logging(
        LoggingConfig(json_output=json_output),
        TerminalLogPolicy.INTERACTIVE,
        stderr_is_tty=stderr_is_tty,
    )
    assert settings.json_output is expected


def test_apply_calls_configure_logging_on_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "autoskillit.core.configure_logging", lambda **kwargs: calls.append(kwargs)
    )

    settings = apply_terminal_logging(LoggingConfig(level="DEBUG"), TerminalLogPolicy.INTERACTIVE)

    assert len(calls) == 1
    assert calls[0]["level"] == logging.DEBUG
    assert calls[0]["stream"] is sys.stderr
    assert calls[0]["json_output"] is settings.json_output
    assert settings.level == logging.DEBUG
