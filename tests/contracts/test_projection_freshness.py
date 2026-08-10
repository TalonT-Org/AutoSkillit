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
import subprocess
import sys
from pathlib import Path

import pytest

import autoskillit
from autoskillit.core import pkg_root
from autoskillit.workspace import (
    iter_public_plugin_asset_files,
    public_plugin_asset_digest,
)
from tests.conftest import production_interpreter_env
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

    def test_projected_codex_formatter_resolves_runtime_imports(self, isolated_home: Path) -> None:
        from autoskillit.core import PluginLoadMode
        from autoskillit.execution.backends.codex import CodexBackend
        from autoskillit.workspace import project_default_plugin_authority

        authority = project_default_plugin_authority(
            cwd=isolated_home,
            base_branch="main",
            catalog=session_catalog(),
        )
        with authority.acquire_launch_binding(
            backend=CodexBackend(),
            load_mode=PluginLoadMode.PROJECTED_HOME,
        ) as binding:
            assert binding.plugin_dir is not None
            assert binding.plugin_dir != pkg_root()
            assert binding.plugin_dir.is_relative_to(
                isolated_home / ".autoskillit" / "plugin-projections"
            )

            subprocess_cwd = isolated_home / "projected-hook-cwd"
            subprocess_cwd.mkdir()
            dispatcher = binding.plugin_dir / "hooks" / "_dispatch.py"
            command = [
                sys.executable,
                "-B",
                str(dispatcher),
                "formatters/pretty_output_hook",
            ]
            input_text = "Kitchen is open. AutoSkillit 1.2.3."
            event = json.dumps(
                {
                    "tool_name": "mcp__autoskillit__open_kitchen",
                    "tool_response": json.dumps({"result": input_text}),
                }
            )
            env = production_interpreter_env()
            env.pop("PYTHONPATH", None)

            result = subprocess.run(
                command,
                input=event,
                text=True,
                capture_output=True,
                cwd=subprocess_cwd,
                env=env,
                timeout=10,
            )

            assert result.returncode == 0, (
                f"projected formatter command failed: {command!r}\n{result.stderr}"
            )
            assert result.stdout, (
                f"projected formatter command emitted no output: {command!r}\n{result.stderr}"
            )
            diagnostics = (
                f"command: {command!r}\n"
                f"stdout: {result.stdout[:2000]!r}\n"
                f"stderr: {result.stderr[:2000]!r}"
            )
            try:
                output = json.loads(result.stdout)
            except json.JSONDecodeError as exc:
                pytest.fail(
                    f"projected formatter emitted invalid JSON ({exc}):\n{diagnostics}",
                    pytrace=False,
                )
            updated_output = output["hookSpecificOutput"]["updatedMCPToolOutput"]
            assert "## open_kitchen" in updated_output, diagnostics
            assert input_text in updated_output, diagnostics
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
    """The copier and the digest vocabulary must never disagree about bytecode.

    ``is_projected_asset`` excludes ``__pycache__`` directories and
    ``.pyc``/``.pyo`` files at every depth — see ``_projection_cache.py``.
    These tests exercise that exclusion at the call sites that actually
    matter: the file copier (``_copy_non_skill_plugin_assets``, which writes
    what a session executes), the digest vocabulary walk
    (``iter_public_plugin_asset_files``, which keys the projection cache),
    and the cache-key digest itself (``public_plugin_asset_digest``). If any
    of these drift apart, one silently ships or hashes bytes another believes
    were excluded — the same class of defect this module's docstring
    describes for stale snapshots, in a new place.
    """

    @staticmethod
    def _seed_source_with_bytecode(source: Path) -> None:
        (source / "hooks" / "guards").mkdir(parents=True)
        (source / "hooks" / "guard_one.py").write_text("real hook\n")
        (source / "hooks" / "guards" / "guard_two.py").write_text("real hook\n")
        (source / "hooks" / "__pycache__").mkdir()
        (source / "hooks" / "__pycache__" / "quota_guard.cpython-311.pyc").write_bytes(b"fake pyc")
        (source / "hooks" / "guards" / "__pycache__").mkdir()
        (source / "hooks" / "guards" / "__pycache__" / "quota_guard.cpython-311.pyc").write_bytes(
            b"fake pyc"
        )
        (source / "hooks" / "stale.pyc").write_bytes(b"standalone fake pyc")
        (source / "hooks" / "stale.pyo").write_bytes(b"fake pyo")
        (source / "scripts" / "__pycache__").mkdir(parents=True)
        (source / "scripts" / "module.py").write_text("real script\n")
        (source / "scripts" / "__pycache__" / "module.cpython-311.pyc").write_bytes(b"fake pyc")

    def test_copier_excludes_bytecode_at_every_depth(self, tmp_path: Path) -> None:
        from autoskillit.workspace._projected_artifact.materialization import (
            _copy_non_skill_plugin_assets,
        )
        from autoskillit.workspace._projection_cache import is_projected_asset

        source = tmp_path / "source"
        source.mkdir()
        self._seed_source_with_bytecode(source)
        destination = tmp_path / "destination"
        destination.mkdir()

        _copy_non_skill_plugin_assets(source, destination)

        copied_pycache_dirs = list(destination.rglob("__pycache__"))
        copied_bytecode_files = [
            p for p in destination.rglob("*") if p.is_file() and p.suffix in (".pyc", ".pyo")
        ]
        assert not copied_pycache_dirs, f"copier published __pycache__ dirs: {copied_pycache_dirs}"
        assert not copied_bytecode_files, (
            f"copier published bytecode files: {copied_bytecode_files}"
        )

        # The predicate behind the copier agrees, at the exact seeded sites.
        assert not is_projected_asset(source / "hooks" / "__pycache__", top_level=False)
        assert not is_projected_asset(
            source / "hooks" / "__pycache__" / "quota_guard.cpython-311.pyc", top_level=False
        )
        assert not is_projected_asset(source / "hooks" / "stale.pyc", top_level=False)
        assert not is_projected_asset(source / "hooks" / "stale.pyo", top_level=False)

        # Sanity: the copier did real work, so the empty assertions above
        # aren't trivially true because nothing was copied at all.
        assert (destination / "hooks" / "guard_one.py").is_file()
        assert (destination / "hooks" / "guards" / "guard_two.py").is_file()
        assert (destination / "scripts" / "module.py").is_file()

    def test_asset_vocabulary_lockstep_under_bytecode_seeding(self, tmp_path: Path) -> None:
        """Three-way lockstep: copier set == iterator set == digest hash set.

        The plan requires all three consumers of ``is_projected_asset`` to
        agree on the exact file set when bytecode is present in the source.
        """

        from autoskillit.workspace._projected_artifact.materialization import (
            _copy_non_skill_plugin_assets,
        )
        from autoskillit.workspace._projection_cache import iter_public_plugin_asset_files

        source = tmp_path / "source"
        source.mkdir()
        self._seed_source_with_bytecode(source)
        destination = tmp_path / "destination"
        destination.mkdir()

        _copy_non_skill_plugin_assets(source, destination)

        # Set 1: files the iterator yields
        iterated = {str(p.relative_to(source)) for p in iter_public_plugin_asset_files(source)}
        # Set 2: files the copier actually wrote
        copied = {str(p.relative_to(destination)) for p in destination.rglob("*") if p.is_file()}
        # Set 3: files the digest function hashes (derive from the digest
        # implementation — public_plugin_asset_digest hashes exactly the
        # files that iter_public_plugin_asset_files yields, so we verify
        # that by checking the digest is stable across a bytecode-removal
        # cycle AND that both sets agree).
        digest_before = public_plugin_asset_digest(source)
        # Remove all bytecode and re-digest — if the digest function
        # hashes a different set than the iterator, this will diverge.
        import shutil

        for pycache in list(source.rglob("__pycache__")):
            shutil.rmtree(pycache)
        for pattern in ("*.pyc", "*.pyo"):
            for artifact in list(source.rglob(pattern)):
                artifact.unlink()
        digest_after = public_plugin_asset_digest(source)

        assert iterated == copied, (
            "iterator and copier disagree on a bytecode-seeded tree:\n"
            f"  only in iterator: {sorted(iterated - copied)}\n"
            f"  only in copier: {sorted(copied - iterated)}"
        )
        assert digest_before == digest_after, (
            "public_plugin_asset_digest changed when only bytecode was removed — "
            "the digest hashes a different set than the iterator yields"
        )

        ordinary_asset = source / "hooks" / "guard_one.py"
        ordinary_asset.write_text("changed hook\n", encoding="utf-8")
        assert public_plugin_asset_digest(source) != digest_after, (
            "public_plugin_asset_digest ignored an ordinary file yielded by the iterator"
        )

    def test_iter_walk_excludes_bytecode_directly(self, tmp_path: Path) -> None:
        """Direct membership check, independent of the copier comparison above."""
        from autoskillit.workspace._projection_cache import iter_public_plugin_asset_files

        source = tmp_path / "source"
        source.mkdir()
        self._seed_source_with_bytecode(source)

        walked = set(iter_public_plugin_asset_files(source))
        bytecode = {p for p in walked if "__pycache__" in p.parts or p.suffix in {".pyc", ".pyo"}}
        assert not bytecode, f"iter_public_plugin_asset_files yielded bytecode entries: {bytecode}"

    def test_cache_key_digest_ignores_bytecode(self, tmp_path: Path) -> None:
        """Adding or removing only bytecode must not move the projection cache key."""
        import shutil

        source = tmp_path / "source"
        source.mkdir()
        self._seed_source_with_bytecode(source)

        with_bytecode = public_plugin_asset_digest(source)

        for pycache in list(source.rglob("__pycache__")):
            shutil.rmtree(pycache)
        for artifact in list(source.rglob("*.pyo")):
            artifact.unlink()

        without_bytecode = public_plugin_asset_digest(source)
        assert with_bytecode == without_bytecode, (
            "public_plugin_asset_digest changed when only bytecode was removed — "
            "bytecode must be excluded from the digest by is_projected_asset"
        )
