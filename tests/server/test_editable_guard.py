"""Unit tests for server/_editable_guard.py — scan_editable_installs_for_worktree."""
import json
import pytest
from pathlib import Path

from autoskillit.server._editable_guard import scan_editable_installs_for_worktree


def _make_dist_info(site_packages: Path, pkg: str, version: str, direct_url: dict) -> None:
    dist_info = site_packages / f"{pkg}-{version}.dist-info"
    dist_info.mkdir(parents=True, exist_ok=True)
    (dist_info / "direct_url.json").write_text(json.dumps(direct_url))


class TestScanEditableInstalls:

    def test_empty_site_packages_returns_empty(self, tmp_path: Path) -> None:
        """No dist-info directories → empty result."""
        site = tmp_path / "site-packages"
        site.mkdir()
        result = scan_editable_installs_for_worktree(
            worktree_path=tmp_path / "worktree",
            site_packages_dirs=[site],
        )
        assert result == []

    def test_editable_install_pointing_into_worktree_detected(self, tmp_path: Path) -> None:
        """Editable install with url inside worktree_path → reported."""
        worktree = tmp_path / "worktree"
        site = tmp_path / "site-packages"
        _make_dist_info(site, "autoskillit", "0.6.12", {
            "url": f"file://{worktree}/src",
            "dir_info": {"editable": True},
        })
        result = scan_editable_installs_for_worktree(worktree, [site])
        assert len(result) == 1
        assert "autoskillit" in result[0].lower()
        assert str(worktree) in result[0]

    def test_editable_install_pointing_elsewhere_not_reported(self, tmp_path: Path) -> None:
        """Editable install with url outside worktree_path → not reported."""
        worktree = tmp_path / "worktree"
        other = tmp_path / "other-project"
        site = tmp_path / "site-packages"
        _make_dist_info(site, "autoskillit", "0.6.12", {
            "url": f"file://{other}/src",
            "dir_info": {"editable": True},
        })
        result = scan_editable_installs_for_worktree(worktree, [site])
        assert result == []

    def test_non_editable_install_not_reported(self, tmp_path: Path) -> None:
        """Install with editable=False → not reported even if url points to worktree."""
        worktree = tmp_path / "worktree"
        site = tmp_path / "site-packages"
        _make_dist_info(site, "autoskillit", "0.6.12", {
            "url": f"file://{worktree}/src",
            "dir_info": {"editable": False},
        })
        result = scan_editable_installs_for_worktree(worktree, [site])
        assert result == []

    def test_newer_pep610_format_editable_detected(self, tmp_path: Path) -> None:
        """New-format direct_url.json (top-level 'editable' key) is also detected."""
        worktree = tmp_path / "worktree"
        site = tmp_path / "site-packages"
        _make_dist_info(site, "autoskillit", "0.6.12", {
            "url": f"file://{worktree}/src",
            "editable": True,
        })
        result = scan_editable_installs_for_worktree(worktree, [site])
        assert len(result) == 1

    def test_malformed_direct_url_json_fail_open(self, tmp_path: Path) -> None:
        """Malformed JSON in direct_url.json → fail-open (returns [])."""
        worktree = tmp_path / "worktree"
        site = tmp_path / "site-packages"
        dist_info = site / "autoskillit-0.6.12.dist-info"
        dist_info.mkdir(parents=True)
        (dist_info / "direct_url.json").write_text("not valid json {{{")
        result = scan_editable_installs_for_worktree(worktree, [site])
        assert result == []

    def test_missing_direct_url_json_ignored(self, tmp_path: Path) -> None:
        """Dist-info without direct_url.json (e.g. regular PyPI install) → not reported."""
        worktree = tmp_path / "worktree"
        site = tmp_path / "site-packages"
        dist_info = site / "autoskillit-0.6.12.dist-info"
        dist_info.mkdir(parents=True)
        # No direct_url.json file — a normal non-editable PyPI install
        result = scan_editable_installs_for_worktree(worktree, [site])
        assert result == []

    def test_multiple_site_packages_all_scanned(self, tmp_path: Path) -> None:
        """Multiple site-packages directories are all scanned."""
        worktree = tmp_path / "worktree"
        site_a = tmp_path / "site-a"
        site_b = tmp_path / "site-b"
        # Only site_b has the poisoned install
        site_a.mkdir()
        _make_dist_info(site_b, "autoskillit", "0.6.12", {
            "url": f"file://{worktree}/src",
            "dir_info": {"editable": True},
        })
        result = scan_editable_installs_for_worktree(worktree, [site_a, site_b])
        assert len(result) == 1
