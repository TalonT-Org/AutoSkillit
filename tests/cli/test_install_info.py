"""Tests for cli/_install_info.py — install classification and update policy."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from autoskillit.cli._install_info import (
    _INSTALL_FROM_DEVELOP,
    InstallInfo,
    InstallType,
    comparison_branch,
    detect_install,
    dismissal_window,
    upgrade_command,
)

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_dist(direct_url_json: str | None) -> MagicMock:
    """Return a mock Distribution whose read_text('direct_url.json') returns the given string."""
    dist = MagicMock()
    dist.read_text.return_value = direct_url_json
    return dist


# ---------------------------------------------------------------------------
# detect_install — classification tests
# ---------------------------------------------------------------------------


def test_detect_install_git_vcs_stable(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = json.dumps(
        {
            "url": "https://github.com/TalonT-Org/AutoSkillit.git",
            "vcs_info": {
                "vcs": "git",
                "requested_revision": "stable",
                "commit_id": "abc123def456",
            },
        }
    )
    monkeypatch.setattr(
        "importlib.metadata.Distribution.from_name",
        lambda _name: _fake_dist(payload),
    )
    info = detect_install()
    assert info.install_type == InstallType.GIT_VCS
    assert info.commit_id == "abc123def456"
    assert info.requested_revision == "stable"
    assert info.editable_source is None


def test_detect_install_git_vcs_develop(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = json.dumps(
        {
            "url": "https://github.com/TalonT-Org/AutoSkillit.git",
            "vcs_info": {
                "vcs": "git",
                "requested_revision": "develop",
                "commit_id": "deadbeef0000",
            },
        }
    )
    monkeypatch.setattr(
        "importlib.metadata.Distribution.from_name",
        lambda _name: _fake_dist(payload),
    )
    info = detect_install()
    assert info.install_type == InstallType.GIT_VCS
    assert info.requested_revision == "develop"
    assert info.commit_id == "deadbeef0000"


def test_detect_install_git_vcs_release_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = json.dumps(
        {
            "url": "https://github.com/TalonT-Org/AutoSkillit.git",
            "vcs_info": {
                "vcs": "git",
                "requested_revision": "v0.7.75",
                "commit_id": "cafebabe1234",
            },
        }
    )
    monkeypatch.setattr(
        "importlib.metadata.Distribution.from_name",
        lambda _name: _fake_dist(payload),
    )
    info = detect_install()
    assert info.install_type == InstallType.GIT_VCS
    assert info.requested_revision == "v0.7.75"
    assert info.commit_id == "cafebabe1234"


def test_detect_install_local_editable(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = json.dumps(
        {
            "url": "file:///tmp/repo",
            "dir_info": {"editable": True},
        }
    )
    monkeypatch.setattr(
        "importlib.metadata.Distribution.from_name",
        lambda _name: _fake_dist(payload),
    )
    info = detect_install()
    assert info.install_type == InstallType.LOCAL_EDITABLE
    assert info.editable_source == Path("/tmp/repo")
    assert info.requested_revision is None
    assert info.commit_id is None


def test_detect_install_local_path(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = json.dumps(
        {
            "url": "file:///home/user/autoskillit",
            "dir_info": {},
        }
    )
    monkeypatch.setattr(
        "importlib.metadata.Distribution.from_name",
        lambda _name: _fake_dist(payload),
    )
    info = detect_install()
    assert info.install_type == InstallType.LOCAL_PATH


def test_detect_install_unknown_no_direct_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "importlib.metadata.Distribution.from_name",
        lambda _name: _fake_dist(None),
    )
    info = detect_install()
    assert info.install_type == InstallType.UNKNOWN


def test_detect_install_unknown_malformed_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "importlib.metadata.Distribution.from_name",
        lambda _name: _fake_dist("{{not-json}}"),
    )
    info = detect_install()
    assert info.install_type == InstallType.UNKNOWN


# ---------------------------------------------------------------------------
# comparison_branch — policy tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "requested_revision,expected",
    [
        ("stable", "releases/latest"),
        ("main", "releases/latest"),
        ("v0.7.75", "releases/latest"),
        (None, "releases/latest"),  # UNKNOWN has no revision
    ],
)
def test_comparison_branch_stable_variants(requested_revision: str | None, expected: str) -> None:
    info = InstallInfo(
        install_type=InstallType.GIT_VCS,
        commit_id="abc123",
        requested_revision=requested_revision,
        url=None,
        editable_source=None,
    )
    assert comparison_branch(info) == expected


def test_comparison_branch_unknown_type() -> None:
    info = InstallInfo(
        install_type=InstallType.UNKNOWN,
        commit_id=None,
        requested_revision=None,
        url=None,
        editable_source=None,
    )
    assert comparison_branch(info) == "releases/latest"


def test_comparison_branch_develop() -> None:
    info = InstallInfo(
        install_type=InstallType.GIT_VCS,
        commit_id="abc123",
        requested_revision="develop",
        url=None,
        editable_source=None,
    )
    assert comparison_branch(info) == "develop"


@pytest.mark.parametrize(
    "install_type",
    [InstallType.LOCAL_EDITABLE, InstallType.LOCAL_PATH],
)
def test_comparison_branch_local_types_returns_none(install_type: InstallType) -> None:
    info = InstallInfo(
        install_type=install_type,
        commit_id=None,
        requested_revision=None,
        url=None,
        editable_source=Path("/tmp/repo") if install_type == InstallType.LOCAL_EDITABLE else None,
    )
    assert comparison_branch(info) is None


# ---------------------------------------------------------------------------
# dismissal_window — policy tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "requested_revision,install_type",
    [
        ("stable", InstallType.GIT_VCS),
        ("main", InstallType.GIT_VCS),
        ("v0.7.75", InstallType.GIT_VCS),
        (None, InstallType.UNKNOWN),
    ],
)
def test_dismissal_window_seven_days(
    requested_revision: str | None, install_type: InstallType
) -> None:
    info = InstallInfo(
        install_type=install_type,
        commit_id=None,
        requested_revision=requested_revision,
        url=None,
        editable_source=None,
    )
    assert dismissal_window(info) == timedelta(days=7)


@pytest.mark.parametrize(
    "requested_revision,install_type,editable_source",
    [
        ("develop", InstallType.GIT_VCS, None),
        (None, InstallType.LOCAL_EDITABLE, Path("/tmp/repo")),
    ],
)
def test_dismissal_window_twelve_hours(
    requested_revision: str | None,
    install_type: InstallType,
    editable_source: Path | None,
) -> None:
    info = InstallInfo(
        install_type=install_type,
        commit_id=None,
        requested_revision=requested_revision,
        url=None,
        editable_source=editable_source,
    )
    assert dismissal_window(info) == timedelta(hours=12)


# ---------------------------------------------------------------------------
# upgrade_command — policy tests
# ---------------------------------------------------------------------------


def test_upgrade_command_stable() -> None:
    info = InstallInfo(
        install_type=InstallType.GIT_VCS,
        commit_id="abc123",
        requested_revision="stable",
        url=None,
        editable_source=None,
    )
    assert upgrade_command(info) == ["uv", "tool", "upgrade", "autoskillit"]


def test_upgrade_command_main() -> None:
    info = InstallInfo(
        install_type=InstallType.GIT_VCS,
        commit_id="abc123",
        requested_revision="main",
        url=None,
        editable_source=None,
    )
    assert upgrade_command(info) == ["uv", "tool", "upgrade", "autoskillit"]


def test_upgrade_command_release_tag() -> None:
    info = InstallInfo(
        install_type=InstallType.GIT_VCS,
        commit_id="abc123",
        requested_revision="v0.7.75",
        url=None,
        editable_source=None,
    )
    assert upgrade_command(info) == ["uv", "tool", "upgrade", "autoskillit"]


def test_upgrade_command_develop() -> None:
    info = InstallInfo(
        install_type=InstallType.GIT_VCS,
        commit_id="abc123",
        requested_revision="develop",
        url=None,
        editable_source=None,
    )
    assert upgrade_command(info) == [
        "uv",
        "tool",
        "install",
        "--force",
        _INSTALL_FROM_DEVELOP,
    ]


def test_upgrade_command_local_editable() -> None:
    editable_source = Path("/home/user/autoskillit")
    info = InstallInfo(
        install_type=InstallType.LOCAL_EDITABLE,
        commit_id=None,
        requested_revision=None,
        url="file:///home/user/autoskillit",
        editable_source=editable_source,
    )
    assert upgrade_command(info) == ["uv", "pip", "install", "-e", str(editable_source)]


@pytest.mark.parametrize(
    "install_type",
    [InstallType.UNKNOWN, InstallType.LOCAL_PATH],
)
def test_upgrade_command_unknown_and_local_path_returns_none(
    install_type: InstallType,
) -> None:
    info = InstallInfo(
        install_type=install_type,
        commit_id=None,
        requested_revision=None,
        url=None,
        editable_source=None,
    )
    assert upgrade_command(info) is None


# ---------------------------------------------------------------------------
# _is_release_tag — unit tests (1a)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rev", ["v0.7.75", "0.7.75", "v1.0", "1.0.0.0"])
def test_is_release_tag_positive(rev: str) -> None:
    from autoskillit.core._install_detect import _is_release_tag

    assert _is_release_tag(rev) is True


@pytest.mark.parametrize("rev", ["develop", "main", "stable", "feature-foo", "v", "v0.7.75-rc1"])
def test_is_release_tag_negative(rev: str) -> None:
    from autoskillit.core._install_detect import _is_release_tag

    assert _is_release_tag(rev) is False


# ---------------------------------------------------------------------------
# _is_stable_track — unit tests (1b)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rev", [None, "main", "stable", "v0.7.75", "0.9.300", ""])
def test_is_stable_track_true(rev: str | None) -> None:
    from autoskillit.core._install_detect import _is_stable_track

    assert _is_stable_track(rev) is True


@pytest.mark.parametrize(
    "rev",
    ["develop", "integration", "feature-foo", "staging", "release-candidate", "develops"],
)
def test_is_stable_track_false(rev: str) -> None:
    from autoskillit.core._install_detect import _is_stable_track

    assert _is_stable_track(rev) is False


# ---------------------------------------------------------------------------
# classify_track — unit tests (1c)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "install_type,revision,expected_track",
    [
        (InstallType.LOCAL_EDITABLE, "develop", "local"),
        (InstallType.LOCAL_EDITABLE, None, "local"),
        (InstallType.LOCAL_PATH, "stable", "local"),
        (InstallType.LOCAL_PATH, None, "local"),
        (InstallType.GIT_VCS, "stable", "stable"),
        (InstallType.GIT_VCS, "main", "stable"),
        (InstallType.GIT_VCS, "v0.7.75", "stable"),
        (InstallType.GIT_VCS, None, "stable"),
        (InstallType.GIT_VCS, "develop", "dev"),
        (InstallType.GIT_VCS, "integration", "dev"),
        (InstallType.GIT_VCS, "feature-foo", "dev"),
        (InstallType.GIT_VCS, "staging", "dev"),
        (InstallType.GIT_VCS, "", "stable"),
        (InstallType.UNKNOWN, None, "stable"),
    ],
)
def test_classify_track(
    install_type: InstallType, revision: str | None, expected_track: str
) -> None:
    from autoskillit.cli._install_info import InstallTrack, classify_track

    info = InstallInfo(
        install_type=install_type,
        commit_id=None,
        requested_revision=revision,
        url=None,
        editable_source=Path("/tmp/repo") if install_type == InstallType.LOCAL_EDITABLE else None,
    )
    assert classify_track(info) == InstallTrack(expected_track)


# ---------------------------------------------------------------------------
# Policy functions with arbitrary branch names (1d)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "revision", ["integration", "feature-foo", "staging", "release-candidate", "my-branch"]
)
def test_comparison_branch_arbitrary_dev_revision(revision: str) -> None:
    info = InstallInfo(
        install_type=InstallType.GIT_VCS,
        commit_id="abc123",
        requested_revision=revision,
        url=None,
        editable_source=None,
    )
    assert comparison_branch(info) == "develop"


@pytest.mark.parametrize(
    "revision", ["integration", "feature-foo", "staging", "release-candidate", "my-branch"]
)
def test_dismissal_window_arbitrary_dev_revision(revision: str) -> None:
    info = InstallInfo(
        install_type=InstallType.GIT_VCS,
        commit_id="abc123",
        requested_revision=revision,
        url=None,
        editable_source=None,
    )
    assert dismissal_window(info) == timedelta(hours=12)


@pytest.mark.parametrize(
    "revision", ["integration", "feature-foo", "staging", "release-candidate", "my-branch"]
)
def test_upgrade_command_arbitrary_dev_revision(revision: str) -> None:
    info = InstallInfo(
        install_type=InstallType.GIT_VCS,
        commit_id="abc123",
        requested_revision=revision,
        url=None,
        editable_source=None,
    )
    result = upgrade_command(info)
    assert result is not None
    assert result[:3] == ["uv", "tool", "install"]
    assert "--force" in result
