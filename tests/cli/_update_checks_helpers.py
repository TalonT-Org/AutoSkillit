"""Shared factory helpers for update-checks test files."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from autoskillit.cli._install_info import InstallInfo, InstallType


def _make_stable_info(commit_id: str = "abc123", revision: str = "stable") -> InstallInfo:
    return InstallInfo(
        install_type=InstallType.GIT_VCS,
        commit_id=commit_id,
        requested_revision=revision,
        url="https://github.com/TalonT-Org/AutoSkillit.git",
        editable_source=None,
    )


def _make_develop_info(commit_id: str = "def456") -> InstallInfo:
    return InstallInfo(
        install_type=InstallType.GIT_VCS,
        commit_id=commit_id,
        requested_revision="develop",
        url="https://github.com/TalonT-Org/AutoSkillit.git",
        editable_source=None,
    )


def _make_mock_client(
    status_code: int = 200,
    json_body: dict[str, Any] | None = None,
    etag: str | None = None,
    raise_exc: Exception | None = None,
) -> MagicMock:
    """Build a MagicMock httpx.Client context manager for fetch cache tests."""
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = json_body or {}
    response.headers = {"ETag": etag} if etag else {}

    client_instance = MagicMock()
    if raise_exc is not None:
        client_instance.get.side_effect = raise_exc
    else:
        client_instance.get.return_value = response

    ctx_manager = MagicMock()
    ctx_manager.__enter__.return_value = client_instance
    ctx_manager.__exit__.return_value = False
    return ctx_manager
