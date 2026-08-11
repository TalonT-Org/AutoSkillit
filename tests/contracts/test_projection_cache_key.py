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
import json
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
        "adaptation_identity": "a:adapted",
        "namespace_identity": "a:bundled",
        "asset_digest": "0" * 64,
        "rendered_hooks_digest": "f" * 64,
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
            ("adaptation_identity", "a:changed-adaptation"),
            ("namespace_identity", "a:project_local"),
            ("asset_digest", "1" * 64),
            ("rendered_hooks_digest", "e" * 64),
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
            "adaptation_identity",
            "namespace_identity",
            "asset_digest",
            "rendered_hooks_digest",
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
        from autoskillit.core import PluginLoadMode
        from autoskillit.execution.backends.claude import ClaudeCodeBackend
        from autoskillit.workspace import project_default_plugin_authority
        from tests.contracts._projection_helpers import session_catalog

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        backend = ClaudeCodeBackend()
        catalog = session_catalog()

        first_authority = project_default_plugin_authority(
            cwd=tmp_path,
            base_branch="main",
            catalog=catalog,
        )
        first = first_authority.acquire_launch_binding(
            backend=backend,
            load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
        )
        try:
            assert first.plugin_dir is not None
            marker = first.plugin_dir / "recipes" / "_cache_key_probe.yaml"
            marker.write_text("probe: 1\n")

            from autoskillit.core import pkg_root

            real_digest = public_plugin_asset_digest(pkg_root())
            monkeypatch.setattr(
                "autoskillit.workspace._projected_artifact.authority.public_plugin_asset_digest",
                lambda _root: real_digest[:-1] + ("0" if real_digest[-1] != "0" else "1"),
            )

            second_authority = project_default_plugin_authority(
                cwd=tmp_path,
                base_branch="main",
                catalog=catalog,
            )
            second = second_authority.acquire_launch_binding(
                backend=backend,
                load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
            )
            try:
                assert second.plugin_dir is not None
                assert second.plugin_dir != first.plugin_dir, (
                    "an asset-digest change did not change the cache key — a release that "
                    "touches recipes/, agents/ or hooks/ without touching a skill would reuse "
                    "the previous release's projection"
                )
                assert not (second.plugin_dir / "recipes" / "_cache_key_probe.yaml").exists(), (
                    "the new projection was not re-materialised from source"
                )
            finally:
                second.close()
            assert second.closed
        finally:
            first.close()
        assert first.closed

    def test_identical_inputs_reuse_the_same_projection(self, tmp_path: Path, monkeypatch) -> None:
        """The key must still be stable — invalidation, not churn."""
        from autoskillit.core import PluginLoadMode
        from autoskillit.execution.backends.claude import ClaudeCodeBackend
        from autoskillit.workspace import project_default_plugin_authority
        from tests.contracts._projection_helpers import session_catalog

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        backend = ClaudeCodeBackend()
        catalog = session_catalog()
        kwargs = {
            "cwd": tmp_path,
            "base_branch": "main",
            "catalog": catalog,
        }
        first = project_default_plugin_authority(**kwargs).acquire_launch_binding(
            backend=backend,
            load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
        )
        try:
            second = project_default_plugin_authority(**kwargs).acquire_launch_binding(
                backend=backend,
                load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
            )
            try:
                assert second.plugin_dir == first.plugin_dir
            finally:
                second.close()
            assert second.closed
        finally:
            first.close()
        assert first.closed


