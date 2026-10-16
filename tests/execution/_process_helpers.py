"""Process doubles shared by liveness watcher tests."""

from functools import partial
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest

from autoskillit.execution.process import _process_race, _session_log_monitor


class ConnectedProcess:
    def __init__(self, pid: int) -> None:
        self.pid = pid

    def children(self, recursive: bool = False) -> list[object]:
        return []

    def connections(self, kind: str | None = None) -> list[SimpleNamespace]:
        return [
            SimpleNamespace(
                status=psutil.CONN_ESTABLISHED,
                raddr=SimpleNamespace(port=443),
            )
        ]

    net_connections = connections


def coordinate_phase2_start(
    monkeypatch: pytest.MonkeyPatch,
    session_dir: Path,
) -> None:
    ready_path = session_dir / "phase2.ready"
    monkeypatch.setattr(
        _process_race,
        "_session_log_monitor",
        partial(_session_log_monitor, _on_poll=ready_path.touch),
    )
