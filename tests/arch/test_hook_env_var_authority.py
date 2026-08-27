"""Architectural guard: hooks reading PROVIDER_PROFILE must also read AGENT_BACKEND.

Provider profile (``AUTOSKILLIT_PROVIDER_PROFILE``) is a credentials/model
label — it answers "which API endpoint and model configuration?" — NOT a
backend-identity signal ("which backend CLI is executing?"). Using profile
as the sole authority for backend-specific decisions causes false
denials/bypasses (e.g., Codex-backend steps that carry the default
``"anthropic"`` credentials bundle are denied based on Anthropic quota
they cannot consume).

Each hook script that reads ``AUTOSKILLIT_PROVIDER_PROFILE`` MUST also
read ``AUTOSKILLIT_AGENT_BACKEND`` so backend identity can be checked
independently of provider routing.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests._helpers import _EnvVarReadCollector
from tests.arch._helpers import SRC_ROOT

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_PROVIDER_PROFILE_VAR = "AUTOSKILLIT_PROVIDER_PROFILE"
_AGENT_BACKEND_VAR = "AUTOSKILLIT_AGENT_BACKEND"


def _collect_reads(py_file: Path) -> set[str]:
    try:
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
    except SyntaxError:
        return set()
    collector = _EnvVarReadCollector()
    collector.visit(tree)
    return collector.reads


def _iter_hook_files() -> list[Path]:
    """Recursively enumerate hook scripts, skipping package markers and shared modules.

    Files whose basename starts with ``_`` (e.g., ``_hook_settings.py``,
    ``_hook_utils.py``) are shared stdlib-only modules imported by hook
    scripts; their env-var usage is delegated from the executable hooks
    themselves and is therefore covered transitively.
    """
    hooks_root = SRC_ROOT / "hooks"
    files: list[Path] = []
    for py_file in sorted(hooks_root.rglob("*.py")):
        if py_file.name.startswith("_"):
            continue
        files.append(py_file)
    return files


def test_hooks_with_provider_profile_must_also_read_agent_backend() -> None:
    """Every executable hook that reads PROVIDER_PROFILE must read AGENT_BACKEND.

    This is a structural guard: a hook that already reads
    ``AUTOSKILLIT_PROVIDER_PROFILE`` has a working env-var lookup pattern,
    so adding ``AUTOSKILLIT_AGENT_BACKEND`` is mechanically trivial and
    enforced. Hooks that don't read either variable inherit the implicit
    behavior of treating the host as a default Claude backend — they are
    not subject to this guard.
    """
    violations: list[str] = []
    for py_file in _iter_hook_files():
        reads = _collect_reads(py_file)
        if _PROVIDER_PROFILE_VAR in reads and _AGENT_BACKEND_VAR not in reads:
            relpath = py_file.relative_to(SRC_ROOT)
            violations.append(f"{relpath}: reads PROVIDER_PROFILE without AGENT_BACKEND")

    assert not violations, (
        "Hook reads AUTOSKILLIT_PROVIDER_PROFILE without also reading "
        "AUTOSKILLIT_AGENT_BACKEND. Provider profile is a credentials "
        "label, not a backend-identity signal. Check both to avoid "
        "profile-as-identity confusion.\n" + "\n".join(f"  {v}" for v in violations)
    )
