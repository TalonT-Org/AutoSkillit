"""Integration tests for the obligation-repair subprocess composition.

These tests spawn a real subprocess through attempt_obligation_repair,
exercising the full argv-construction contract (the typed builder) end
to end. The fake `autoskillit` entrypoint is a thin shell script that
records argv and exits 0 — sufficient to verify that the parent
process produces the canonical argv, not to verify the child's full
behavior (covered by unit tests in test_publication_obligation_loop.py).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("integration"), pytest.mark.medium]


def _build_fake_entrypoint(
    bin_dir: Path, *, version_output: str = "1.1.0\n", exit_code: int = 0
) -> Path:
    """Create a thin `autoskillit` Python shim that records argv to a log.

    The shim treats `--version` specially (print version, exit 0) so the
    pre-launch probe succeeds. For all other commands, it records argv and
    exits with the configured exit_code (default 0). Using Python (rather
    than a shell script) avoids quoting hazards around embedded newlines.
    """
    bin_dir.mkdir(parents=True, exist_ok=True)
    log_path = str(bin_dir / "fake-args.jsonl")
    entrypoint = bin_dir / "autoskillit"
    script = (
        "#!/usr/bin/env python3\n"
        "import sys\n"
        f"LOG = {log_path!r}\n"
        f"VERSION_OUTPUT = {version_output!r}\n"
        f"EXIT_CODE = {exit_code}\n"
        "if '--version' in sys.argv:\n"
        "    sys.stdout.write(VERSION_OUTPUT)\n"
        "    sys.exit(0)\n"
        "with open(LOG, 'a', encoding='utf-8') as f:\n"
        "    f.write(' '.join(sys.argv[1:]) + '\\n')\n"
        "sys.exit(EXIT_CODE)\n"
    )
    entrypoint.write_text(script, encoding="utf-8")
    entrypoint.chmod(0o755)
    return entrypoint


def test_obligation_repair_e2e_clears_obligation_with_typed_argv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real subprocess composition: pending obligation + real repair spawn
    + child receives --expected-version + obligation is cleared.

    Spawns the full attempt_obligation_repair chain with a fake
    `autoskillit` entrypoint that records argv to a log file. Asserts
    the canonical argv is produced (no flagless regression) and the
    obligation is cleared end-to-end.
    """
    from unittest.mock import MagicMock

    from autoskillit.cli.update import _obligation_repair as m

    attempt_obligation_repair = m.attempt_obligation_repair
    from autoskillit.workspace import (
        read_obligation,
        update_obligation_expected_version,
        write_obligation,
    )

    home = tmp_path / "home"
    home.mkdir()
    bin_dir = tmp_path / "bin"
    _build_fake_entrypoint(bin_dir)
    log_path = bin_dir / "fake-args.jsonl"

    obligation = write_obligation(
        home,
        previous_version="1.0.0",
        originating_phase="upgrade-subprocess-gate",
    )
    update_obligation_expected_version(home, expected=obligation, expected_version="1.1.0")

    # The fake entrypoint does not write a real plugin cache; stub the
    # post-install generation verification so the e2e chain reaches the
    # obligation-clear step.
    gen_root = tmp_path / "generation-root"
    monkeypatch.setattr(
        "autoskillit.core.resolve_current_generation",
        lambda _home, _ref, _version: gen_root,
    )
    monkeypatch.setattr(
        "autoskillit.core.read_installed_plugin_artifact_identity",
        lambda _managed_path, **_kwargs: MagicMock(semantic_key="x"),
    )

    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(shutil, "which", lambda name, path=None: str(bin_dir / "autoskillit"))

    env = dict(os.environ)
    env["HOME"] = str(home)
    # The fake entrypoint is a Python script — keep the original PATH so
    # `env python3` (via the shebang) can find a working interpreter.
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    # Drop harness bytecode suppression so child behavior matches production.
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    env.pop("PYTHONPYCACHEPREFIX", None)
    # The harness sets CLAUDECODE=1 for child-spawning tests; the repair
    # helper defers in that case. Strip it so the e2e flow can run.
    env.pop("CLAUDECODE", None)

    result = attempt_obligation_repair(home, environment=env)

    assert result.outcome.value == "cleared", result
    assert read_obligation(home) is None
    # Recorded argv lines: probe (--version is intercepted, not recorded)
    # then the install call with both flags.
    recorded = log_path.read_text(encoding="utf-8").splitlines()
    assert len(recorded) == 1, recorded
    argv_words = recorded[0].split()
    assert "--maintenance-update" in argv_words
    assert "--expected-version" in argv_words
    idx = argv_words.index("--expected-version")
    assert argv_words[idx + 1] == "1.1.0"
