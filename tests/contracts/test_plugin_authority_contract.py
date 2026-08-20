"""Shared behavioral contract: every implementation refuses identically (T-B5a).

Sibling to ``test_plugin_artifact_lifetime.py`` (``ProjectedPluginArtifactAuthority``-
only): this file is the shared cross-implementation contract, parametrized over
all three ``PluginArtifactAuthority`` implementations —
``ProjectedPluginArtifactAuthority``, ``InstalledPluginArtifactAuthority``, and
``FakePluginArtifactAuthority`` — and, separately, both
``ManagedHeadlessSessionLineageStore`` implementations —
``DefaultManagedHeadlessSessionLineageStore`` and
``FakeManagedHeadlessSessionLineageStore``.

Strictly stronger than asserting a fake invokes its precondition: an
invocation assertion is satisfied by a call whose result is discarded; a
behavioural contract run against every implementation under the identical
fault is not. The two conditions covered here — a deleted generator root, an
anchor directory replaced at the same path with a new inode — are both
cheaply triggerable, which is what makes them suitable for a contract test
rather than the harder-to-provoke error states noted as out of scope in
``tests/arch/_rules.py``'s ARCH-012 residual-coverage note.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast
from unittest.mock import Mock

import pytest

from autoskillit.cli._plugin_artifact import InstalledPluginArtifactAuthority
from autoskillit.core import (
    ManagedHeadlessSessionKind,
    NativeShellCaptureMode,
    PluginLoadMode,
    StaleGeneratorError,
    resolve_native_shell_capture_decision,
)
from autoskillit.execution.session._managed_headless_session_lineage import (
    DefaultManagedHeadlessSessionLineageStore,
)
from autoskillit.workspace import project_default_plugin_authority
from tests._helpers import replace_directory_preserving_children
from tests.contracts._projection_helpers import session_catalog
from tests.fakes import FakeManagedHeadlessSessionLineageStore, FakePluginArtifactAuthority

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.medium]


def _projected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Any, PluginLoadMode]:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    authority = project_default_plugin_authority(
        cwd=tmp_path,
        base_branch="main",
        catalog=session_catalog(),
    )
    return authority, cast(Any, None), PluginLoadMode.EXPLICIT_PLUGIN_DIR


def _installed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Any, PluginLoadMode]:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    version = "1.2.3"
    root = tmp_path / "fake-root-parent" / version

    # A current generation must exist, or acquire_launch_binding takes the
    # no-generation-store legacy fallback, which never runs the probe.
    gen_version_root = tmp_path / ".autoskillit" / "plugin-generations" / "autoskillit" / version
    current_generation = gen_version_root / "current-incarnation"
    current_generation.mkdir(parents=True)
    (gen_version_root / "current").symlink_to(current_generation)

    authority = InstalledPluginArtifactAuthority(root, semantic_key="contract-semantic-key")
    # Self-heal republish is exercised elsewhere (test_installed_authority_also_
    # refuses_stale_generator); mocked here so this shared contract exercises
    # only the freshness probe every implementation shares, not the unrelated
    # republish machinery that only InstalledPluginArtifactAuthority has.
    monkeypatch.setattr(authority, "_self_heal_republish", Mock(return_value=None))
    return authority, cast(Any, None), PluginLoadMode.EXPLICIT_PLUGIN_DIR


def _fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Any, PluginLoadMode]:
    del monkeypatch
    authority = FakePluginArtifactAuthority(tmp_path)
    return authority, cast(Any, object()), PluginLoadMode.EXPLICIT_PLUGIN_DIR


_PLUGIN_AUTHORITY_FACTORIES = {
    "projected": _projected,
    "installed": _installed,
    "fake": _fake,
}


@pytest.mark.parametrize("kind", sorted(_PLUGIN_AUTHORITY_FACTORIES))
def test_every_plugin_authority_refuses_a_deleted_generator_root(
    kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A deleted generator install root refuses launch identically across all three."""
    import autoskillit.workspace._projected_artifact.authority as _auth

    monkeypatch.setattr(_auth, "pkg_root", lambda: tmp_path / "nonexistent")
    authority, backend, load_mode = _PLUGIN_AUTHORITY_FACTORIES[kind](tmp_path, monkeypatch)

    with pytest.raises(StaleGeneratorError, match="no longer exists"):
        authority.acquire_launch_binding(backend=backend, load_mode=load_mode)


_LINEAGE_STORE_FACTORIES = {
    "default": DefaultManagedHeadlessSessionLineageStore,
    "fake": FakeManagedHeadlessSessionLineageStore,
}


@pytest.mark.parametrize("kind", sorted(_LINEAGE_STORE_FACTORIES))
def test_every_lineage_store_refuses_an_anchor_replaced_at_the_same_path(
    kind: str, tmp_path: Path
) -> None:
    """An anchor directory replaced at the same path (new inode) refuses
    identically across ``DefaultManagedHeadlessSessionLineageStore`` and
    ``FakeManagedHeadlessSessionLineageStore``.

    Uses ``replace_directory_preserving_children`` rather than a plain
    ``rmtree`` + ``mkdir`` — the real store persists its records *inside*
    the anchor directory (``anchor/.autoskillit/managed-headless-session-
    lineage/``), so a bare delete-and-recreate would destroy the very
    record this test needs to still exist, and the failure would be a
    ``FileNotFoundError`` from ``_read_record`` rather than the anchor-
    identity check this test targets.
    """
    store = _LINEAGE_STORE_FACTORIES[kind]()
    anchor = tmp_path / "anchor"
    anchor.mkdir()
    decision = resolve_native_shell_capture_decision(NativeShellCaptureMode.DIRECT)
    store.create(
        lineage_anchor=anchor,
        launch_id="a" * 32,
        decision=decision,
        backend="claude-code",
        session_kind=ManagedHeadlessSessionKind.SKILL,
    )

    replace_directory_preserving_children(anchor)

    with pytest.raises(ValueError, match="Managed lineage anchor identity mismatch"):
        store.load(lineage_anchor=anchor, launch_id="a" * 32)
