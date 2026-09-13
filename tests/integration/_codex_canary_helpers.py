"""Shared installed-Codex binary selection for opt-in real-loader canaries.

Both the managed-skill discovery canary
(``tests/integration/test_codex_skill_discovery_canary.py``, gated on
``AUTOSKILLIT_CODEX_DISCOVERY_CANARY``) and the startup/session-lease canary
(``tests/integration/test_codex_startup_canary.py``, gated on
``AUTOSKILLIT_CODEX_STARTUP_CANARY``) need to select and version-validate an
installed Codex CLI binary the same way: resolved from an operator-supplied
absolute path or PATH, then checked against the backend's supported minimum
and an optional operator-pinned expected version. Only the enable-gate env
var differs between canaries -- ``select_canary_codex`` takes that as a
parameter and everything else is shared here.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest
from packaging.version import Version

from autoskillit.core import normalize_codex_cli_version
from autoskillit.execution.backends._codex_discovery import probe_codex_version
from autoskillit.execution.backends.codex import CodexBackend

#: Shared across every canary: an operator-selected absolute Codex binary,
#: and the normalized version it is expected to resolve to.
BINARY_ENV = "AUTOSKILLIT_CODEX_CANARY_BINARY"
EXPECTED_VERSION_ENV = "AUTOSKILLIT_CODEX_CANARY_EXPECTED_VERSION"
PROBE_TIMEOUT_SECONDS = 30


@dataclass(frozen=True, slots=True)
class SelectedCodex:
    binary: Path
    raw_version: str
    normalized_version: str


def select_canary_codex(*, canary_env: str) -> SelectedCodex:
    """Select and version-validate an installed Codex binary for one canary.

    ``canary_env`` is the canary-specific enable-gate env var (e.g.
    ``AUTOSKILLIT_CODEX_DISCOVERY_CANARY`` or
    ``AUTOSKILLIT_CODEX_STARTUP_CANARY``). Skips when that gate is unset or
    the platform lacks POSIX managed-home symlink support; fails (not
    skips) when the gate is explicitly set but the requested binary is
    missing, non-executable, unprobeable, below the backend's minimum
    supported version, or does not match an operator-pinned expected
    version.
    """
    if os.environ.get(canary_env) != "1":
        pytest.skip(f"set {canary_env}=1 to run this Codex canary")
    if os.name != "posix":
        pytest.skip("Codex canary requires POSIX managed-home symlinks")

    requested = os.environ.get(BINARY_ENV, "")
    if requested:
        binary = Path(requested)
        if not binary.is_absolute():
            pytest.fail(f"{BINARY_ENV} must be an absolute executable path: {requested!r}")
    else:
        resolved = shutil.which("codex")
        if resolved is None:
            pytest.fail("Codex canary requested but the Codex CLI is not present")
        binary = Path(resolved).resolve()

    if not binary.is_file() or not os.access(binary, os.X_OK):
        pytest.fail(f"Codex canary requested with a missing or non-executable binary: {binary}")

    raw_version, normalized_version, errors = probe_codex_version(
        executable=str(binary),
        env=os.environ,
        cwd=str(Path.cwd()),
        timeout_seconds=PROBE_TIMEOUT_SECONDS,
    )
    if errors:
        pytest.fail("; ".join(errors))
    assert raw_version
    assert normalized_version

    minimum_version = CodexBackend().capabilities.min_version
    if Version(normalized_version) < Version(minimum_version):
        pytest.fail(
            "Codex canary requested with an unsupported version: "
            f"raw={raw_version!r}; normalized={normalized_version!r}; "
            f"minimum={minimum_version!r}"
        )
    expected = os.environ.get(EXPECTED_VERSION_ENV, "")
    if expected:
        try:
            expected_normalized = normalize_codex_cli_version(expected)
        except ValueError as exc:
            pytest.fail(f"{EXPECTED_VERSION_ENV} is not a normalized Codex version: {exc}")
        if normalized_version != expected_normalized:
            pytest.fail(
                "Codex canary selected the wrong Codex version: "
                f"raw={raw_version!r}; normalized={normalized_version!r}; "
                f"expected={expected_normalized!r}"
            )
    return SelectedCodex(binary, raw_version, normalized_version)
