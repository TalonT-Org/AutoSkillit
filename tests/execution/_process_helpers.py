"""Process doubles shared by liveness watcher tests."""

from types import SimpleNamespace

import psutil


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
