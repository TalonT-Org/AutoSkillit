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
from autoskillit.core import pkg_root
from autoskillit.workspace import (
    iter_public_plugin_asset_files,
    public_plugin_asset_digest,
)
from tests.contracts._projection_helpers import plant_stale_snapshot, session_catalog

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.medium]


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _assert_projection_is_live(root: Path) -> None:
    """Every projected asset must byte-match the running package."""
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

    def test_project_default_plugin_authority_matches_live_package(
        self, isolated_home: Path
    ) -> None:
        from autoskillit.core import PluginLoadMode
        from autoskillit.execution.backends.claude import ClaudeCodeBackend
        from autoskillit.workspace import project_default_plugin_authority

        plant_stale_snapshot(isolated_home)
        authority = project_default_plugin_authority(
            cwd=isolated_home,
            base_branch="main",
            catalog=session_catalog(),
        )
        with authority.acquire_launch_binding(
            backend=ClaudeCodeBackend(),
            load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
        ) as binding:
            assert binding.plugin_dir is not None
            _assert_projection_is_live(binding.plugin_dir)
        assert binding.closed

    def test_prepare_catalog_skill_projection_matches_live_package(
        self, isolated_home: Path
    ) -> None:
        from autoskillit.core import PluginLoadMode
        from autoskillit.execution.backends.claude import ClaudeCodeBackend
        from autoskillit.workspace import prepare_catalog_skill_projection

        plant_stale_snapshot(isolated_home)
        authority, preparation = prepare_catalog_skill_projection(
            cwd=isolated_home,
            catalog=session_catalog(),
            default_base_branch="main",
        )
        backend = ClaudeCodeBackend()
        with authority.acquire_launch_binding(
            backend=backend,
            load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
        ) as binding:
            assert binding.plugin_dir is not None
            _assert_projection_is_live(binding.plugin_dir)
            contract = preparation.finalize(
                backend=backend,
                binding=binding,
            )
            assert contract.artifact_paths == (str(binding.plugin_dir),)
        assert binding.closed

    def test_make_context_matches_live_package(self, isolated_home: Path) -> None:
        from autoskillit.config import AutomationConfig
        from autoskillit.core import PluginLoadMode
        from autoskillit.execution.backends.claude import ClaudeCodeBackend
        from autoskillit.server._factory import make_context

        plant_stale_snapshot(isolated_home)
        ctx = make_context(AutomationConfig(), runner=None, project_dir=isolated_home)
        with ctx.plugin_authority.acquire_launch_binding(
            backend=ClaudeCodeBackend(),
            load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
        ) as binding:
            assert binding.plugin_dir is not None
            _assert_projection_is_live(binding.plugin_dir)
        assert binding.closed

    def test_projection_never_exposes_the_canonical_root(self, isolated_home: Path) -> None:
        from autoskillit.core import PluginLoadMode
        from autoskillit.execution.backends.claude import ClaudeCodeBackend
        from autoskillit.workspace import project_default_plugin_authority

        authority = project_default_plugin_authority(
            cwd=isolated_home,
            base_branch="main",
            catalog=session_catalog(),
        )
        with authority.acquire_launch_binding(
            backend=ClaudeCodeBackend(),
            load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
        ) as binding:
            assert binding.plugin_dir is not None
            assert binding.plugin_dir != pkg_root()
            assert binding.plugin_dir.is_relative_to(
                isolated_home / ".autoskillit" / "plugin-projections"
            )
        assert binding.closed


class TestAssetDigestMirrorsTheCopier:
    """The cache-key digest and the copier must agree on what a projection is.

    If they drift, the key stops covering bytes that actually get copied — which
    is the same defect in a new place.
    """

    def test_digest_walk_covers_exactly_the_copied_files(self, isolated_home: Path) -> None:
        from autoskillit.core import PluginLoadMode
        from autoskillit.execution.backends.claude import ClaudeCodeBackend
        from autoskillit.workspace import project_default_plugin_authority

        authority = project_default_plugin_authority(
            cwd=isolated_home,
            base_branch="main",
            catalog=session_catalog(),
        )
        walked = {
            str(p.relative_to(pkg_root())) for p in iter_public_plugin_asset_files(pkg_root())
        }
        with authority.acquire_launch_binding(
            backend=ClaudeCodeBackend(),
            load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
        ) as binding:
            assert binding.plugin_dir is not None
            root = binding.plugin_dir
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
        assert binding.closed

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


class TestBytecodeExclusion:
    """Bytecode (``__pycache__/``, ``*.pyc``, ``*.pyo``) must never enter a
    published artifact — exclusion is enforced by ``is_projected_asset``
    at every depth, and the digest/copier stay in lockstep through it.
    """

    @staticmethod
    def _seed_bytecode(source: Path) -> None:
        """Seed a source tree with bytecode at multiple depths."""
        hooks = source / "hooks"
        hooks.mkdir(parents=True, exist_ok=True)
        (hooks / "real.py").write_text("pass")
        guards = hooks / "guards"
        guards.mkdir(parents=True, exist_ok=True)
        (guards / "guard.py").write_text("pass")

        # Bytecode at hooks/ level
        pc1 = hooks / "__pycache__"
        pc1.mkdir(exist_ok=True)
        (pc1 / "real.cpython-311.pyc").write_bytes(b"fake pyc")

        # Bytecode at hooks/guards/ level
        pc2 = guards / "__pycache__"
        pc2.mkdir(exist_ok=True)
        (pc2 / "guard.cpython-311.pyc").write_bytes(b"fake pyc")

        # .pyo file at hooks/ level
        (hooks / "stale.pyo").write_bytes(b"fake pyo")

        # Bytecode under scripts/
        scripts = source / "scripts"
        scripts.mkdir(parents=True, exist_ok=True)
        (scripts / "helper.py").write_text("pass")
        pc3 = scripts / "__pycache__"
        pc3.mkdir(exist_ok=True)
        (pc3 / "helper.cpython-311.pyc").write_bytes(b"fake pyc")

    def test_iter_walk_excludes_bytecode(self, tmp_path: Path) -> None:
        source = tmp_path / "src"
        source.mkdir()
        self._seed_bytecode(source)

        walked = set(iter_public_plugin_asset_files(source))
        bytecode = {p for p in walked if "__pycache__" in p.parts or p.suffix in {".pyc", ".pyo"}}
        assert not bytecode, f"iter_public_plugin_asset_files yielded bytecode entries: {bytecode}"

    def test_digest_ignores_bytecode(self, tmp_path: Path) -> None:
        source = tmp_path / "src"
        source.mkdir()
        self._seed_bytecode(source)

        d_with_bytecode = public_plugin_asset_digest(source)

        # Remove all bytecode
        import shutil

        for pc in list(source.rglob("__pycache__")):
            shutil.rmtree(pc)
        for pyc in list(source.rglob("*.pyo")):
            pyc.unlink()

        d_clean = public_plugin_asset_digest(source)
        assert d_with_bytecode == d_clean, (
            "digest changed when bytecode was removed — bytecode should be "
            "excluded from the digest by is_projected_asset"
        )
