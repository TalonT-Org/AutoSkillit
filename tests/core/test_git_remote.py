"""Tests for canonical git remote identity resolution."""

from __future__ import annotations

import errno
import subprocess
from typing import Literal

import pytest

from autoskillit.core import (
    GitHubRepositoryRef,
    RemoteIdentityProbe,
    RemoteIdentityResolution,
    parse_github_remote_url,
    resolve_repository_remote_identity_sync,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


@pytest.mark.parametrize(
    ("url", "owner", "repository", "transport"),
    [
        ("https://github.com/TalonT-Org/AutoSkillit.git", "TalonT-Org", "AutoSkillit", "https"),
        ("HTTPS://GITHUB.COM/Mixed/Case", "Mixed", "Case", "https"),
        ("ssh://git@github.com/TalonT-Org/AutoSkillit.git", "TalonT-Org", "AutoSkillit", "ssh"),
        ("git@github.com:TalonT-Org/AutoSkillit.git", "TalonT-Org", "AutoSkillit", "ssh"),
    ],
)
def test_parse_github_remote_url_preserves_display_identity(
    url: str,
    owner: str,
    repository: str,
    transport: Literal["https", "ssh"],
) -> None:
    parsed = parse_github_remote_url(url)

    assert parsed == GitHubRepositoryRef(owner, repository, transport)
    assert parsed.display_identity == f"github.com/{owner}/{repository}"
    assert parsed.normalized_identity == f"github.com/{owner.casefold()}/{repository.casefold()}"


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/owner/repo",
        "https://github.example/owner/repo",
        "https://github.com:443/owner/repo",
        "https://user@github.com/owner/repo",
        "https://github.com/owner/repo?query=1",
        "https://github.com/owner/repo#fragment",
        "https://github.com/owner/repo/extra",
        "https://github.com/owner%2frepo",
        "https://github.com/owner\\repo",
        "https://github.com//repo",
        "https://github.com/owner/.git",
        "git@github.com:owner/repo/extra",
        "git@github.com:owner%2frepo",
        "ssh://owner@github.com/owner/repo",
        "ssh://git:secret@github.com/owner/repo",
        "file:///owner/repo",
        "https://github.com.evil/owner/repo",
    ],
)
def test_parse_github_remote_url_rejects_ambiguous_boundaries(url: str) -> None:
    assert parse_github_remote_url(url) is None


def test_remote_identity_dataclasses_preserve_probe_payloads() -> None:
    repository = GitHubRepositoryRef("Owner", "Repo", "https")
    probe = RemoteIdentityProbe("upstream", "https://github.com/Owner/Repo", True, repository)
    resolution = RemoteIdentityResolution(
        "upstream",
        probe.url,
        repository,
        True,
        (probe,),
        ("github.com/other/fork",),
    )

    assert resolution.probes == (probe,)
    assert resolution.conflicting_github_identities == ("github.com/other/fork",)


def test_remote_resolution_preserves_precedence_conflicts_and_probe_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import autoskillit.core.git_remote as mod

    urls = {
        "remote.upstream.url": "https://github.com/Official/Project.git\n",
        "remote.origin.url": "https://github.com/Fork/Project.git\n",
    }

    def _run(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, stdout=urls[command[-1]], stderr="")

    monkeypatch.setattr(mod.subprocess, "run", _run)

    resolution = resolve_repository_remote_identity_sync(".")

    assert resolution.selected_remote == "upstream"
    assert resolution.selected_url == urls["remote.upstream.url"].strip()
    assert resolution.repository == GitHubRepositoryRef("Official", "Project", "https")
    assert resolution.conflicting_github_identities == ("github.com/fork/project",)
    assert tuple(probe.name for probe in resolution.probes) == ("upstream", "origin")


@pytest.mark.parametrize(
    ("upstream", "origin", "expected_selected", "expected_diagnostics"),
    [
        ("", "https://gitlab.com/owner/repo\n", "origin", ("empty", "non_github_remote")),
        (
            "https://github.com/one/repo\nhttps://github.com/two/repo\n",
            "file:///tmp/repo\n",
            "",
            ("multiple_urls", "clone_isolation_file_url"),
        ),
    ],
)
def test_remote_resolution_handles_non_github_and_unusable_remotes(
    monkeypatch: pytest.MonkeyPatch,
    upstream: str,
    origin: str,
    expected_selected: str,
    expected_diagnostics: tuple[str, str],
) -> None:
    import autoskillit.core.git_remote as mod

    urls = {"remote.upstream.url": upstream, "remote.origin.url": origin}

    def _run(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, stdout=urls[command[-1]], stderr="")

    monkeypatch.setattr(mod.subprocess, "run", _run)

    resolution = resolve_repository_remote_identity_sync(".")

    assert resolution.selected_remote == expected_selected
    assert resolution.usable_remote_found is bool(expected_selected)
    assert tuple(probe.diagnostic for probe in resolution.probes) == expected_diagnostics


def test_timeout_diagnostic_preserves_detail_and_remote_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import autoskillit.core.git_remote as mod

    def _run(command, **kwargs):
        if command[-1] == "remote.upstream.url":
            raise subprocess.TimeoutExpired(command, 15)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="https://github.com/TalonT-Org/AutoSkillit.git\n",
            stderr="",
        )

    monkeypatch.setattr(mod.subprocess, "run", _run)

    resolution = resolve_repository_remote_identity_sync(".")

    assert resolution.selected_remote == "origin"
    assert resolution.repository is not None
    timeout_diagnostic = resolution.probes[0].diagnostic
    assert timeout_diagnostic.startswith("timeout:TimeoutExpired:")
    assert "timed out after 15 seconds" in timeout_diagnostic
    assert len(timeout_diagnostic) <= 256


def test_os_error_diagnostic_is_sanitized_and_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import autoskillit.core.git_remote as mod

    def _run(*args, **kwargs):
        raise PermissionError(
            errno.EACCES,
            "permission\ndenied\tby policy " + ("x" * 512),
        )

    monkeypatch.setattr(mod.subprocess, "run", _run)

    resolution = resolve_repository_remote_identity_sync(".")

    assert not resolution.usable_remote_found
    diagnostic = resolution.probes[0].diagnostic
    assert diagnostic.startswith("os_error:PermissionError:errno=13:permission denied by policy ")
    assert "\n" not in diagnostic
    assert "\t" not in diagnostic
    assert len(diagnostic) == 256
    assert diagnostic.endswith("...")
