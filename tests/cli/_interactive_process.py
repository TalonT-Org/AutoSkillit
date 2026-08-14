"""Small process doubles for the interactive ``Popen`` owner boundary."""

from __future__ import annotations

from unittest.mock import MagicMock

_ABSENT_SYNTHETIC_PID = 2_147_483_647


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
