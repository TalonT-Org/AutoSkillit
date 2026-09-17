"""Small process doubles for the interactive ``Popen`` owner boundary."""

from __future__ import annotations

from typing import TypedDict
from unittest.mock import MagicMock

from autoskillit.core import CmdOrigin, FreshLaunch, PositionalRole, ResumeWithBriefing

_ABSENT_SYNTHETIC_PID = 2_147_483_647


class _InteractiveLaunchMetadata(TypedDict):
    origin: CmdOrigin
    is_resume: bool


class InteractiveProcessStub:
    def __init__(self, returncode: int = 0, *, pid: int | None = None) -> None:
        self.pid = _ABSENT_SYNTHETIC_PID if pid is None else pid
        self.returncode: int | None = None
        self._final_returncode: int = returncode
        self.terminated = False
        self.killed = False

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        if self.returncode is None:
            self.returncode = self._final_returncode
        return self.returncode

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


def configure_popen(mock_popen: MagicMock, *, returncode: int = 0) -> InteractiveProcessStub:
    process = InteractiveProcessStub(returncode)
    mock_popen.return_value = process
    return process


def interactive_launch_metadata(*, binary: str, launch: object) -> _InteractiveLaunchMetadata:
    """Return the CmdSpec metadata required for an interactive test-double build."""
    positional: tuple[tuple[PositionalRole, str], ...] = ()
    if isinstance(launch, ResumeWithBriefing):
        positional = ((PositionalRole.PROMPT, launch.briefing),)
    elif isinstance(launch, FreshLaunch) and launch.initial_prompt is not None:
        positional = ((PositionalRole.PROMPT, launch.initial_prompt),)
    return {
        "origin": CmdOrigin(binary=binary, positional=positional),
        "is_resume": not isinstance(launch, FreshLaunch),
    }
