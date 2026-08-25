"""Composed, lock-owned Codex configuration pre-launch transaction."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from autoskillit.execution.backends._codex_config import (
    _ensure_codex_mcp_registered_unlocked,
)
from autoskillit.execution.backends._codex_config_lock import CodexConfigLock
from autoskillit.execution.backends._codex_hooks import (
    _sync_hooks_to_codex_config_unlocked,
)


def _staged_error(stage: str, exc: Exception) -> RuntimeError:
    """Build a RuntimeError tagged with the pre-launch stage that failed, chained to *exc*."""
    return RuntimeError(f"{stage}: {type(exc).__name__}: {exc}")


@contextmanager
def codex_prelaunch_transaction(
    *,
    source_codex_home: Path,
    hook_config_format: str = "",
    plugin_dir: Path | None = None,
) -> Iterator[Path]:
    """Synchronize the source config and hold its lock across caller work.

    The yielded path names the exact config protected by the transaction.
    Callers may snapshot or validate those bytes before leaving the context;
    unique generated-home mutations happen only after this context exits.
    """
    resolved_home = Path(source_codex_home).expanduser().resolve(strict=False)
    config_path = resolved_home / "config.toml"
    with CodexConfigLock(config_path):
        try:
            _ensure_codex_mcp_registered_unlocked(config_path=config_path)
        except Exception as exc:
            raise _staged_error("source-config sync", exc) from exc
        try:
            _sync_hooks_to_codex_config_unlocked(
                config_path=config_path,
                hook_config_format=hook_config_format,
                plugin_dir=plugin_dir,
            )
        except Exception as exc:
            raise _staged_error("hook update", exc) from exc
        yield config_path
