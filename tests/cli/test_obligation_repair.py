"""Tests for the env/stdio contract of the obligation-repair subprocess spawns.

``attempt_obligation_repair`` spawns two children — a ``--version`` probe and
an ``install --maintenance-update`` child — both built via
``MaintenanceSubprocessInvocation`` (see
``autoskillit.core.MaintenanceSubprocessInvocation.for_version_probe`` /
``.for_install``). This module pins two contracts that regressed pre-Phase-1:

1. Both children's env must carry the maintenance recursion guards
   (``AUTOSKILLIT_SKIP_UPDATE_CHECK`` / ``AUTOSKILLIT_SKIP_STALE_CHECK``),
   even when the caller-supplied base environment lacks them.
2. The install child's stdio must be explicitly captured
   (``capture_output=True``), not left to inherit by omission.

Broader repair-outcome behavior (verification, hook validation, obligation
clearing) is covered by ``tests/contracts/test_publication_obligation_loop.py``
and self-invocation reentrancy is covered by
``tests/cli/test_app_main.py``; this file is scoped to the env/stdio contract
of the two subprocess spawns only.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from autoskillit.cli.update._obligation_repair import attempt_obligation_repair
from autoskillit.workspace import write_obligation

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium]

_CallRecord = tuple[list[str], dict[str, Any]]


def _make_recording_runner(
    calls: list[_CallRecord],
) -> Callable[..., subprocess.CompletedProcess[str]]:
    """Fake ``_ProcessRunner`` that records argv + kwargs for every spawn.

    Returns a successful ``--version`` probe and a successful install for
    whichever argv shape it is handed, so both children in
    ``attempt_obligation_repair`` are reached and recorded.
    """

    def runner(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((list(argv), dict(kwargs)))
        if argv[-1] == "--version":
            return subprocess.CompletedProcess(argv, 0, stdout="0.2.0\n", stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    return runner


def _stage_pending_obligation(home: Path) -> None:
    write_obligation(home, previous_version="0.1.0", originating_phase="upgrade-subprocess-gate")


def test_repair_install_child_env_carries_update_skip_flags(tmp_path: Path) -> None:
    """Both the probe and install children must carry the recursion guards.

    Pre-Phase-1, the install child was launched with the caller's raw
    ``child_env`` (a plain ``dict(env)`` with only ``HOME`` overridden) and
    never gained ``AUTOSKILLIT_SKIP_UPDATE_CHECK`` /
    ``AUTOSKILLIT_SKIP_STALE_CHECK`` — the actual double-prompt root cause
    this phase fixes. Post-Phase-1, both children route through
    ``build_maintenance_env()`` via ``MaintenanceSubprocessInvocation``,
    which always injects both guards regardless of what the caller supplied.
    """
    home = tmp_path
    _stage_pending_obligation(home)

    base_env = {"HOME": str(home), "PATH": "/usr/bin"}
    assert "AUTOSKILLIT_SKIP_UPDATE_CHECK" not in base_env
    assert "AUTOSKILLIT_SKIP_STALE_CHECK" not in base_env

    calls: list[_CallRecord] = []
    runner = _make_recording_runner(calls)

    attempt_obligation_repair(
        home,
        environment=base_env,
        process_runner=runner,
        entrypoint=Path("/usr/bin/autoskillit"),
    )

    assert len(calls) == 2, "expected both the --version probe and install children to spawn"
    probe_argv, probe_kwargs = calls[0]
    install_argv, install_kwargs = calls[1]
    assert probe_argv[-1] == "--version"
    assert install_argv[1:3] == ["install", "--maintenance-update"]

    for label, kwargs in (("probe", probe_kwargs), ("install", install_kwargs)):
        env = kwargs["env"]
        assert env["AUTOSKILLIT_SKIP_UPDATE_CHECK"] == "1", (
            f"{label} child env missing AUTOSKILLIT_SKIP_UPDATE_CHECK"
        )
        assert env["AUTOSKILLIT_SKIP_STALE_CHECK"] == "1", (
            f"{label} child env missing AUTOSKILLIT_SKIP_STALE_CHECK"
        )


def test_repair_install_child_stdio_is_captured(tmp_path: Path) -> None:
    """The install child's stdio must be explicitly captured.

    Pre-Phase-1, the probe call passed ``capture_output=True`` but the
    install child call passed no capture kwargs at all — it inherited the
    parent's stdio. Post-Phase-1, ``MaintenanceSubprocessInvocation``
    defaults ``capture_output=True`` and both spawn sites now pass it
    explicitly.
    """
    home = tmp_path
    _stage_pending_obligation(home)

    base_env = {"HOME": str(home), "PATH": "/usr/bin"}

    calls: list[_CallRecord] = []
    runner = _make_recording_runner(calls)

    attempt_obligation_repair(
        home,
        environment=base_env,
        process_runner=runner,
        entrypoint=Path("/usr/bin/autoskillit"),
    )

    assert len(calls) == 2, "expected both the --version probe and install children to spawn"
    install_argv, install_kwargs = calls[1]
    assert install_argv[1:3] == ["install", "--maintenance-update"]
    assert install_kwargs.get("capture_output") is True
