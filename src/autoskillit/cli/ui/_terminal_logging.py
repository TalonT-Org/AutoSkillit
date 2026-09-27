"""Terminal log-level policy for every process entry point.

Rules:

- ``configure_logging`` is called only by :func:`apply_terminal_logging` and by
  serve's two boot phases (guarded by ``tests/arch/test_launch_terminal_logging.py``).
- Each launch entry calls :func:`apply_terminal_logging` immediately after
  ``load_config`` and before any prompt or launch sink (same guard).
- An explicit ``logging.level`` can only raise verbosity above the policy's
  baseline, never lower it: serve's long-standing rule, which also guarantees
  that no config value hides WARNING-level failure records such as plugin
  artifact lifecycle outcomes other than ``succeeded``.
- The INTERACTIVE baseline is WARNING because routine lifecycle successes log at
  INFO and must stay off the operator's prompt, while failures log at WARNING
  and must stay visible.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, assert_never

if TYPE_CHECKING:
    from autoskillit.config import LoggingConfig


class TerminalLogPolicy(StrEnum):
    SERVICE = "service"  # serve: long-running MCP server stderr
    INTERACTIVE = "interactive"  # cook, order, fleet dispatch, fleet campaign
    HEADLESS = "headless"  # fleet run: JSON envelope on stdout, diagnostics on stderr

    @property
    def baseline(self) -> int:
        match self:
            case TerminalLogPolicy.SERVICE | TerminalLogPolicy.HEADLESS:
                return logging.INFO
            case TerminalLogPolicy.INTERACTIVE:
                return logging.WARNING
            case _:
                assert_never(self)


@dataclass(frozen=True, slots=True)
class TerminalLogSettings:
    level: int
    json_output: bool


def resolve_terminal_logging(
    logging_cfg: LoggingConfig,
    policy: TerminalLogPolicy,
    *,
    stderr_is_tty: bool,
    verbose: bool = False,
) -> TerminalLogSettings:
    cli_level = logging.DEBUG if verbose else policy.baseline
    configured = (
        policy.baseline
        if logging_cfg.level is None
        else logging.getLevelNamesMapping()[logging_cfg.level]
    )
    json_output = (
        logging_cfg.json_output if logging_cfg.json_output is not None else not stderr_is_tty
    )
    return TerminalLogSettings(level=min(configured, cli_level), json_output=json_output)


def apply_terminal_logging(
    logging_cfg: LoggingConfig, policy: TerminalLogPolicy
) -> TerminalLogSettings:
    import autoskillit.core as core  # call-time lookup so tests can patch configure_logging

    settings = resolve_terminal_logging(logging_cfg, policy, stderr_is_tty=sys.stderr.isatty())
    core.configure_logging(
        level=settings.level, json_output=settings.json_output, stream=sys.stderr
    )
    return settings
