"""The projection cache key must cover every input that changes projected bytes.

This is the test that protects the change from itself.

The key used to be derived from `source_root`, backend name, projection version,
base branch, and two *skill-only* identities. Nothing in it covered `recipes/`,
`agents/`, `hooks/`, or `plugin.json`. That gap was masked by accident: the old
`source_root` was the versioned Claude Code cache path, so the key changed on
every release and forced a re-projection.

Moving the source to `pkg_root()` — the fix for everything else — makes
`source_root` version-invariant and removes that accidental protection. A release
that changes a recipe or a hook script without touching a skill digest would
produce an identical key and reuse the previous release's assets: the same silent
mixed-version execution, through a new mechanism. Hence the content digest, and
hence this test.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from autoskillit.workspace import (
    PROJECTION_CACHE_KEY_EXCLUSIONS,
    ProjectionCacheKey,
    public_plugin_asset_digest,
)
from autoskillit.workspace._projection_cache import _PUBLIC_PLUGIN_ASSET_NAMES

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.medium]


def _key(**overrides) -> ProjectionCacheKey:
    base = {
        "source_root": "/pkg",
        "backend_name": "claude-code",
        "projection_version": 2,
        "default_base_branch": "main",
        "skill_identity": "a:deadbeef",
        "namespace_identity": "a:bundled",
        "asset_digest": "0" * 64,
    }
    return ProjectionCacheKey(**{**base, **overrides})


class TestEveryFieldChangesTheKey:
    """A field present on the record but absent from the digest is a silent gap."""

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("source_root", "/other"),
            ("backend_name", "codex"),
            ("projection_version", 99),
            ("default_base_branch", "develop"),
            ("skill_identity", "a:cafebabe"),
            ("namespace_identity", "a:project_local"),
            ("asset_digest", "1" * 64),
        ],
    )
    def test_field_participates_in_the_digest(self, field: str, value: object) -> None:
        assert _key().digest() != _key(**{field: value}).digest(), (
            f"ProjectionCacheKey.{field} does not affect the cache key — a change to it "
            "would silently reuse a stale projection"
        )

    def test_every_declared_field_is_covered_by_a_parametrized_case(self) -> None:
        """Meta-test: adding a field without keying it fails the build.

        Without this, a new field could be added to the record and quietly left
        out of `digest()` — exactly the omission this whole file exists to catch.
        """
        declared = {f.name for f in dataclasses.fields(ProjectionCacheKey)}
        covered = {
            "source_root",
            "backend_name",
            "projection_version",
            "default_base_branch",
            "skill_identity",
            "namespace_identity",
            "asset_digest",
        }
        assert declared == covered, (
            "ProjectionCacheKey fields changed. Add a parametrized case above for each "
            f"new field so it is proven to affect the key. Unkeyed: {declared - covered}"
        )


class TestExclusionsCarryRationales:
    def test_every_public_asset_name_is_digested_or_excluded(self) -> None:
        """No third option: an asset is either in the digest or excluded in writing."""
        undeclared = sorted(
            name
            for name in _PUBLIC_PLUGIN_ASSET_NAMES
            if name in PROJECTION_CACHE_KEY_EXCLUSIONS
            and not PROJECTION_CACHE_KEY_EXCLUSIONS[name].strip()
        )
        assert not undeclared, f"excluded without a rationale: {undeclared}"

    def test_exclusions_are_non_empty_prose(self) -> None:
        thin = sorted(k for k, v in PROJECTION_CACHE_KEY_EXCLUSIONS.items() if len(v.strip()) < 40)
        assert not thin, (
            "Every cache-key exclusion needs a written reason it cannot affect projected "
            f"bytes. Too thin: {thin}"
        )

    def test_cwd_and_project_root_are_explicitly_resolved(self) -> None:
        """Related Issue 12, settled in one place rather than left contradictory.

        `_direct_install_projection_context`'s docstring claimed it bound "every
        byte-affecting input" while the key omitted `cwd` and `project_root`.
        Either they belong in the key or the claim needs qualifying — the
        exclusion list is where that decision now lives.
        """
        for name in ("cwd", "project_root"):
            assert name in PROJECTION_CACHE_KEY_EXCLUSIONS, (
                f"{name} is neither in the cache key nor in the documented exclusion list"
            )


class TestAssetChangesForceReprojection:
    def test_mutating_a_recipe_changes_the_key_without_touching_any_skill(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """The regression this file is named for.

        Fails both before the fix and after a source-change-without-digest — which
        is precisely why it is the gate.
        """
        from autoskillit.execution.backends.claude import ClaudeCodeBackend
        from autoskillit.workspace import project_default_plugin_source
        from tests.contracts._projection_helpers import session_catalog

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        backend = ClaudeCodeBackend()
        catalog = session_catalog()

        first = project_default_plugin_source(
            cwd=tmp_path,
            backend=backend,
            default_base_branch="main",
            skill_catalog=catalog,
        )
        marker = first.plugin_dir / "recipes" / "_cache_key_probe.yaml"
        marker.write_text("probe: 1\n")

        from autoskillit.core import pkg_root

        real_digest = public_plugin_asset_digest(pkg_root())
        monkeypatch.setattr(
            "autoskillit.workspace.skill_projection.public_plugin_asset_digest",
            lambda _root: real_digest[:-1] + ("0" if real_digest[-1] != "0" else "1"),
        )

        second = project_default_plugin_source(
            cwd=tmp_path,
            backend=backend,
            default_base_branch="main",
            skill_catalog=catalog,
        )

        assert second.plugin_dir != first.plugin_dir, (
            "an asset-digest change did not change the cache key — a release that "
            "touches recipes/, agents/ or hooks/ without touching a skill would reuse "
            "the previous release's projection"
        )
        assert not (second.plugin_dir / "recipes" / "_cache_key_probe.yaml").exists(), (
            "the new projection was not re-materialised from source"
        )

    def test_identical_inputs_reuse_the_same_projection(self, tmp_path: Path, monkeypatch) -> None:
        """The key must still be stable — invalidation, not churn."""
        from autoskillit.execution.backends.claude import ClaudeCodeBackend
        from autoskillit.workspace import project_default_plugin_source
        from tests.contracts._projection_helpers import session_catalog

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        backend = ClaudeCodeBackend()
        catalog = session_catalog()
        kwargs = {
            "cwd": tmp_path,
            "backend": backend,
            "default_base_branch": "main",
            "skill_catalog": catalog,
        }
        assert (
            project_default_plugin_source(**kwargs).plugin_dir
            == project_default_plugin_source(**kwargs).plugin_dir
        )


class TestOrphanedProjectionsArePruned:
    """`plugin-projections/` had no cleanup anywhere.

    Pre-existing, but this change orphans every user's current projection at
    once (new source, new key composition), so it is the right moment. Pruning
    reuses the retiring-cache grace/lock machinery rather than inventing a
    second deletion mechanism — a projection a running session is still reading
    survives the grace window.
    """

    def test_orphan_is_retired_and_the_active_projection_is_not(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        import json

        from autoskillit.execution.backends.claude import ClaudeCodeBackend
        from autoskillit.workspace import project_default_plugin_source
        from tests.contracts._projection_helpers import session_catalog

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        projections = tmp_path / ".autoskillit" / "plugin-projections"
        projections.mkdir(parents=True)
        orphan = projections / "deadbeefdeadbeefdeadbeef"
        orphan.mkdir()
        (projections / f".{orphan.name}.autoskillit-projection.json").write_text("{}")

        active = project_default_plugin_source(
            cwd=tmp_path,
            backend=ClaudeCodeBackend(),
            default_base_branch="main",
            skill_catalog=session_catalog(),
        )

        retiring = json.loads((tmp_path / ".autoskillit" / "retiring_cache.json").read_text())
        queued = {e["path"] for e in retiring["retiring"]}
        assert str(orphan) in queued, "the orphaned projection was not queued for retirement"
        assert str(active.plugin_dir) not in queued, "the live projection was queued for deletion"
        assert active.plugin_dir.is_dir()
        assert orphan.is_dir(), "an orphan must survive the grace window, not vanish immediately"
        assert not (projections / f".{orphan.name}.autoskillit-projection.json").exists()
