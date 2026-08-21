"""Staging reads the sealed install root, never a live lookup (T-B8, issue #4597).

No dedicated test file for ``_stage_projected_plugin_artifact()`` exists —
it is exercised today from ``tests/workspace/test_agent_definition_rendering.py``
(via ``inspect.getsource`` plus a call-graph assertion) and
``tests/contracts/test_plugin_artifact_lifetime.py`` (direct invocation via
full ``acquire_launch_binding()`` flows). This file adds the one behavioural
case those don't cover: staging must not re-derive its source root partway
through, which is the actual shape of the TOCTOU window noted in the
rectify plan for issue #4597 (finding #6) — the freshness probe runs once,
before staging, and everything staging reads afterward must come from what
the probe already validated, not a second, later resolution.

Verified safe to poison ``resolve_install_binding`` for the duration of a
real staging call: ``hook_registry.py``'s only ``pkg_root()`` call
(``HOOKS_DIR = pkg_root() / "hooks"``) is at module scope, already evaluated
at import time, and no staging helper (``materialization.py``,
``render_hooks_json_text()``) calls ``pkg_root()``/``resolve_install_binding()``
itself.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

import autoskillit.workspace._projected_artifact.authority as authority
from autoskillit.execution.backends.claude import ClaudeCodeBackend
from autoskillit.workspace import project_default_plugin_authority
from tests.contracts._projection_helpers import session_catalog

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.medium]


def test_staging_reads_the_sealed_root_not_a_live_lookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Poisoning the install-binding resolver mid-stage must not be observed.

    The plan (and hence its immutable ``source_root`` field — ``_ProjectedArtifactPlan``
    is ``frozen=True``) is built before staging begins, from the authority's
    already-resolved ``direct_install.plugin_dir`` — never re-derived by
    ``_stage_projected_plugin_artifact()`` itself. Poisoning
    ``resolve_install_binding`` (imported into this module, the name any
    call inside staging would actually resolve) to raise, then successfully
    staging real content from the plan proves this module does not perform a
    second resolution. Helpers imported from other modules bind their own
    names and are outside this monkeypatch's claim.
    """
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    plugin_authority = project_default_plugin_authority(
        cwd=tmp_path,
        base_branch="main",
        catalog=session_catalog(),
    )
    plan = plugin_authority._plan(ClaudeCodeBackend())
    plan.destination.parent.mkdir(parents=True, exist_ok=True)

    def _poisoned() -> object:
        raise AssertionError(
            "staging called resolve_install_binding() instead of using plan.source_root"
        )

    monkeypatch.setattr(authority, "resolve_install_binding", _poisoned)

    staged = None
    try:
        staged = authority._stage_projected_plugin_artifact(plan)
        assert staged.root.is_dir()
    finally:
        if staged is not None:
            shutil.rmtree(staged.root, ignore_errors=True)
            staged.manifest.unlink(missing_ok=True)
