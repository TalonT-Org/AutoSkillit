"""Unit tests for server/_editable_guard.py — scan_editable_installs_for_worktree."""

import json
import subprocess
from pathlib import Path

import pytest
import structlog.testing

from autoskillit.server import _editable_guard
from autoskillit.server._editable_guard import scan_editable_installs_for_worktree

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def _make_dist_info(site_packages: Path, pkg: str, version: str, direct_url: dict) -> None:
    dist_info = site_packages / f"{pkg}-{version}.dist-info"
    dist_info.mkdir(parents=True, exist_ok=True)
    (dist_info / "direct_url.json").write_text(json.dumps(direct_url))


def _patch_discovery_environment(
    monkeypatch: pytest.MonkeyPatch,
    *,
    executable: Path | None,
    user_site: Path,
) -> None:
    monkeypatch.setattr(
        _editable_guard.shutil,
        "which",
        lambda name: str(executable) if name == "python3" and executable is not None else None,
    )
    monkeypatch.setattr(
        _editable_guard.site,
        "getusersitepackages",
        lambda: str(user_site),
    )


class TestScanEditableInstalls:
    def test_empty_site_packages_returns_empty(self, tmp_path: Path) -> None:
        """No dist-info directories → empty result."""
        site = tmp_path / "site-packages"
        site.mkdir()
        result = scan_editable_installs_for_worktree(
            worktree_path=tmp_path / "worktree",
            site_packages_dirs=[site],
        )
        assert result.findings == ()

    def test_editable_install_pointing_into_worktree_detected(self, tmp_path: Path) -> None:
        """Editable install with url inside worktree_path → reported."""
        worktree = tmp_path / "worktree"
        site = tmp_path / "site-packages"
        _make_dist_info(
            site,
            "autoskillit",
            "0.6.12",
            {
                "url": f"file://{worktree}/src",
                "dir_info": {"editable": True},
            },
        )
        result = scan_editable_installs_for_worktree(worktree, [site])
        assert len(result.findings) == 1
        assert "autoskillit" in result.findings[0].lower()
        assert str(worktree) in result.findings[0]

    def test_editable_install_pointing_elsewhere_not_reported(self, tmp_path: Path) -> None:
        """Editable install with url outside worktree_path → not reported."""
        worktree = tmp_path / "worktree"
        other = tmp_path / "other-project"
        site = tmp_path / "site-packages"
        _make_dist_info(
            site,
            "autoskillit",
            "0.6.12",
            {
                "url": f"file://{other}/src",
                "dir_info": {"editable": True},
            },
        )
        result = scan_editable_installs_for_worktree(worktree, [site])
        assert result.findings == ()

    def test_non_editable_install_not_reported(self, tmp_path: Path) -> None:
        """Install with editable=False → not reported even if url points to worktree."""
        worktree = tmp_path / "worktree"
        site = tmp_path / "site-packages"
        _make_dist_info(
            site,
            "autoskillit",
            "0.6.12",
            {
                "url": f"file://{worktree}/src",
                "dir_info": {"editable": False},
            },
        )
        result = scan_editable_installs_for_worktree(worktree, [site])
        assert result.findings == ()

    def test_newer_pep610_format_editable_detected(self, tmp_path: Path) -> None:
        """New-format direct_url.json (top-level 'editable' key) is also detected."""
        worktree = tmp_path / "worktree"
        site = tmp_path / "site-packages"
        _make_dist_info(
            site,
            "autoskillit",
            "0.6.12",
            {
                "url": f"file://{worktree}/src",
                "editable": True,
            },
        )
        result = scan_editable_installs_for_worktree(worktree, [site])
        assert len(result.findings) == 1

    def test_malformed_metadata_is_recorded_unverified(self, tmp_path: Path) -> None:
        worktree = tmp_path / "worktree"
        site = tmp_path / "site-packages"
        dist_info = site / "autoskillit-0.6.12.dist-info"
        dist_info.mkdir(parents=True)
        (dist_info / "direct_url.json").write_text("not valid json {{{")
        result = scan_editable_installs_for_worktree(worktree, [site])
        assert result.findings == ()
        assert len(result.unverified) == 1
        assert "malformed" in result.unverified[0]

    def test_missing_direct_url_json_ignored(self, tmp_path: Path) -> None:
        """Dist-info without direct_url.json (e.g. regular PyPI install) → not reported."""
        worktree = tmp_path / "worktree"
        site = tmp_path / "site-packages"
        dist_info = site / "autoskillit-0.6.12.dist-info"
        dist_info.mkdir(parents=True)
        # No direct_url.json file — a normal non-editable PyPI install
        result = scan_editable_installs_for_worktree(worktree, [site])
        assert result.findings == ()
        assert result.unverified == ()

    def test_multiple_site_packages_all_scanned(self, tmp_path: Path) -> None:
        """Multiple site-packages directories are all scanned."""
        worktree = tmp_path / "worktree"
        site_a = tmp_path / "site-a"
        site_b = tmp_path / "site-b"
        # Only site_b has the poisoned install
        site_a.mkdir()
        _make_dist_info(
            site_b,
            "autoskillit",
            "0.6.12",
            {
                "url": f"file://{worktree}/src",
                "dir_info": {"editable": True},
            },
        )
        result = scan_editable_installs_for_worktree(worktree, [site_a, site_b])
        assert len(result.findings) == 1

    def test_enumeration_read_race_is_skipped_and_recorded_unverified(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        worktree = tmp_path / "worktree"
        site = tmp_path / "site-packages"
        _make_dist_info(site, "autoskillit", "1.0", {"url": "file:///elsewhere"})
        direct_url = site / "autoskillit-1.0.dist-info" / "direct_url.json"
        original_read_text = Path.read_text

        def vanished(path: Path) -> str:
            if path == direct_url:
                raise FileNotFoundError(str(direct_url))
            return original_read_text(path)

        monkeypatch.setattr(Path, "read_text", vanished)
        result = scan_editable_installs_for_worktree(worktree, [site])

        assert result.findings == ()
        assert len(result.unverified) == 1
        assert str(direct_url) in result.unverified[0]
        assert "vanished" in result.unverified[0]

    def test_undecodable_metadata_is_recorded_unverified(self, tmp_path: Path) -> None:
        worktree = tmp_path / "worktree"
        site = tmp_path / "site-packages"
        direct_url = site / "autoskillit-1.0.dist-info" / "direct_url.json"
        direct_url.parent.mkdir(parents=True)
        direct_url.write_bytes(b"\xff\xfe\x00garbage")

        result = scan_editable_installs_for_worktree(worktree, [site])

        assert result.findings == ()
        assert len(result.unverified) == 1
        assert "decode" in result.unverified[0]

    def test_unexpected_exception_propagates(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        worktree = tmp_path / "worktree"
        site = tmp_path / "site-packages"
        _make_dist_info(site, "autoskillit", "1.0", {"url": "file:///elsewhere"})

        def unexpected(_data: dict, _worktree: Path) -> bool:
            raise AttributeError("boom")

        monkeypatch.setattr(_editable_guard, "_is_editable_in_worktree", unexpected)

        with pytest.raises(AttributeError, match="boom"):
            scan_editable_installs_for_worktree(worktree, [site])

    def test_interpreter_probe_failure_is_recorded_unverified(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        worktree = tmp_path / "worktree"
        user_site = tmp_path / "user-site"
        user_site.mkdir()
        executable = tmp_path / "external-python"
        _patch_discovery_environment(monkeypatch, executable=executable, user_site=user_site)

        def fail_probe(*_args, **_kwargs):
            raise OSError("cannot execute")

        monkeypatch.setattr(_editable_guard.subprocess, "run", fail_probe)
        result = scan_editable_installs_for_worktree(worktree, site_packages_dirs=None)

        assert result.findings == ()
        assert any(str(executable) in reason for reason in result.unverified)

    def test_user_site_probe_failure_is_recorded_unverified(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(_editable_guard.shutil, "which", lambda _name: None)
        monkeypatch.setattr(
            _editable_guard.subprocess,
            "run",
            lambda *_args, **_kwargs: pytest.fail("subprocess.run must not be called"),
        )

        def fail_user_site() -> str:
            raise AttributeError("unavailable")

        monkeypatch.setattr(_editable_guard.site, "getusersitepackages", fail_user_site)
        result = scan_editable_installs_for_worktree(
            tmp_path / "worktree", site_packages_dirs=None
        )

        assert result.findings == ()
        assert any("user site-packages" in reason for reason in result.unverified)

    def test_clean_scan_reports_nothing_unverified(self, tmp_path: Path) -> None:
        site = tmp_path / "site-packages"
        _make_dist_info(site, "example", "1.0", {"url": "https://example.invalid/pkg"})

        result = scan_editable_installs_for_worktree(tmp_path / "worktree", [site])

        assert result.findings == ()
        assert result.unverified == ()

    def test_skip_diagnostics_reach_the_package_logger(self, tmp_path: Path) -> None:
        site = tmp_path / "site-packages"
        direct_url = site / "autoskillit-1.0.dist-info" / "direct_url.json"
        direct_url.parent.mkdir(parents=True)
        direct_url.write_text("not json")

        with structlog.testing.capture_logs() as logs:
            scan_editable_installs_for_worktree(tmp_path / "worktree", [site])

        event = next(
            entry for entry in logs if entry.get("event") == "editable_guard_metadata_invalid"
        )
        assert event["path"] == str(direct_url)
        assert event["error_type"] == "JSONDecodeError"

    def test_nonzero_interpreter_exit_is_recorded_unverified(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        user_site = tmp_path / "user-site"
        user_site.mkdir()
        executable = tmp_path / "external-python"
        _patch_discovery_environment(monkeypatch, executable=executable, user_site=user_site)
        monkeypatch.setattr(
            _editable_guard.subprocess,
            "run",
            lambda *_args, **_kwargs: subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="probe failed"
            ),
        )

        result = scan_editable_installs_for_worktree(
            tmp_path / "worktree", site_packages_dirs=None
        )

        assert result.findings == ()
        assert any(str(executable) in reason for reason in result.unverified)

    def test_vanished_site_dir_is_recorded_unverified(self, tmp_path: Path) -> None:
        vanished = tmp_path / "vanished-site-packages"

        result = scan_editable_installs_for_worktree(
            tmp_path / "worktree", site_packages_dirs=[vanished]
        )

        assert result.findings == ()
        assert result.unverified == (f"site-packages directory vanished: {vanished}",)

    def test_non_dict_metadata_is_recorded_unverified(self, tmp_path: Path) -> None:
        site = tmp_path / "site-packages"
        direct_url = site / "autoskillit-1.0.dist-info" / "direct_url.json"
        direct_url.parent.mkdir(parents=True)
        direct_url.write_text('"just a string"')

        result = scan_editable_installs_for_worktree(tmp_path / "worktree", [site])

        assert result.findings == ()
        assert len(result.unverified) == 1
        assert "JSON object" in result.unverified[0]

    def test_symlink_loop_interpreter_is_recorded_unverified(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        user_site = tmp_path / "user-site"
        user_site.mkdir()
        executable = tmp_path / "loop-python"
        executable.symlink_to(executable.name)
        _patch_discovery_environment(monkeypatch, executable=executable, user_site=user_site)
        monkeypatch.setattr(
            _editable_guard.subprocess,
            "run",
            lambda *_args, **_kwargs: pytest.fail("subprocess.run must not be called"),
        )

        result = scan_editable_installs_for_worktree(
            tmp_path / "worktree", site_packages_dirs=None
        )

        assert result.findings == ()
        assert any(str(executable) in reason for reason in result.unverified)
