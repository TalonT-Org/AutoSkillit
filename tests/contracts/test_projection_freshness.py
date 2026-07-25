"""The projection must always match the running package. Never a snapshot.

This is the most important test in the change, because it is the only one that
would have caught the *silent* failure.

While the plugin source was read from `installed_plugins.json`, `cook` resolved
`skills/` fresh from the live package but copied `recipes/`, `agents/`, `hooks/`,
`.mcp.json`, and `plugin.json` from an eleven-versions-old cache snapshot. Every
session ran 0.10.883 recipes and hook scripts against 0.10.894 code. No error, no
warning — the crash that finally surfaced was the *benign* symptom, fixed by hand
in minutes, while the staleness had been live for days.

`pkg_root()` is the only source that cannot be stale, because it is the code
currently executing. These tests assert that property at every entrypoint, and
then adversarially plant a stale snapshot to prove nothing reads it.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import autoskillit
from autoskillit.core import ProjectedPluginRoot, pkg_root
from autoskillit.workspace import (
    iter_public_plugin_asset_files,
    public_plugin_asset_digest,
)
from tests.contracts._projection_helpers import plant_stale_snapshot, session_catalog

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.medium]


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _assert_projection_is_live(projected: ProjectedPluginRoot) -> None:
    """Every projected asset must byte-match the running package."""
    root = projected.plugin_dir
    assert root.is_dir()

    plugin_json = root / ".claude-plugin" / "plugin.json"
    assert plugin_json.is_file(), "projection is missing .claude-plugin/plugin.json"
    assert json.loads(plugin_json.read_text())["version"] == autoskillit.__version__, (
        "projected plugin.json reports a version other than the running package — "
        "the projection came from a snapshot"
    )

    mismatched: list[str] = []
    missing: list[str] = []
    for source_file in iter_public_plugin_asset_files(pkg_root()):
        rel = source_file.relative_to(pkg_root())
        projected_file = root / rel
        if not projected_file.is_file():
            missing.append(str(rel))
        elif _digest(projected_file) != _digest(source_file):
            mismatched.append(str(rel))
    assert not missing, f"projection is missing live package assets: {missing[:10]}"
    assert not mismatched, f"projected assets differ from the live package: {mismatched[:10]}"

    assert not (root / "STALE.md").exists()
    for name in ("recipes", "agents", "hooks"):
        assert not (root / name / "STALE.md").exists(), (
            f"{name}/ was copied from the planted stale snapshot"
        )


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return tmp_path


class TestProjectionFreshness:
    """Every entrypoint must project the live package, never a snapshot."""

    def test_project_default_plugin_source_matches_live_package(self, isolated_home: Path) -> None:
        from autoskillit.execution.backends.claude import ClaudeCodeBackend
        from autoskillit.workspace import project_default_plugin_source

        plant_stale_snapshot(isolated_home)
        projected = project_default_plugin_source(
            cwd=isolated_home,
            backend=ClaudeCodeBackend(),
            default_base_branch="main",
            skill_catalog=session_catalog(),
        )
        _assert_projection_is_live(projected)

    def test_prepare_catalog_skill_dispatch_matches_live_package(
        self, isolated_home: Path
    ) -> None:
        from autoskillit.execution.backends.claude import ClaudeCodeBackend
        from autoskillit.workspace import prepare_catalog_skill_dispatch

        plant_stale_snapshot(isolated_home)
        projected, _contract = prepare_catalog_skill_dispatch(
            resolved_command="/investigate",
            cwd=isolated_home,
            backend=ClaudeCodeBackend(),
            catalog=session_catalog(),
            default_base_branch="main",
        )
        _assert_projection_is_live(projected)

    def test_make_context_matches_live_package(self, isolated_home: Path) -> None:
        from autoskillit.config import AutomationConfig
        from autoskillit.server._factory import make_context

        plant_stale_snapshot(isolated_home)
        ctx = make_context(AutomationConfig(), runner=None, project_dir=isolated_home)
        _assert_projection_is_live(ctx.plugin_source)

    def test_projection_never_exposes_the_canonical_root(self, isolated_home: Path) -> None:
        from autoskillit.execution.backends.claude import ClaudeCodeBackend
        from autoskillit.workspace import project_default_plugin_source

        projected = project_default_plugin_source(
            cwd=isolated_home,
            backend=ClaudeCodeBackend(),
            default_base_branch="main",
            skill_catalog=session_catalog(),
        )
        assert projected.plugin_dir != pkg_root()
        assert projected.plugin_dir.is_relative_to(
            isolated_home / ".autoskillit" / "plugin-projections"
        )


class TestAssetDigestMirrorsTheCopier:
    """The cache-key digest and the copier must agree on what a projection is.

    If they drift, the key stops covering bytes that actually get copied — which
    is the same defect in a new place.
    """

    def test_digest_walk_covers_exactly_the_copied_files(self, isolated_home: Path) -> None:
        from autoskillit.execution.backends.claude import ClaudeCodeBackend
        from autoskillit.workspace import project_default_plugin_source

        projected = project_default_plugin_source(
            cwd=isolated_home,
            backend=ClaudeCodeBackend(),
            default_base_branch="main",
            skill_catalog=session_catalog(),
        )
        walked = {
            str(p.relative_to(pkg_root())) for p in iter_public_plugin_asset_files(pkg_root())
        }
        root = projected.plugin_dir
        copied = {
            str(p.relative_to(root))
            for p in root.rglob("*")
            if p.is_file() and not str(p.relative_to(root)).startswith("skills/")
        }
        # hooks/hooks.json is regenerated post-projection by install(), and the
        # skills/ tree is projected from contracts rather than copied.
        assert walked == copied, (
            "the cache-key digest walk and the projection copier disagree:\n"
            f"  only walked: {sorted(walked - copied)[:10]}\n"
            f"  only copied: {sorted(copied - walked)[:10]}"
        )

    def test_digest_changes_when_an_asset_changes(self, tmp_path: Path) -> None:
        source = tmp_path / "src"
        (source / "recipes").mkdir(parents=True)
        (source / "recipes" / "a.yaml").write_text("one\n")

        before = public_plugin_asset_digest(source)
        (source / "recipes" / "a.yaml").write_text("two\n")
        assert public_plugin_asset_digest(source) != before

    def test_digest_ignores_files_the_projection_never_copies(self, tmp_path: Path) -> None:
        source = tmp_path / "src"
        (source / "recipes").mkdir(parents=True)
        (source / "recipes" / "a.yaml").write_text("one\n")
        (source / "not_public").mkdir()

        before = public_plugin_asset_digest(source)
        (source / "not_public" / "x.txt").write_text("irrelevant\n")
        assert public_plugin_asset_digest(source) == before
