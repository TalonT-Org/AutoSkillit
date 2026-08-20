"""Tests for the AutoSkillit-owned exec-time entrypoint shim.

Covers ``render_entrypoint_shim`` / ``entrypoint_shim_path`` /
``write_entrypoint_shim`` (pure path and I/O logic) plus the shim's own
rendered source, which is executed as a real subprocess to prove the
single-resolution property described in ``core/_entrypoint_shim.py``'s
module docstring: the shim resolves the ``current`` generation selector
exactly once, then ``exec``s into it, never re-consulting the selector
afterward. Full concurrent-flip coverage (a flip racing a fresh process
launch, rather than one racing an already-running process) lives in the
broader T-C1/T-C2 subprocess-survival tests elsewhere in this plan.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

from autoskillit.core import (
    _AUTOSKILLIT_INSTALL_ROOT_KEY,
    entrypoint_shim_path,
    generation_plugin_selector_path,
    render_entrypoint_shim,
    write_entrypoint_shim,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.medium]


# ---------------------------------------------------------------------------
# Path consistency: the shim's hardcoded selector literal vs. the real helper
# ---------------------------------------------------------------------------


def test_shim_selector_literal_matches_generation_plugin_selector_path(tmp_path: Path) -> None:
    """The shim can't import ``generation_plugin_selector_path`` (bootstrap
    constraint), so its path is duplicated as string literals. Assert the
    duplication stays in sync with the real function's actual output."""
    home = tmp_path / "home"
    expected = generation_plugin_selector_path(home, _AUTOSKILLIT_INSTALL_ROOT_KEY)
    segments = expected.relative_to(home).parts
    assert segments == (".autoskillit", "plugin-generations", "autoskillit-install", "current")

    source = render_entrypoint_shim()
    cursor = 0
    for segment in segments:
        needle = f'"{segment}"'
        found = source.index(needle, cursor)
        cursor = found + len(needle)


# ---------------------------------------------------------------------------
# entrypoint_shim_path()
# ---------------------------------------------------------------------------


def test_entrypoint_shim_path_is_well_known_location(tmp_path: Path) -> None:
    home = tmp_path / "home"
    assert entrypoint_shim_path(home) == home / ".local" / "bin" / "autoskillit"


# ---------------------------------------------------------------------------
# write_entrypoint_shim()
# ---------------------------------------------------------------------------


def test_write_entrypoint_shim_creates_executable_file_on_first_call(tmp_path: Path) -> None:
    home = tmp_path / "home"

    changed = write_entrypoint_shim(home)

    path = entrypoint_shim_path(home)
    assert changed is True
    assert path.read_text() == render_entrypoint_shim()
    assert os.access(path, os.X_OK)
    assert stat.S_IMODE(path.stat().st_mode) == 0o755


def test_write_entrypoint_shim_second_call_is_a_noop(tmp_path: Path) -> None:
    home = tmp_path / "home"
    write_entrypoint_shim(home)

    assert write_entrypoint_shim(home) is False


def test_write_entrypoint_shim_rewrites_stale_content(tmp_path: Path) -> None:
    home = tmp_path / "home"
    path = entrypoint_shim_path(home)
    path.parent.mkdir(parents=True)
    path.write_text("#!/usr/bin/env python3\nprint('stale shim')\n")

    changed = write_entrypoint_shim(home)

    assert changed is True
    assert path.read_text() == render_entrypoint_shim()


