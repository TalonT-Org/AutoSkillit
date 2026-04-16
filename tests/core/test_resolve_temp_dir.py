"""Tests for autoskillit.core.io.resolve_temp_dir."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from autoskillit.core.io import resolve_temp_dir

pytestmark = [pytest.mark.layer("core"), pytest.mark.medium]


def test_resolve_temp_dir_default_returns_project_relative() -> None:
    assert resolve_temp_dir(Path("/proj"), None) == Path("/proj/.autoskillit/temp")


def test_resolve_temp_dir_relative_override_anchored_to_project() -> None:
    assert resolve_temp_dir(Path("/proj"), ".build/scratch") == Path("/proj/.build/scratch")


def test_resolve_temp_dir_absolute_override_passthrough() -> None:
    assert resolve_temp_dir(Path("/proj"), "/mnt/ramdisk/at") == Path("/mnt/ramdisk/at")


def test_resolve_temp_dir_empty_string_raises_value_error() -> None:
    with pytest.raises(ValueError, match="empty string"):
        resolve_temp_dir(Path("/proj"), "")


def test_resolve_temp_dir_no_autoskillit_imports() -> None:
    """core.io must not transitively import any non-core autoskillit module.

    Runs in an isolated subprocess so the destructive ``sys.modules`` clear
    cannot leak into other xdist tests sharing the same worker process.
    """
    import subprocess
    import textwrap

    script = textwrap.dedent(
        """
        import sys
        for m in list(sys.modules):
            if m == "autoskillit" or m.startswith("autoskillit."):
                del sys.modules[m]
        import autoskillit.core.io  # noqa: F401
        leaked = [
            m
            for m in sys.modules
            if m.startswith("autoskillit.")
            and not m.startswith("autoskillit.core")
            and m != "autoskillit"
        ]
        if leaked:
            print("LEAKED:" + ",".join(sorted(leaked)))
            sys.exit(1)
        sys.exit(0)
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"core.io leaked imports outside core/: {result.stdout.strip()}"
