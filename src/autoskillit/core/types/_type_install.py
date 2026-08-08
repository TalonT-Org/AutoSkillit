"""Typed maintenance-install subprocess boundary shared across package layers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal

__all__ = ["InstallMode", "MaintenanceInstallArgv"]


class InstallMode(StrEnum):
    """The reason an install transaction was requested."""

    DIRECT = "direct"
    MAINTENANCE_UPDATE = "maintenance-update"


_MaintenanceArgvElement = Literal[
    "install",
    "--maintenance-update",
    "--expected-version",
    "--require-registered-plugin",
]


@dataclass(frozen=True, slots=True)
class MaintenanceInstallArgv:
    """Validated child argv for ``autoskillit install --maintenance-update``.

    Construction enforces ``mode=MAINTENANCE_UPDATE`` and a non-empty
    ``expected_version`` string. The ``.to_argv()`` method produces the
    canonical argv for the install --maintenance-update child process. Use
    this type for ALL subprocess self-invocation of the autoskillit install
    child with ``--maintenance-update``.
    """

    entrypoint: Path
    expected_version: str
    mode: InstallMode = InstallMode.MAINTENANCE_UPDATE

    def __post_init__(self) -> None:
        if not isinstance(self.entrypoint, Path):
            raise TypeError(f"entrypoint must be Path, got {type(self.entrypoint).__name__}")
        if not isinstance(self.expected_version, str) or not self.expected_version.strip():
            raise ValueError("maintenance update requires a non-empty expected_version string")
        if self.mode is not InstallMode.MAINTENANCE_UPDATE:
            raise ValueError("MaintenanceInstallArgv requires mode=MAINTENANCE_UPDATE")

    def to_argv(
        self,
        *,
        require_registered_plugin: bool = False,
    ) -> list[_MaintenanceArgvElement | str]:
        """Build the canonical child argv for ``install --maintenance-update``.

        ``require_registered_plugin`` controls whether the
        ``--require-registered-plugin`` flag is appended.
        """
        argv: list[_MaintenanceArgvElement | str] = [
            str(self.entrypoint),
            "install",
            "--maintenance-update",
            "--expected-version",
            self.expected_version,
        ]
        if require_registered_plugin:
            argv.append("--require-registered-plugin")
        return argv
