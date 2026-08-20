"""Typed maintenance-install subprocess boundary shared across package layers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from autoskillit.core._claude_env import build_maintenance_env
from autoskillit.core.paths import is_git_main_checkout, is_git_worktree

__all__ = [
    "InstallMode",
    "MaintenanceInstallArgv",
    "MaintenanceSubprocessInvocation",
    "_MAINTENANCE_EXTRAS",
]

# Recursion guards that suppress prompt-suppression-relevant checks in a
# maintenance child. Lives in the same IL-0 module as the type that builds
# every maintenance-subprocess env, so no spawn site needs its own import or
# a duplicated literal to reach it.
_MAINTENANCE_EXTRAS: Mapping[str, str] = {
    "AUTOSKILLIT_SKIP_STALE_CHECK": "1",
    "AUTOSKILLIT_SKIP_UPDATE_CHECK": "1",
}


class InstallMode(StrEnum):
    """The reason an install transaction was requested."""

    DIRECT = "direct"
    MAINTENANCE_UPDATE = "maintenance-update"


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
    ) -> list[str]:
        """Build the canonical child argv for ``install --maintenance-update``.

        ``require_registered_plugin`` controls whether the
        ``--require-registered-plugin`` flag is appended.
        """
        argv = [
            str(self.entrypoint),
            "install",
            "--maintenance-update",
            "--expected-version",
            self.expected_version,
        ]
        if require_registered_plugin:
            argv.append("--require-registered-plugin")
        return argv


def _validated_maintenance_cwd(cwd: Path) -> Path:
    """Refuse a working directory a maintenance child must never run from.

    A maintenance subprocess deletes and recreates the install root in
    place; running it with a git worktree or main checkout as its cwd is
    the exact hazard ``_transaction.py``'s pre-existing check exists to
    prevent (a scratch dir is pre-verified before the probe and install
    child spawn). This carries that same check into every spawn site,
    including ones that historically passed no ``cwd=`` at all and so
    inherited whatever arbitrary project directory the caller had.
    """
    if is_git_worktree(cwd) or is_git_main_checkout(cwd):
        raise ValueError(
            f"Refusing to build a maintenance subprocess invocation with cwd "
            f"inside a git repository: {cwd}"
        )
    return cwd


@dataclass(frozen=True, slots=True)
class MaintenanceSubprocessInvocation:
    """Complete, immutable self-invocation contract: argv + env + cwd + stdio.

    One value produces the full spawn — argv, the sealed maintenance
    environment, a validated working directory, and an explicit stdio
    capture disposition — so a spawn site cannot supply argv without env,
    env without a validated cwd, or leave stdio to inherit by omission.
    This is *Introduce Parameter Object* applied to a process invocation,
    the same shape ``subprocess.Popen``'s ecosystem peers converge on
    (Java's ``ProcessBuilder``, Rust's ``std::process::Command``, Go's
    ``exec.Cmd`` all bundle program + args + cwd + env + stdio together).

    Argv shape is NOT uniform across callers — a ``--version`` probe and a
    full ``install --maintenance-update`` invocation build different argv —
    so this type does not itself construct argv from a single template.
    Build one via :meth:`for_version_probe` or :meth:`for_install`; both
    route env through :func:`build_maintenance_env`'s allowlist (never
    ``os.environ.copy()``) and both validate ``cwd`` identically.
    """

    argv: tuple[str, ...]
    env: Mapping[str, str]
    cwd: Path
    capture_output: bool = True

    @classmethod
    def for_version_probe(
        cls,
        entrypoint: Path,
        *,
        environment: Mapping[str, str],
        cwd: Path,
    ) -> MaintenanceSubprocessInvocation:
        """Build the ``--version`` probe invocation two spawn sites share."""
        return cls(
            argv=(str(entrypoint), "--version"),
            env=build_maintenance_env(environment, _MAINTENANCE_EXTRAS),
            cwd=_validated_maintenance_cwd(cwd),
            capture_output=True,
        )

    @classmethod
    def for_install(
        cls,
        entrypoint: Path,
        expected_version: str,
        *,
        environment: Mapping[str, str],
        cwd: Path,
        require_registered_plugin: bool = False,
    ) -> MaintenanceSubprocessInvocation:
        """Build the ``install --maintenance-update`` invocation.

        Composes :class:`MaintenanceInstallArgv` for argv construction —
        the sanctioned way to build this argv shape — rather than
        duplicating it.
        """
        argv = MaintenanceInstallArgv(
            entrypoint=entrypoint,
            expected_version=expected_version,
        ).to_argv(require_registered_plugin=require_registered_plugin)
        return cls(
            argv=tuple(argv),
            env=build_maintenance_env(environment, _MAINTENANCE_EXTRAS),
            cwd=_validated_maintenance_cwd(cwd),
            capture_output=True,
        )
