"""Small process doubles for the interactive ``Popen`` owner boundary."""

from __future__ import annotations

import os
from unittest.mock import MagicMock


class InteractiveProcessStub:
    def __init__(self, returncode: int = 0) -> None:
        self.pid = os.getpid()
        self.returncode: int | None = None
        self._final_returncode = returncode

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self.returncode = self._final_returncode
        return self._final_returncode

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.returncode = -15

    def kill(self) -> None:
        self.returncode = -9


def configure_popen(mock_popen: MagicMock, *, returncode: int = 0) -> InteractiveProcessStub:
    process = InteractiveProcessStub(returncode)
    mock_popen.return_value = process
    return process
