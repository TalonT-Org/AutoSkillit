"""Focused source-currency classification coverage."""

from __future__ import annotations

import pytest

from autoskillit.core.install import install_detect
from autoskillit.core.install.install_detect import SourceCurrencyStatus, source_currency

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


_VCS_INFO = {
    "install_type": "git-vcs",
    "requested_revision": "develop",
    "commit_id": "installed",
    "editable": False,
    "url": "https://example.test/repo.git",
}


def test_source_currency_without_generation_is_unknown(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(install_detect, "parse_direct_url", lambda *_a: _VCS_INFO)

    result = source_currency(tmp_path, generation_root=None)

    assert result.status is SourceCurrencyStatus.UNKNOWN
    assert result.generation_root is None


def test_source_currency_no_generation_root_local_editable_is_unknown(
    monkeypatch, tmp_path
) -> None:
    info = {
        "install_type": "local-editable",
        "requested_revision": None,
        "commit_id": None,
        "editable": True,
        "url": f"file://{tmp_path}",
    }
    monkeypatch.setattr(install_detect, "parse_direct_url", lambda *_a: info)

    result = source_currency(tmp_path, generation_root=None)

    assert result.status is SourceCurrencyStatus.UNKNOWN
    assert result.install_type == info["install_type"]


def test_source_currency_classifies_git_generation(monkeypatch, tmp_path) -> None:
    generation = tmp_path / "generation"
    info = {
        "install_type": "git-vcs",
        "requested_revision": "develop",
        "commit_id": "installed",
        "editable": False,
        "url": "https://example.test/repo.git",
    }
    monkeypatch.setattr(install_detect, "parse_direct_url", lambda _root: info)

    def fake_git(_checkout, *args):
        if args == ("rev-parse", "HEAD"):
            return "head"
        if args[:2] == ("cat-file", "-e"):
            return ""
        if args[:2] == ("merge-base", "--is-ancestor"):
            return ""
        if args[:2] == ("rev-list", "--count"):
            return "3"
        raise AssertionError(args)

    monkeypatch.setattr(install_detect, "_git", fake_git)

    stale = source_currency(tmp_path, generation_root=generation)

    assert stale.status is SourceCurrencyStatus.STALE
    assert stale.behind_by == 3

    info["commit_id"] = "head"
    current = source_currency(tmp_path, generation_root=generation)
    assert current.status is SourceCurrencyStatus.CURRENT


def test_source_currency_reports_foreign_commit(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        install_detect,
        "parse_direct_url",
        lambda _root: {
            "install_type": "git-vcs",
            "requested_revision": "develop",
            "commit_id": "foreign",
            "editable": False,
            "url": "",
        },
    )

    def fake_git(_checkout, *args):
        return "head" if args == ("rev-parse", "HEAD") else None

    monkeypatch.setattr(install_detect, "_git", fake_git)

    result = source_currency(tmp_path, generation_root=tmp_path / "generation")
    assert result.status is SourceCurrencyStatus.NOT_SOURCE_CHECKOUT


def test_source_currency_classifies_diverged_head(monkeypatch, tmp_path) -> None:
    """A checkout whose HEAD is not a descendant of the installed commit is DIVERGED."""
    monkeypatch.setattr(install_detect, "parse_direct_url", lambda _root: _VCS_INFO)

    def fake_git(_checkout, *args):
        if args == ("rev-parse", "HEAD"):
            return "head"
        if args[:2] == ("cat-file", "-e"):
            return ""
        if args[:2] == ("merge-base", "--is-ancestor"):
            return None
        if args[:2] == ("rev-list", "--count"):
            raise AssertionError("rev-list must not be called on the DIVERGED branch")
        raise AssertionError(args)

    monkeypatch.setattr(install_detect, "_git", fake_git)

    result = source_currency(tmp_path, generation_root=tmp_path / "generation")

    assert result.status is SourceCurrencyStatus.DIVERGED
    assert result.behind_by is None


def test_source_currency_local_path_current_at_equal_versions(monkeypatch, tmp_path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "autoskillit"\nversion = "1.2.3"\n', encoding="utf-8"
    )
    generation = tmp_path / "generation"
    info = {
        "install_type": "local-path",
        "requested_revision": None,
        "commit_id": None,
        "editable": False,
        "url": f"file://{tmp_path}",
    }
    monkeypatch.setattr(install_detect, "parse_direct_url", lambda _root: info)
    monkeypatch.setattr(install_detect, "distribution_version_at", lambda _root: "1.2.3")

    result = source_currency(tmp_path, generation_root=generation)

    assert result.status is SourceCurrencyStatus.CURRENT
    assert result.installed_version == "1.2.3"
    assert result.checkout_version == "1.2.3"
    assert result.install_type == info["install_type"]


def test_source_currency_local_path_stale_at_different_versions(monkeypatch, tmp_path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "autoskillit"\nversion = "1.3.0"\n', encoding="utf-8"
    )
    generation = tmp_path / "generation"
    info = {
        "install_type": "local-path",
        "requested_revision": None,
        "commit_id": None,
        "editable": False,
        "url": f"file://{tmp_path}",
    }
    monkeypatch.setattr(install_detect, "parse_direct_url", lambda _root: info)
    monkeypatch.setattr(install_detect, "distribution_version_at", lambda _root: "1.2.3")

    result = source_currency(tmp_path, generation_root=generation)

    assert result.status is SourceCurrencyStatus.STALE
    assert result.installed_version == "1.2.3"
    assert result.checkout_version == "1.3.0"
    assert result.install_type == info["install_type"]
    assert result.behind_by is None


def test_source_currency_local_path_recorded_path_mismatch_is_not_source_checkout(
    monkeypatch, tmp_path
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "autoskillit"\nversion = "1.2.3"\n', encoding="utf-8"
    )
    elsewhere = tmp_path / "elsewhere"
    generation = tmp_path / "generation"
    info = {
        "install_type": "local-path",
        "requested_revision": None,
        "commit_id": None,
        "editable": False,
        "url": f"file://{elsewhere}",
    }
    monkeypatch.setattr(install_detect, "parse_direct_url", lambda _root: info)
    monkeypatch.setattr(install_detect, "distribution_version_at", lambda _root: "1.2.3")

    result = source_currency(tmp_path, generation_root=generation)

    assert result.status is SourceCurrencyStatus.NOT_SOURCE_CHECKOUT


def test_source_currency_local_path_foreign_checkout_is_not_source_checkout(
    monkeypatch, tmp_path
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "other-package"\nversion = "1.2.3"\n', encoding="utf-8"
    )
    generation = tmp_path / "generation"
    info = {
        "install_type": "local-path",
        "requested_revision": None,
        "commit_id": None,
        "editable": False,
        "url": f"file://{tmp_path}",
    }
    monkeypatch.setattr(install_detect, "parse_direct_url", lambda _root: info)
    monkeypatch.setattr(install_detect, "distribution_version_at", lambda _root: "1.2.3")

    result = source_currency(tmp_path, generation_root=generation)

    assert result.status is SourceCurrencyStatus.NOT_SOURCE_CHECKOUT


def test_source_currency_unknown_provenance_current_by_version(monkeypatch, tmp_path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "autoskillit"\nversion = "1.2.3"\n', encoding="utf-8"
    )
    generation = tmp_path / "generation"
    info = {
        "install_type": "unknown",
        "requested_revision": None,
        "commit_id": None,
        "editable": False,
        "url": "",
    }
    monkeypatch.setattr(install_detect, "parse_direct_url", lambda _root: info)
    monkeypatch.setattr(install_detect, "distribution_version_at", lambda _root: "1.2.3")

    result = source_currency(tmp_path, generation_root=generation)

    assert result.status is SourceCurrencyStatus.CURRENT


def test_source_currency_unknown_provenance_stale_by_version(monkeypatch, tmp_path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "autoskillit"\nversion = "1.3.0"\n', encoding="utf-8"
    )
    generation = tmp_path / "generation"
    info = {
        "install_type": "unknown",
        "requested_revision": None,
        "commit_id": None,
        "editable": False,
        "url": "",
    }
    monkeypatch.setattr(install_detect, "parse_direct_url", lambda _root: info)
    monkeypatch.setattr(install_detect, "distribution_version_at", lambda _root: "1.2.3")

    result = source_currency(tmp_path, generation_root=generation)

    assert result.status is SourceCurrencyStatus.STALE


def test_source_currency_unknown_provenance_foreign_checkout_is_not_source_checkout(
    monkeypatch, tmp_path
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "other-package"\nversion = "1.2.3"\n', encoding="utf-8"
    )
    generation = tmp_path / "generation"
    info = {
        "install_type": "unknown",
        "requested_revision": None,
        "commit_id": None,
        "editable": False,
        "url": "",
    }
    monkeypatch.setattr(install_detect, "parse_direct_url", lambda _root: info)
    monkeypatch.setattr(install_detect, "distribution_version_at", lambda _root: "1.2.3")

    result = source_currency(tmp_path, generation_root=generation)

    assert result.status is SourceCurrencyStatus.NOT_SOURCE_CHECKOUT


def test_source_currency_no_generation_root_local_path_uses_running_version(
    monkeypatch, tmp_path
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "autoskillit"\nversion = "1.2.3"\n', encoding="utf-8"
    )
    info = {
        "install_type": "local-path",
        "requested_revision": None,
        "commit_id": None,
        "editable": False,
        "url": f"file://{tmp_path}",
    }
    monkeypatch.setattr(install_detect, "parse_direct_url", lambda *_a: info)
    monkeypatch.setattr(install_detect, "_running_distribution_version", lambda: "1.2.3")

    result = source_currency(tmp_path, generation_root=None)

    assert result.status is SourceCurrencyStatus.CURRENT
    assert result.installed_version == "1.2.3"
    assert result.generation_root is None


def test_source_currency_no_generation_root_unknown_provenance_uses_running_version(
    monkeypatch, tmp_path
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "autoskillit"\nversion = "1.3.0"\n', encoding="utf-8"
    )
    info = {
        "install_type": "unknown",
        "requested_revision": None,
        "commit_id": None,
        "editable": False,
        "url": "",
    }
    monkeypatch.setattr(install_detect, "parse_direct_url", lambda *_a: info)
    monkeypatch.setattr(install_detect, "_running_distribution_version", lambda: "1.2.3")

    result = source_currency(tmp_path, generation_root=None)

    assert result.status is SourceCurrencyStatus.STALE
    assert result.installed_version == "1.2.3"
    assert result.checkout_version == "1.3.0"
    assert result.generation_root is None
