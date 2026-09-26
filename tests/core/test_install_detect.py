"""Tests for core/_install_detect.py — install-type detection."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def _fake_dist(direct_url_json: str | None) -> MagicMock:
    dist = MagicMock()
    dist.read_text.return_value = direct_url_json
    return dist


def _write_staged_metadata(
    root: Path,
    *,
    python_dir: str,
    version: str = "1.2.3",
    revision: str = "develop",
    commit: str = "a" * 40,
) -> Path:
    dist_info = (
        root
        / "autoskillit"
        / "lib"
        / python_dir
        / "site-packages"
        / f"autoskillit-{version}.dist-info"
    )
    dist_info.mkdir(parents=True)
    (dist_info / "direct_url.json").write_text(
        json.dumps(
            {
                "url": "https://github.com/TalonT-Org/AutoSkillit.git",
                "vcs_info": {
                    "vcs": "git",
                    "requested_revision": revision,
                    "commit_id": commit,
                },
            }
        ),
        encoding="utf-8",
    )
    return dist_info


def test_is_dev_install_editable_returns_true(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = json.dumps({"url": "file:///home/user/autoskillit", "dir_info": {"editable": True}})
    monkeypatch.setattr(
        "importlib.metadata.Distribution.from_name",
        lambda _name: _fake_dist(payload),
    )
    from autoskillit.core._install_detect import is_dev_install

    assert is_dev_install() is True


def test_is_dev_install_git_vcs_stable_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = json.dumps(
        {
            "url": "https://github.com/TalonT-Org/AutoSkillit.git",
            "vcs_info": {"vcs": "git", "requested_revision": "stable", "commit_id": "abc123"},
        }
    )
    monkeypatch.setattr(
        "importlib.metadata.Distribution.from_name",
        lambda _name: _fake_dist(payload),
    )
    from autoskillit.core._install_detect import is_dev_install

    assert is_dev_install() is False


@pytest.mark.parametrize(
    "revision, expected",
    [
        ("develop", True),
        ("feature-foo", True),
        ("integration", True),
        ("main", False),
        ("stable", False),
        ("v1.0.0", False),
        ("v0.9.300", False),
        (None, False),
    ],
)
def test_is_dev_install_git_vcs_revision_matrix(
    monkeypatch: pytest.MonkeyPatch, revision: str | None, expected: bool
) -> None:
    vcs_info: dict = {"vcs": "git", "commit_id": "abc123"}
    if revision is not None:
        vcs_info["requested_revision"] = revision
    payload = json.dumps(
        {"url": "https://github.com/TalonT-Org/AutoSkillit.git", "vcs_info": vcs_info}
    )
    monkeypatch.setattr(
        "importlib.metadata.Distribution.from_name",
        lambda _name: _fake_dist(payload),
    )
    from autoskillit.core._install_detect import is_dev_install

    assert is_dev_install() is expected


def test_is_dev_install_local_path_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = json.dumps({"url": "file:///home/user/autoskillit", "dir_info": {}})
    monkeypatch.setattr(
        "importlib.metadata.Distribution.from_name",
        lambda _name: _fake_dist(payload),
    )
    from autoskillit.core._install_detect import is_dev_install

    assert is_dev_install() is False


def test_is_dev_install_unknown_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib.metadata

    def _raise(_name: str) -> None:
        raise importlib.metadata.PackageNotFoundError("autoskillit")

    monkeypatch.setattr("importlib.metadata.Distribution.from_name", _raise)
    from autoskillit.core._install_detect import is_dev_install

    assert is_dev_install() is False


def test_is_dev_install_no_direct_url_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "importlib.metadata.Distribution.from_name",
        lambda _name: _fake_dist(None),
    )
    from autoskillit.core._install_detect import is_dev_install

    assert is_dev_install() is False


def test_is_dev_install_malformed_json_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "importlib.metadata.Distribution.from_name",
        lambda _name: _fake_dist("not-valid-json{{{"),
    )
    from autoskillit.core._install_detect import is_dev_install

    assert is_dev_install() is False


def test_parse_direct_url_reads_staged_root(tmp_path: Path) -> None:
    from autoskillit.core import distribution_version_at, parse_direct_url

    _write_staged_metadata(tmp_path, python_dir="python3.14")

    info = parse_direct_url(tmp_path)

    assert info["commit_id"] == "a" * 40
    assert info["requested_revision"] == "develop"
    assert distribution_version_at(tmp_path) == "1.2.3"


def test_parse_direct_url_at_missing_root_does_not_fall_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autoskillit.core import parse_direct_url

    def _unexpected_fallback(_name: str) -> None:
        raise AssertionError("staged-root reads must not inspect the running distribution")

    monkeypatch.setattr("importlib.metadata.Distribution.from_name", _unexpected_fallback)

    assert parse_direct_url(tmp_path / "missing") == {
        "install_type": "unknown",
        "requested_revision": None,
        "commit_id": None,
        "editable": False,
        "url": "",
    }


def test_parse_direct_url_globs_any_python_minor_deterministically(tmp_path: Path) -> None:
    from autoskillit.core import parse_direct_url

    _write_staged_metadata(
        tmp_path,
        python_dir="python9.99",
        revision="later-sort",
        commit="b" * 40,
    )
    _write_staged_metadata(
        tmp_path,
        python_dir="python1.2",
        revision="earlier-sort",
        commit="c" * 40,
    )
    info = parse_direct_url(tmp_path)

    assert info["requested_revision"] == "earlier-sort"
    assert info["commit_id"] == "c" * 40


@pytest.mark.parametrize(
    "url, expected",
    [
        ("file:///a/b", Path("/a/b")),
        ("file:///a/My%20Dir", Path("/a/My Dir")),
        ("file://localhost/a", Path("/a")),
        ("https://x", None),
        ("", None),
        ("file://localhost", None),
        ("file:", None),
        ("file:rel/x", None),
        ("file://evil.example/a", None),
    ],
)
def test_file_url_path(url: str, expected: Path | None) -> None:
    from autoskillit.core import file_url_path

    assert file_url_path(url) == expected


def test_autoskillit_source_version_reads_version(tmp_path: Path) -> None:
    from autoskillit.core import autoskillit_source_version

    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "autoskillit"\nversion = "1.2.3"\n', encoding="utf-8"
    )

    assert autoskillit_source_version(tmp_path) == "1.2.3"


def test_autoskillit_source_version_foreign_name_returns_none(tmp_path: Path) -> None:
    from autoskillit.core import autoskillit_source_version

    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "other-package"\nversion = "1.2.3"\n', encoding="utf-8"
    )

    assert autoskillit_source_version(tmp_path) is None


def test_autoskillit_source_version_missing_file_returns_none(tmp_path: Path) -> None:
    from autoskillit.core import autoskillit_source_version

    assert autoskillit_source_version(tmp_path) is None


def test_autoskillit_source_version_malformed_toml_returns_none(tmp_path: Path) -> None:
    from autoskillit.core import autoskillit_source_version

    (tmp_path / "pyproject.toml").write_text("not [ valid toml", encoding="utf-8")

    assert autoskillit_source_version(tmp_path) is None


def test_autoskillit_source_version_non_utf8_bytes_returns_none(tmp_path: Path) -> None:
    from autoskillit.core import autoskillit_source_version

    (tmp_path / "pyproject.toml").write_bytes(b"\xff\xfe[project]")

    assert autoskillit_source_version(tmp_path) is None


def test_autoskillit_source_version_non_table_project_returns_none(tmp_path: Path) -> None:
    from autoskillit.core import autoskillit_source_version

    (tmp_path / "pyproject.toml").write_text('project = "x"\n', encoding="utf-8")

    assert autoskillit_source_version(tmp_path) is None


@pytest.mark.parametrize(
    "project_block",
    [
        '[project]\nname = "autoskillit"\n',
        '[project]\nname = "autoskillit"\nversion = 123\n',
    ],
)
def test_autoskillit_source_version_missing_or_non_string_version_returns_none(
    tmp_path: Path, project_block: str
) -> None:
    from autoskillit.core import autoskillit_source_version

    (tmp_path / "pyproject.toml").write_text(project_block, encoding="utf-8")

    assert autoskillit_source_version(tmp_path) is None
