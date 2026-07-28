"""Shared launch scope for exact plugin artifact bindings."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager

from .types import (
    CodingAgentBackend,
    PluginArtifactAuthority,
    PluginLaunchBinding,
    PluginLoadMode,
)

__all__ = ["plugin_launch_binding_scope"]

_CloseFailureReporter = Callable[[BaseException, BaseException], None]


def _close_preserving_primary(
    binding: PluginLaunchBinding,
    primary_error: BaseException,
    reporter: _CloseFailureReporter | None,
) -> None:
    try:
        binding.close()
    except BaseException as cleanup_error:
        primary_error.add_note(f"Plugin artifact binding cleanup failed: {cleanup_error!r}")
        if reporter is None:
            return
        try:
            reporter(primary_error, cleanup_error)
        except BaseException as reporter_error:
            primary_error.add_note(f"Plugin artifact cleanup reporting failed: {reporter_error!r}")


@contextmanager
def plugin_launch_binding_scope(
    *,
    authority: PluginArtifactAuthority | None,
    backend: CodingAgentBackend,
    load_mode: PluginLoadMode,
    on_suppressed_close_error: _CloseFailureReporter | None = None,
) -> Iterator[PluginLaunchBinding | None]:
    """Own a nullable launch binding without replacing an active failure."""
    if not load_mode.consumes_artifact:
        yield None
        return
    if authority is None:
        raise RuntimeError(f"{load_mode.value} launch requires plugin artifact authority")

    binding = authority.acquire_launch_binding(
        backend=backend,
        load_mode=load_mode,
    )
    try:
        yield binding
    except BaseException as primary_error:
        _close_preserving_primary(
            binding,
            primary_error,
            on_suppressed_close_error,
        )
        raise
    else:
        binding.close()