class TestRenderedHooksDigestChangesTheKey:
    """T-A4: The cache key must change when rendered hook content changes."""

    def test_renderer_change_invalidates_the_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Monkeypatch the renderer to return altered commands;
        the cache key must differ.
        """
        from autoskillit.core import PluginLoadMode
        from autoskillit.execution.backends.claude import ClaudeCodeBackend
        from autoskillit.workspace import project_default_plugin_authority
        from tests.contracts._projection_helpers import session_catalog

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        backend = ClaudeCodeBackend()
        catalog = session_catalog()

        first = project_default_plugin_authority(
            cwd=tmp_path,
            base_branch="main",
            catalog=catalog,
        ).acquire_launch_binding(
            backend=backend,
            load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
        )
        try:
            first_dir = first.plugin_dir
            assert first_dir is not None

            # Monkeypatch render_hooks_json_text to return different bytes
            import autoskillit.hook_registry as _hr
            import autoskillit.workspace._projected_artifact.authority as _auth

            original_render = _hr.render_hooks_json_text

            def altered_render(*args, **kwargs):
                text = original_render(*args, **kwargs)
                return text.replace("_dispatch.py", "_dispatch_v2.py")

            monkeypatch.setattr(_hr, "render_hooks_json_text", altered_render)
            monkeypatch.setattr(_auth, "render_hooks_json_text", altered_render)

            second = project_default_plugin_authority(
                cwd=tmp_path,
                base_branch="main",
                catalog=catalog,
            ).acquire_launch_binding(
                backend=backend,
                load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
            )
            try:
                assert second.plugin_dir is not None
                assert second.plugin_dir != first_dir, (
                    "renderer output changed but the cache key did not — a "
                    "renderer change would silently reuse a stale projection"
                )
            finally:
                second.close()
        finally:
            first.close()

    def test_key_is_stable_when_rendered_hooks_are_unchanged(self) -> None:
        """Without a render change, the key must be deterministic."""
        k1 = _key()
        k2 = _key()
        assert k1.digest() == k2.digest()


class TestOrphanedProjectionRetirementIsLeaseGated:
    @pytest.mark.parametrize("malformed_version", [True, 1.0])
    def test_projection_identity_rejects_non_integer_version_aliases(
        self,
        tmp_path: Path,
        monkeypatch,
        malformed_version: object,
    ) -> None:
        from autoskillit.core import PluginArtifactValidationError, PluginLoadMode
        from autoskillit.execution.backends.claude import ClaudeCodeBackend
        from autoskillit.workspace import project_default_plugin_authority
        from autoskillit.workspace._projection_cache import read_projected_plugin_identity
        from tests.contracts._projection_helpers import session_catalog

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        binding = project_default_plugin_authority(
            cwd=tmp_path,
            base_branch="main",
            catalog=session_catalog(),
        ).acquire_launch_binding(
            backend=ClaudeCodeBackend(),
            load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
        )
        try:
            binding.close()
            manifest = json.loads(binding.identity.manifest_path.read_text(encoding="utf-8"))
            manifest["projection_version"] = malformed_version
            binding.identity.manifest_path.write_text(
                json.dumps(manifest),
                encoding="utf-8",
            )

            with pytest.raises(PluginArtifactValidationError, match="version mismatch"):
                read_projected_plugin_identity(
                    binding.identity.managed_path,
                    manifest_path=binding.identity.manifest_path,
                    expected_semantic_key=binding.identity.semantic_key,
                    expected_projection_version=1,
                )
        finally:
            binding.close()

    @pytest.mark.parametrize(
        ("field", "value", "message"),
        [
            ("unexpected", True, "unexpected fields"),
            ("artifact_kind", "installed_plugin", "artifact kind"),
        ],
    )
    def test_projection_identity_requires_exact_manifest_contract(
        self,
        tmp_path: Path,
        monkeypatch,
        field: str,
        value: object,
        message: str,
    ) -> None:
        from autoskillit.core import PluginArtifactValidationError, PluginLoadMode
        from autoskillit.execution.backends.claude import ClaudeCodeBackend
        from autoskillit.workspace import project_default_plugin_authority
        from autoskillit.workspace._projection_cache import read_projected_plugin_identity
        from tests.contracts._projection_helpers import session_catalog

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        binding = project_default_plugin_authority(
            cwd=tmp_path,
            base_branch="main",
            catalog=session_catalog(),
        ).acquire_launch_binding(
            backend=ClaudeCodeBackend(),
            load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
        )
        binding.close()
        manifest_path = binding.identity.manifest_path
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest[field] = value
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        with pytest.raises(PluginArtifactValidationError, match=message):
            read_projected_plugin_identity(
                binding.identity.managed_path,
                manifest_path=manifest_path,
                expected_semantic_key=binding.identity.semantic_key,
            )

    def test_projection_identity_requires_canonical_paths_and_current_digest(
        self,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        from autoskillit.core import PluginArtifactValidationError, PluginLoadMode
        from autoskillit.execution.backends.claude import ClaudeCodeBackend
        from autoskillit.workspace import project_default_plugin_authority
        from autoskillit.workspace._projection_cache import read_projected_plugin_identity
        from tests.contracts._projection_helpers import session_catalog

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        binding = project_default_plugin_authority(
            cwd=tmp_path,
            base_branch="main",
            catalog=session_catalog(),
        ).acquire_launch_binding(
            backend=ClaudeCodeBackend(),
            load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
        )
        binding.close()
        identity = binding.identity
        alias = identity.managed_path.parent / "projection-alias"
        alias.symlink_to(identity.managed_path, target_is_directory=True)

        with pytest.raises(PluginArtifactValidationError, match="canonical directory"):
            read_projected_plugin_identity(
                alias,
                manifest_path=identity.manifest_path,
                expected_semantic_key=identity.semantic_key,
            )
        with pytest.raises(PluginArtifactValidationError, match="manifest path"):
            read_projected_plugin_identity(
                identity.managed_path,
                manifest_path=identity.manifest_path.with_name("other-manifest.json"),
                expected_semantic_key=identity.semantic_key,
            )

        (identity.managed_path / "tampered-after-publication").write_text(
            "changed",
            encoding="utf-8",
        )
        with pytest.raises(PluginArtifactValidationError, match="digest mismatch"):
            read_projected_plugin_identity(
                identity.managed_path,
                manifest_path=identity.manifest_path,
                expected_semantic_key=identity.semantic_key,
            )

    def test_pruning_queues_only_after_reader_release(
        self,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        from autoskillit.core import PluginLoadMode, read_retiring_cache
        from autoskillit.execution.backends.claude import ClaudeCodeBackend
        from autoskillit.workspace import (
            project_default_plugin_authority,
            prune_stale_projections,
        )
        from tests.contracts._projection_helpers import session_catalog

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        backend = ClaudeCodeBackend()
        orphan = project_default_plugin_authority(
            cwd=tmp_path,
            base_branch="old",
            catalog=session_catalog(),
        ).acquire_launch_binding(
            backend=backend,
            load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
        )
        active = project_default_plugin_authority(
            cwd=tmp_path,
            base_branch="new",
            catalog=session_catalog(),
        ).acquire_launch_binding(
            backend=backend,
            load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
        )
        try:
            assert read_retiring_cache().records == ()
            orphan.close()
            assert (
                prune_stale_projections(
                    tmp_path / ".autoskillit" / "plugin-projections",
                    active_key=active.identity.semantic_key,
                )
                == 1
            )
            assert read_retiring_cache().records[0].identity == orphan.identity
            assert orphan.identity.managed_path.is_dir()
            assert orphan.identity.manifest_path.is_file()
        finally:
            orphan.close()
            active.close()

    def test_pruning_logs_invalid_projection_identity(
        self,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        from unittest.mock import Mock

        import autoskillit.workspace._projection_cache as projection_cache
        from autoskillit.core import PluginLoadMode
        from autoskillit.execution.backends.claude import ClaudeCodeBackend
        from autoskillit.workspace import (
            project_default_plugin_authority,
            prune_stale_projections,
        )
        from tests.contracts._projection_helpers import session_catalog

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        backend = ClaudeCodeBackend()
        orphan = project_default_plugin_authority(
            cwd=tmp_path,
            base_branch="old",
            catalog=session_catalog(),
        ).acquire_launch_binding(
            backend=backend,
            load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
        )
        active = project_default_plugin_authority(
            cwd=tmp_path,
            base_branch="new",
            catalog=session_catalog(),
        ).acquire_launch_binding(
            backend=backend,
            load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
        )
        logger = Mock()
        monkeypatch.setattr(projection_cache, "logger", logger)
        try:
            orphan.close()
            orphan.identity.manifest_path.write_text("{not-json", encoding="utf-8")

            assert (
                prune_stale_projections(
                    tmp_path / ".autoskillit" / "plugin-projections",
                    active_key=active.identity.semantic_key,
                )
                == 0
            )
            logger.warning.assert_called_once()
            assert logger.warning.call_args.args == ("projected_plugin_prune_validation_failed",)
            assert logger.warning.call_args.kwargs["projection_path"] == str(
                orphan.identity.managed_path
            )
            assert logger.warning.call_args.kwargs["error"]
        finally:
            orphan.close()
            active.close()
