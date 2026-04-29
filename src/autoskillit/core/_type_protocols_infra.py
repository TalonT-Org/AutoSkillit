"""Infrastructure and pipeline-control protocol definitions."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "GateState",
    "BackgroundSupervisor",
    "FleetLock",
    "QuotaRefreshTask",
    "TokenFactory",
    "CampaignProtector",
]


@runtime_checkable
class GateState(Protocol):
    """Protocol for gate enable/disable state."""

    @property
    def enabled(self) -> bool: ...

    def enable(self) -> None: ...

    def disable(self) -> None: ...


@runtime_checkable
class BackgroundSupervisor(Protocol):
    """Protocol for supervised background task execution."""

    @property
    def pending_count(self) -> int: ...

    def submit(
        self,
        coro: Any,
        *,
        on_exception: Any | None = None,
        status_path: Any | None = None,
        label: str = "",
    ) -> Any: ...

    async def drain(self) -> None: ...


@runtime_checkable
class FleetLock(Protocol):
    """Protocol for a semaphore-style fleet dispatch guard.

    Default implementation is FleetSemaphore in server/_factory.py.
    """

    def at_capacity(self) -> bool: ...

    async def acquire(self) -> None: ...

    def release(self) -> None: ...

    @property
    def active_count(self) -> int: ...

    @property
    def max_concurrent(self) -> int: ...


@runtime_checkable
class QuotaRefreshTask(Protocol):
    """Protocol for a cancellable background task handle.

    Satisfied by asyncio.Task — used to type the kitchen-scoped quota
    refresh task stored in ToolContext without leaking asyncio.Task into the
    core layer.
    """

    def cancel(self, msg: Any = None) -> bool: ...


@runtime_checkable
class TokenFactory(Protocol):
    """Protocol for resolving a GitHub token via the config → env → CLI fallback chain.

    Satisfied by any zero-argument callable that returns a token string or None.
    Set by make_context() on ToolContext; None in test ToolContext instances unless
    explicitly provided.
    """

    def __call__(self) -> str | None: ...


class CampaignProtector(Protocol):
    """Protocol for resolving the set of protected campaign IDs for session retention.

    Satisfied by any callable that accepts a project root Path and returns a frozenset
    of campaign ID strings that should not be purged during log retention.
    Set by make_context() on ToolContext; None in test ToolContext instances unless
    explicitly provided.
    """

    def __call__(self, project_dir: Path) -> frozenset[str]: ...