def test_write_entrypoint_shim_writes_via_temp_file_and_os_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Writes must go through temp-file + ``os.replace`` so a concurrently
    exec'ing reader never observes a partially written shim.

    ``write_entrypoint_shim()`` delegates to ``core.io.atomic_write()``
    (the shared temp-file + ``os.replace`` + fsync primitive — REQ-CNST
    requires every durable write in ``src/`` to route through it rather than
    hand-rolling its own), so the spy is on ``core.io``'s own ``os.replace``,
    not on anything in ``_entrypoint_shim`` itself.
    """
    import autoskillit.core.io as _io

    home = tmp_path / "home"
    real_replace = os.replace
    calls: list[tuple[Path, Path]] = []

    def _spy_replace(src: object, dst: object) -> None:
        calls.append((Path(src), Path(dst)))  # type: ignore[arg-type]
        real_replace(src, dst)  # type: ignore[arg-type]

    monkeypatch.setattr(_io.os, "replace", _spy_replace)

    write_entrypoint_shim(home)

    assert len(calls) == 1
    temporary, target = calls[0]
    assert target == entrypoint_shim_path(home)
    assert temporary != target
    assert temporary.parent == target.parent
    assert not temporary.exists()  # renamed away by os.replace


# ---------------------------------------------------------------------------
# Real exec-time behavior: single-resolution property (T-C6)
# ---------------------------------------------------------------------------

_MARKER_WRITER = (
    "#!/usr/bin/env python3\n"
    "import sys\n"
    "from pathlib import Path\n"
    "Path(sys.argv[1]).write_text('RAN-FROM-A')\n"
)

_SELF_FLIPPING_INNER = (
    "#!/usr/bin/env python3\n"
    "import sys\n"
    "from pathlib import Path\n"
    "marker = Path(sys.argv[1])\n"
    "selector = Path(sys.argv[2])\n"
    "new_target = Path(sys.argv[3])\n"
    "with marker.open('a') as fh:\n"
    "    fh.write('RAN-FROM-A\\n')\n"
    "selector.unlink()\n"
    "selector.symlink_to(new_target)\n"
    "with marker.open('a') as fh:\n"
    "    fh.write('STILL-A-AFTER-FLIP\\n')\n"
)

_MARKER_APPENDER_B = (
    "#!/usr/bin/env python3\n"
    "import sys\n"
    "from pathlib import Path\n"
    "with Path(sys.argv[1]).open('a') as fh:\n"
    "    fh.write('RAN-FROM-B\\n')\n"
)


def _write_executable(path: Path, source: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source)
    path.chmod(0o755)


def _install_shim(tmp_path: Path) -> Path:
    shim_path = tmp_path / "autoskillit-shim"
    _write_executable(shim_path, render_entrypoint_shim())
    return shim_path


def test_shim_execs_into_resolved_generation(tmp_path: Path) -> None:
    """The shim reads the ``current`` selector and ``exec``s into the
    generation it points to."""
    home = tmp_path / "home"
    generation_a = tmp_path / "store" / "gen-a"
    _write_executable(generation_a / "autoskillit" / "bin" / "autoskillit", _MARKER_WRITER)

    selector = generation_plugin_selector_path(home, _AUTOSKILLIT_INSTALL_ROOT_KEY)
    selector.parent.mkdir(parents=True)
    selector.symlink_to(generation_a)

    shim_path = _install_shim(tmp_path)
    marker = tmp_path / "marker.txt"

    result = subprocess.run(
        [str(shim_path), str(marker)],
        env={**os.environ, "HOME": str(home)},
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert marker.read_text() == "RAN-FROM-A"


def test_shim_already_execd_process_is_immune_to_post_read_selector_flip(
    tmp_path: Path,
) -> None:
    """Once the shim has resolved the selector and ``exec``'d, flipping the
    selector cannot redirect the already-running process.

    Generation A's own inner script flips ``current`` to point at generation
    B *while it is running*, then keeps writing to the marker. Because
    ``os.execv`` already replaced the process image before the flip, the
    running process has no selector left to re-consult — it simply keeps
    executing as generation A's program. A second, fresh shim invocation
    afterward confirms the flip *did* take effect for new processes, proving
    the isolation is specific to the already-exec'd process, not a no-op
    flip.
    """
    home = tmp_path / "home"
    generation_a = tmp_path / "store" / "gen-a"
    generation_b = tmp_path / "store" / "gen-b"
    _write_executable(generation_a / "autoskillit" / "bin" / "autoskillit", _SELF_FLIPPING_INNER)
    _write_executable(generation_b / "autoskillit" / "bin" / "autoskillit", _MARKER_APPENDER_B)

    selector = generation_plugin_selector_path(home, _AUTOSKILLIT_INSTALL_ROOT_KEY)
    selector.parent.mkdir(parents=True)
    selector.symlink_to(generation_a)

    shim_path = _install_shim(tmp_path)
    marker = tmp_path / "marker.txt"
    marker.write_text("")
    env = {**os.environ, "HOME": str(home)}

    first = subprocess.run(
        [str(shim_path), str(marker), str(selector), str(generation_b)],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert first.returncode == 0, first.stderr
    assert marker.read_text() == "RAN-FROM-A\nSTILL-A-AFTER-FLIP\n"
    # The flip landed on disk; the already-exec'd process above was
    # unaffected by it, but the selector now genuinely points at B.
    assert selector.resolve() == generation_b.resolve()

    second = subprocess.run(
        [str(shim_path), str(marker)],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert second.returncode == 0, second.stderr
    assert marker.read_text() == "RAN-FROM-A\nSTILL-A-AFTER-FLIP\nRAN-FROM-B\n"
