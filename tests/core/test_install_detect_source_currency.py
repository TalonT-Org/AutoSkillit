"""Focused source-currency classification coverage."""

from __future__ import annotations

import pytest

from autoskillit.core.install import install_detect
from autoskillit.core.install.install_detect import source_currency

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_source_currency_without_generation_is_unknown(tmp_path) -> None:
    result = source_currency(tmp_path, generation_root=None)

    assert result.status == "unknown"
    assert result.generation_root is None


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

    assert stale.status == "stale"
    assert stale.behind_by == 3

    monkeypatch.setattr(install_detect, "_git", lambda _checkout, *args: "head")
    current = source_currency(tmp_path, generation_root=generation)
    assert current.status == "current"


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
    assert result.status == "not_source_checkout"
