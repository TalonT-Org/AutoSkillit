"""Composed, lock-owned Codex configuration pre-launch transaction."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from autoskillit.core import CodexRuntimeSpec, atomic_write
from autoskillit.execution.backends._codex_config import (
    _apply_codex_runtime_spec_unlocked,
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
    destination_home: Path,
    runtime_spec: CodexRuntimeSpec,
    hook_config_format: str = "",
    plugin_dir: Path | None = None,
) -> Iterator[Path]:
    """Provision a generated-home config from read-only native preferences.

    The source config is read once without locking or mutation. The destination
    config is then locked for all wrapper-owned configuration and hook writes.
    """
    source_config_path = Path(source_codex_home).expanduser().resolve(strict=False) / "config.toml"
    try:
        source_bytes = source_config_path.read_bytes()
    except FileNotFoundError:
        source_bytes = b""
    except OSError as exc:
        raise _staged_error("source-config read", exc) from exc

    config_path = Path(destination_home).expanduser().resolve(strict=False) / "config.toml"
    try:
        atomic_write(config_path, source_bytes)
    except Exception as exc:
        raise _staged_error("destination snapshot", exc) from exc

    with CodexConfigLock(config_path):
        try:
            _ensure_codex_mcp_registered_unlocked(config_path=config_path)
        except Exception as exc:
            raise _staged_error("runtime MCP sync", exc) from exc
        try:
            _apply_codex_runtime_spec_unlocked(
                config_path=config_path,
                runtime_spec=runtime_spec,
            )
        except Exception as exc:
            raise _staged_error("runtime tuning", exc) from exc
        try:
            _sync_hooks_to_codex_config_unlocked(
                config_path=config_path,
                hook_config_format=hook_config_format,
                plugin_dir=plugin_dir,
                include_runtime_only=True,
            )
        except Exception as exc:
            raise _staged_error("runtime hook update", exc) from exc
        yield config_path
