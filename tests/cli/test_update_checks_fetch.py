"""Tests for cli/_update_checks.py — UC-9 fetch-cache regression coverage,
UC-11 fetch cache lifecycle, UC-12 state transitions, and T1/T2/T6 update
sequence and verification."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from autoskillit.cli._update_checks_fetch import (
    _fetch_with_cache,
)

from ._update_checks_helpers import (
    _make_develop_info,
    _make_mock_client,
    _make_stable_info,
)

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]

# ---------------------------------------------------------------------------
# UC-9 Fetch-cache regression coverage
# ---------------------------------------------------------------------------


def test_fetch_latest_version_uses_cache_within_ttl(tmp_path: Path) -> None:
    # Seed a cache entry that is fresh (1 second old, TTL = 30 min)
    import time

    from autoskillit.cli._update_checks import _fetch_latest_version
    from autoskillit.core import AUTOSKILLIT_INSTALLED_VERSION

    cache_data = {
        "https://api.github.com/repos/TalonT-Org/AutoSkillit/releases/latest": {
            "body": {"tag_name": "v0.9.0"},
            "etag": '"test-etag"',
            "cached_at": time.time() - 1,
            "installed_version": AUTOSKILLIT_INSTALLED_VERSION,
        }
    }
    (tmp_path / ".autoskillit").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".autoskillit" / "github_fetch_cache.json").write_text(
        json.dumps(cache_data), encoding="utf-8"
    )
    call_count = [0]

    class CountingClient:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            call_count[0] += 1
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, **kw):
            raise AssertionError("Should not hit network when cache is fresh")

    with patch("httpx.Client", CountingClient):
        result = _fetch_latest_version("releases/latest", tmp_path)

    assert result == "0.9.0"
    assert call_count[0] == 0


def test_fetch_cache_expires_after_ttl(tmp_path: Path) -> None:
    import time

    from autoskillit.cli._update_checks import _fetch_latest_version

    cache_data = {
        "https://api.github.com/repos/TalonT-Org/AutoSkillit/releases/latest": {
            "body": {"tag_name": "v0.8.0"},
            "etag": '"stale-etag"',
            "cached_at": time.time() - 3601,  # 1 hour + 1 second old
        }
    }
    (tmp_path / ".autoskillit").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".autoskillit" / "github_fetch_cache.json").write_text(
        json.dumps(cache_data), encoding="utf-8"
    )

    mock_client = _make_mock_client(
        status_code=200,
        json_body={"tag_name": "v0.9.0"},
        etag='"new-etag"',
    )
    with patch("httpx.Client", return_value=mock_client):
        result = _fetch_latest_version("releases/latest", tmp_path)

    assert result == "0.9.0"


def test_fetch_cache_respects_env_var_ttl(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import time

    from autoskillit.cli._update_checks import _fetch_latest_version

    # Entry is 61 seconds old — older than the custom 60s TTL
    cache_data = {
        "https://api.github.com/repos/TalonT-Org/AutoSkillit/releases/latest": {
            "body": {"tag_name": "v0.8.0"},
            "etag": '"stale-etag"',
            "cached_at": time.time() - 61,
        }
    }
    (tmp_path / ".autoskillit").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".autoskillit" / "github_fetch_cache.json").write_text(
        json.dumps(cache_data), encoding="utf-8"
    )
    monkeypatch.setenv("AUTOSKILLIT_FETCH_CACHE_TTL_SECONDS", "60")

    mock_client = _make_mock_client(
        status_code=200,
        json_body={"tag_name": "v0.9.0"},
    )
    with patch("httpx.Client", return_value=mock_client):
        result = _fetch_latest_version("releases/latest", tmp_path)

    assert result == "0.9.0"


def test_fetch_sends_github_token_auth_header(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    monkeypatch.setenv("GITHUB_TOKEN", "my-secret-token")
    (tmp_path / ".autoskillit").mkdir(parents=True, exist_ok=True)

    received_headers: dict = {}

    class CapturingClient:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, headers=None, **kw):
            received_headers.update(headers or {})
            r = MagicMock()
            r.status_code = 200
            r.json.return_value = {"tag_name": "v0.9.0"}
            r.headers = {}
            return r

    with patch("httpx.Client", CapturingClient):
        _fetch_with_cache(
            "https://api.github.com/repos/TalonT-Org/AutoSkillit/releases/latest",
            home=tmp_path,
        )

    assert "Authorization" in received_headers
    assert received_headers["Authorization"] == "Bearer my-secret-token"


def test_fetch_sends_if_none_match_when_cached_etag(tmp_path: Path) -> None:
    import time

    cache_data = {
        "https://api.github.com/repos/TalonT-Org/AutoSkillit/releases/latest": {
            "body": {"tag_name": "v0.8.0"},
            "etag": '"cached-etag"',
            "cached_at": time.time() - 3601,  # stale, so will hit network
        }
    }
    (tmp_path / ".autoskillit").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".autoskillit" / "github_fetch_cache.json").write_text(
        json.dumps(cache_data), encoding="utf-8"
    )

    received_headers: dict = {}

    class CapturingClient:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, headers=None, **kw):
            received_headers.update(headers or {})
            r = MagicMock()
            r.status_code = 200
            r.json.return_value = {"tag_name": "v0.9.0"}
            r.headers = {}
            return r

    with patch("httpx.Client", CapturingClient):
        _fetch_with_cache(
            "https://api.github.com/repos/TalonT-Org/AutoSkillit/releases/latest",
            home=tmp_path,
        )

    assert received_headers.get("If-None-Match") == '"cached-etag"'


def test_fetch_304_response_returns_cached_payload(tmp_path: Path) -> None:
    import time

    url = "https://api.github.com/repos/TalonT-Org/AutoSkillit/releases/latest"
    cache_data = {
        url: {
            "body": {"tag_name": "v0.8.5"},
            "etag": '"my-etag"',
            "cached_at": time.time() - 3601,  # stale
        }
    }
    (tmp_path / ".autoskillit").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".autoskillit" / "github_fetch_cache.json").write_text(
        json.dumps(cache_data), encoding="utf-8"
    )

    mock_client = _make_mock_client(status_code=304)
    with patch("httpx.Client", return_value=mock_client):
        result = _fetch_with_cache(url, home=tmp_path)

    assert result == {"tag_name": "v0.8.5"}


def test_fetch_uses_correct_timeout(tmp_path: Path) -> None:
    from autoskillit.cli._update_checks_fetch import _HTTP_TIMEOUT

    assert _HTTP_TIMEOUT.connect == 2.0
    assert _HTTP_TIMEOUT.read == 1.0
    assert _HTTP_TIMEOUT.write == 5.0
    assert _HTTP_TIMEOUT.pool == 1.0


def test_fetch_sends_modern_github_api_version_header(tmp_path: Path) -> None:
    (tmp_path / ".autoskillit").mkdir(parents=True, exist_ok=True)

    received_headers: dict = {}

    class CapturingClient:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, headers=None, **kw):
            received_headers.update(headers or {})
            r = MagicMock()
            r.status_code = 200
            r.json.return_value = {"tag_name": "v0.9.0"}
            r.headers = {}
            return r

    with patch("httpx.Client", CapturingClient):
        _fetch_with_cache(
            "https://api.github.com/repos/TalonT-Org/AutoSkillit/releases/latest",
            home=tmp_path,
        )

    assert received_headers.get("X-GitHub-Api-Version") == "2022-11-28"
    assert received_headers.get("Accept") == "application/vnd.github+json"


def test_fetch_sends_user_agent_with_package_version(tmp_path: Path) -> None:
    (tmp_path / ".autoskillit").mkdir(parents=True, exist_ok=True)

    received_headers: dict = {}

    class CapturingClient:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, headers=None, **kw):
            received_headers.update(headers or {})
            r = MagicMock()
            r.status_code = 200
            r.json.return_value = {}
            r.headers = {}
            return r

    with patch("httpx.Client", CapturingClient):
        _fetch_with_cache(
            "https://api.github.com/repos/TalonT-Org/AutoSkillit/releases/latest",
            home=tmp_path,
        )

    assert received_headers.get("User-Agent", "").startswith("autoskillit/")


def test_fetch_scrubs_authorization_header_from_logged_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    monkeypatch.setenv("GITHUB_TOKEN", "super-secret-token-xyz")
    (tmp_path / ".autoskillit").mkdir(parents=True, exist_ok=True)

    import httpx as _httpx

    class FailingClient:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, headers=None, **kw):
            raise _httpx.ConnectError("Connection refused [super-secret-token-xyz]")

    with caplog.at_level(logging.DEBUG, logger="autoskillit"):
        with patch("httpx.Client", FailingClient):
            result = _fetch_with_cache(
                "https://api.github.com/repos/TalonT-Org/AutoSkillit/releases/latest",
                home=tmp_path,
            )

    assert result is None
    # The token must not appear in any log record
    for record in caplog.records:
        assert "super-secret-token-xyz" not in record.getMessage()


def test_fetch_fails_fast_offline(tmp_path: Path) -> None:
    import httpx as _httpx

    from autoskillit.cli._update_checks import _fetch_latest_version

    (tmp_path / ".autoskillit").mkdir(parents=True, exist_ok=True)

    class OfflineClient:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, **kw):
            raise _httpx.ConnectError("Network unreachable")

    with patch("httpx.Client", OfflineClient):
        result = _fetch_latest_version("releases/latest", tmp_path)

    assert result is None


# ---------------------------------------------------------------------------
# UC-11 Fetch cache lifecycle — version-epoch and invalidation
# ---------------------------------------------------------------------------


def test_stale_fetch_cache_after_install_detected_by_epoch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Cache entry with stale installed_version is treated as a miss even within TTL."""
    import time

    from autoskillit.core._type_constants import (
        AUTOSKILLIT_INSTALLED_VERSION as _REAL_VERSION,
    )

    old_version = "0.0.0-stale"
    assert old_version != _REAL_VERSION

    url = "https://api.github.com/repos/TalonT-Org/AutoSkillit/releases/latest"
    cache_data = {
        url: {
            "body": {"tag_name": "v0.9.170"},
            "etag": '"old-etag"',
            "cached_at": time.time() - 1,
            "installed_version": old_version,
        }
    }
    (tmp_path / ".autoskillit").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".autoskillit" / "github_fetch_cache.json").write_text(
        json.dumps(cache_data), encoding="utf-8"
    )

    network_hit = [False]

    class TrackingClient:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, **kw):
            network_hit[0] = True
            r = MagicMock()
            r.status_code = 200
            r.json.return_value = {"tag_name": "v0.9.175"}
            r.headers = {"ETag": '"new-etag"'}
            return r

    with patch("httpx.Client", TrackingClient):
        result = _fetch_with_cache(url, home=tmp_path)

    assert network_hit[0], "Epoch mismatch must force a network fetch"
    assert result == {"tag_name": "v0.9.175"}


def test_stale_fetch_cache_after_install_resolve_reference_sha_path2(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """PATH 2 (no source repo, fallback to _api_sha): stale epoch forces fresh fetch."""
    import time

    from autoskillit.cli._update_checks import resolve_reference_sha

    url = "https://api.github.com/repos/TalonT-Org/AutoSkillit/git/refs/heads/develop"
    stale_sha = "a" * 40
    fresh_sha = "b" * 40

    cache_data = {
        url: {
            "body": {
                "object": {"sha": stale_sha, "type": "commit"},
                "ref": "refs/heads/develop",
            },
            "etag": '"old-etag"',
            "cached_at": time.time() - 1,
            "installed_version": "0.9.170",
        }
    }
    (tmp_path / ".autoskillit").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".autoskillit" / "github_fetch_cache.json").write_text(
        json.dumps(cache_data), encoding="utf-8"
    )

    monkeypatch.setattr("autoskillit.cli._update_checks_source.find_source_repo", lambda: None)

    class FreshClient:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, **kw):
            r = MagicMock()
            r.status_code = 200
            r.json.return_value = {
                "object": {"sha": fresh_sha, "type": "commit"},
                "ref": "refs/heads/develop",
            }
            r.headers = {"ETag": '"fresh-etag"'}
            return r

    info = _make_develop_info(commit_id=stale_sha)
    with patch("httpx.Client", FreshClient):
        result = resolve_reference_sha(info, tmp_path)

    assert result == fresh_sha, f"Expected fresh SHA {fresh_sha!r}, got {result!r}"


def test_run_update_sequence_invalidates_fetch_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """_run_update_sequence must delete github_fetch_cache.json on success."""
    from autoskillit.cli._update_checks import _run_update_sequence

    cache_file = tmp_path / ".autoskillit" / "github_fetch_cache.json"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text('{"some": "data"}', encoding="utf-8")

    class FakeTG:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    info = _make_stable_info()
    upgrade_ok = subprocess.CompletedProcess([], returncode=0)
    install_ok = subprocess.CompletedProcess([], returncode=0)
    calls = iter([upgrade_ok, install_ok])
    monkeypatch.setattr(
        "autoskillit.cli._update_checks.subprocess.run", lambda *a, **kw: next(calls)
    )
    monkeypatch.setattr("autoskillit.cli._update_checks.terminal_guard", FakeTG)
    monkeypatch.setattr(
        "autoskillit.cli._update_checks._fetch_latest_version", lambda *a, **kw: "0.9.1"
    )
    monkeypatch.setattr(
        "autoskillit.cli._update_checks._verify_update_result", lambda *a, **kw: True
    )
    monkeypatch.setattr("autoskillit.cli._update_checks.perform_restart", lambda: None)
    _run_update_sequence(info, "0.9.0", tmp_path, {}, {})
    assert not cache_file.exists(), "Fetch cache must be deleted after successful update"


def test_run_update_command_invalidates_fetch_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """run_update_command must delete github_fetch_cache.json on success."""
    from autoskillit.cli._update import run_update_command

    cache_file = tmp_path / ".autoskillit" / "github_fetch_cache.json"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text('{"some": "data"}', encoding="utf-8")

    class FakeTG:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    info = _make_stable_info()
    monkeypatch.setattr("autoskillit.cli._update.detect_install", lambda: info)
    monkeypatch.setattr("autoskillit.cli._update.terminal_guard", FakeTG)
    monkeypatch.setattr("autoskillit.core.any_kitchen_open", lambda **kw: False)

    upgrade_ok = subprocess.CompletedProcess([], returncode=0)
    install_ok = subprocess.CompletedProcess([], returncode=0)
    mock_run = MagicMock(side_effect=[upgrade_ok, install_ok])
    monkeypatch.setattr("autoskillit.cli._update.subprocess.run", mock_run)

    import autoskillit as _pkg

    monkeypatch.setattr(_pkg, "__version__", "0.9.0")

    import importlib.metadata

    monkeypatch.setattr(importlib.metadata, "version", lambda _: "0.9.1")
    monkeypatch.setattr(
        "autoskillit.cli._update_checks._fetch_latest_version", lambda *a, **kw: "0.9.1"
    )
    monkeypatch.setattr("autoskillit.cli._update.perform_restart", lambda: None)

    run_update_command(home=tmp_path)
    assert not cache_file.exists(), "Fetch cache must be deleted after successful update command"


def test_install_invalidates_fetch_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """_marketplace.install() must delete github_fetch_cache.json after install."""
    import importlib

    _app_mod = importlib.import_module("autoskillit.cli._marketplace")

    cache_file = tmp_path / ".autoskillit" / "github_fetch_cache.json"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text('{"some": "data"}', encoding="utf-8")

    monkeypatch.setattr(_app_mod, "is_git_worktree", lambda path: False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/claude")
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})(),
    )
    monkeypatch.setattr("autoskillit.cli._marketplace.evict_direct_mcp_entry", lambda _: False)
    monkeypatch.setattr(
        "autoskillit.cli._marketplace.sweep_all_scopes_for_orphans", lambda _: None
    )
    monkeypatch.setattr("autoskillit.cli._marketplace.sync_hooks_to_settings", lambda _: None)
    monkeypatch.setattr("autoskillit.cli._marketplace.generate_hooks_json", lambda: {})
    monkeypatch.setattr("autoskillit.cli._marketplace.atomic_write", lambda *a, **kw: None)

    from autoskillit.cli._marketplace import install as _install

    _install(scope="user")
    assert not cache_file.exists(), "Fetch cache must be deleted after plugin install"


def test_api_sha_with_seeded_cache_returns_cached_sha(tmp_path: Path) -> None:
    """_api_sha returns cached SHA when cache epoch matches current version."""
    import time

    from autoskillit.cli._update_checks_source import _api_sha
    from autoskillit.core import AUTOSKILLIT_INSTALLED_VERSION

    sha = "c" * 40
    url = "https://api.github.com/repos/TalonT-Org/AutoSkillit/git/refs/heads/develop"
    cache_data = {
        url: {
            "body": {"object": {"sha": sha, "type": "commit"}, "ref": "refs/heads/develop"},
            "etag": '"test-etag"',
            "cached_at": time.time() - 1,
            "installed_version": AUTOSKILLIT_INSTALLED_VERSION,
        }
    }
    (tmp_path / ".autoskillit").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".autoskillit" / "github_fetch_cache.json").write_text(
        json.dumps(cache_data), encoding="utf-8"
    )

    class NoNetworkClient:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, **kw):
            raise AssertionError("Should not hit network when epoch matches")

    with patch("httpx.Client", NoNetworkClient):
        result = _api_sha("develop", tmp_path)

    assert result == sha


def test_api_sha_with_stale_epoch_forces_network(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """_api_sha issues a network request when cache epoch is stale."""
    import time

    from autoskillit.cli._update_checks_source import _api_sha

    stale_sha = "d" * 40
    fresh_sha = "e" * 40
    url = "https://api.github.com/repos/TalonT-Org/AutoSkillit/git/refs/heads/develop"
    cache_data = {
        url: {
            "body": {
                "object": {"sha": stale_sha, "type": "commit"},
                "ref": "refs/heads/develop",
            },
            "etag": '"old-etag"',
            "cached_at": time.time() - 1,
            "installed_version": "0.9.170",
        }
    }
    (tmp_path / ".autoskillit").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".autoskillit" / "github_fetch_cache.json").write_text(
        json.dumps(cache_data), encoding="utf-8"
    )

    network_hit = [False]

    class FreshClient:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, **kw):
            network_hit[0] = True
            r = MagicMock()
            r.status_code = 200
            r.json.return_value = {
                "object": {"sha": fresh_sha, "type": "commit"},
                "ref": "refs/heads/develop",
            }
            r.headers = {"ETag": '"fresh-etag"'}
            return r

    with patch("httpx.Client", FreshClient):
        result = _api_sha("develop", tmp_path)

    assert network_hit[0], "Stale epoch must force network fetch"
    assert result == fresh_sha


def test_api_sha_network_false_reads_raw_cache_no_epoch(tmp_path: Path) -> None:
    """_api_sha(network=False) reads raw cache regardless of epoch (doctor mode)."""
    import time

    from autoskillit.cli._update_checks_source import _api_sha

    sha = "f" * 40
    url = "https://api.github.com/repos/TalonT-Org/AutoSkillit/git/refs/heads/develop"
    cache_data = {
        url: {
            "body": {"object": {"sha": sha, "type": "commit"}, "ref": "refs/heads/develop"},
            "etag": '"cached-etag"',
            "cached_at": time.time() - 1,
            "installed_version": "0.0.0",
        }
    }
    (tmp_path / ".autoskillit").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".autoskillit" / "github_fetch_cache.json").write_text(
        json.dumps(cache_data), encoding="utf-8"
    )

    result = _api_sha("develop", tmp_path, network=False)
    assert result == sha, "Doctor mode must read cache body regardless of epoch"


def test_api_sha_tags_url_prefix(tmp_path: Path) -> None:
    """_api_sha('v0.9.174', ...) constructs a refs/tags/ URL."""
    from autoskillit.cli._update_checks_source import _api_sha

    (tmp_path / ".autoskillit").mkdir(parents=True, exist_ok=True)
    captured_urls: list[str] = []

    class UrlCapturingClient:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, **kw):
            captured_urls.append(url)
            r = MagicMock()
            r.status_code = 200
            r.json.return_value = {"object": {"sha": "a" * 40, "type": "commit"}}
            r.headers = {}
            return r

    with patch("httpx.Client", UrlCapturingClient):
        _api_sha("v0.9.174", tmp_path)

    assert len(captured_urls) == 1
    assert "refs/tags/v0.9.174" in captured_urls[0]


# ---------------------------------------------------------------------------
# UC-12 State transitions — cross-hemisphere lifecycle tests
# ---------------------------------------------------------------------------


def test_full_lifecycle_install_clears_stale_cache_then_check_detects_new_version(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Full lifecycle: install invalidates cache, next binary_signal detects new version."""
    import time

    from autoskillit.cli._update_checks import _binary_signal, invalidate_fetch_cache
    from autoskillit.core import AUTOSKILLIT_INSTALLED_VERSION

    stale_version = "0.0.0-stale"
    newer_version = "99.99.99"

    url = "https://api.github.com/repos/TalonT-Org/AutoSkillit/releases/latest"
    cache_data = {
        url: {
            "body": {"tag_name": f"v{stale_version}"},
            "etag": '"old-etag"',
            "cached_at": time.time(),
            "installed_version": stale_version,
        },
        "https://api.github.com/repos/TalonT-Org/AutoSkillit/git/refs/heads/develop": {
            "body": {"object": {"sha": "a" * 40}},
            "etag": '"ref-etag"',
            "cached_at": time.time(),
            "installed_version": stale_version,
        },
    }
    cache_file = tmp_path / ".autoskillit" / "github_fetch_cache.json"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(cache_data), encoding="utf-8")

    invalidate_fetch_cache(tmp_path)
    assert not cache_file.exists(), "invalidate must remove cache file"

    mock_client = _make_mock_client(
        status_code=200,
        json_body={"tag_name": f"v{newer_version}"},
        etag='"new-etag"',
    )
    info = _make_stable_info()
    with patch("httpx.Client", return_value=mock_client):
        signal = _binary_signal(info, tmp_path, AUTOSKILLIT_INSTALLED_VERSION)

    assert signal is not None, "Binary signal must fire after cache invalidation"
    assert newer_version in signal.message


@pytest.mark.parametrize(
    "entry_kwargs,expect_hit",
    [
        pytest.param(
            {"installed_version": "_CURRENT_"},
            True,
            id="matching-epoch-fresh-ttl",
        ),
        pytest.param(
            {"installed_version": "0.0.1"},
            False,
            id="mismatched-epoch-fresh-ttl",
        ),
        pytest.param(
            {},
            False,
            id="missing-epoch-fresh-ttl",
        ),
        pytest.param(
            {"installed_version": "_CURRENT_", "cached_at_offset": -3601},
            False,
            id="matching-epoch-expired-ttl",
        ),
    ],
)
def test_fetch_with_cache_epoch_check_contract(
    tmp_path: Path,
    entry_kwargs: dict,
    expect_hit: bool,
) -> None:
    """Parametrized contract: epoch + TTL together determine cache hit/miss."""
    import time

    from autoskillit.core import AUTOSKILLIT_INSTALLED_VERSION

    entry_kwargs = dict(entry_kwargs)
    cached_at_offset = entry_kwargs.pop("cached_at_offset", -1)
    url = "https://api.github.com/repos/TalonT-Org/AutoSkillit/releases/latest"
    entry: dict[str, Any] = {
        "body": {"tag_name": "v0.8.0"},
        "etag": '"test-etag"',
        "cached_at": time.time() + cached_at_offset,
    }
    for k, v in entry_kwargs.items():
        entry[k] = AUTOSKILLIT_INSTALLED_VERSION if v == "_CURRENT_" else v

    cache_data = {url: entry}
    (tmp_path / ".autoskillit").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".autoskillit" / "github_fetch_cache.json").write_text(
        json.dumps(cache_data), encoding="utf-8"
    )

    network_hit = [False]

    class DetectingClient:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, **kw):
            network_hit[0] = True
            r = MagicMock()
            r.status_code = 200
            r.json.return_value = {"tag_name": "v0.9.0"}
            r.headers = {"ETag": '"fresh-etag"'}
            return r

    with patch("httpx.Client", DetectingClient):
        result = _fetch_with_cache(url, home=tmp_path)

    if expect_hit:
        assert not network_hit[0], "Expected cache hit but network was called"
        assert result == {"tag_name": "v0.8.0"}
    else:
        assert network_hit[0], "Expected cache miss but network was not called"


# ---------------------------------------------------------------------------
# T1 — _verify_update_result uses install-type-aware upgrade command
# ---------------------------------------------------------------------------


def test_verify_update_result_prints_git_vcs_stable_command(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    import importlib.metadata

    from autoskillit.cli._update_checks import _verify_update_result

    info = _make_stable_info()
    with patch.object(importlib.metadata, "version", return_value="0.9.0"):
        result = _verify_update_result(info, "0.9.0", "0.9.1", tmp_path, {})
    assert result is False
    out = capsys.readouterr().out
    assert "uv tool upgrade autoskillit" in out
    assert "autoskillit update" in out


def test_verify_update_result_prints_git_vcs_develop_command(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    import importlib.metadata

    from autoskillit.cli._update_checks import _verify_update_result

    info = _make_develop_info()
    with patch.object(importlib.metadata, "version", return_value="0.9.0"):
        result = _verify_update_result(info, "0.9.0", "0.9.1", tmp_path, {})
    assert result is False
    out = capsys.readouterr().out
    assert "git+" in out
    assert "uv tool upgrade autoskillit" not in out


# ---------------------------------------------------------------------------
# T2 — _run_update_sequence warns when autoskillit install exits non-zero
# ---------------------------------------------------------------------------


def test_run_update_sequence_warns_on_install_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    from autoskillit.cli._update_checks import _run_update_sequence

    class FakeTG:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    info = _make_stable_info()
    upgrade_ok = subprocess.CompletedProcess([], returncode=0)
    install_fail = subprocess.CompletedProcess([], returncode=1)
    calls = iter([upgrade_ok, install_fail])
    monkeypatch.setattr(
        "autoskillit.cli._update_checks.subprocess.run", lambda *a, **kw: next(calls)
    )
    monkeypatch.setattr("autoskillit.cli._update_checks.terminal_guard", FakeTG)
    monkeypatch.setattr(
        "autoskillit.cli._update_checks._fetch_latest_version", lambda *a, **kw: "0.9.1"
    )
    monkeypatch.setattr(
        "autoskillit.cli._update_checks._verify_update_result", lambda *a, **kw: True
    )
    monkeypatch.setattr("autoskillit.cli._update_checks.perform_restart", lambda: None)
    _run_update_sequence(info, "0.9.0", tmp_path, {}, {})
    out = capsys.readouterr().out
    assert "autoskillit install" in out
    assert "stale" in out.lower()


# ---------------------------------------------------------------------------
# T6 — binary_snoozed is never written by _verify_update_result
# ---------------------------------------------------------------------------


def test_run_update_sequence_restarts_on_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """After a successful upgrade, _run_update_sequence must call perform_restart."""
    from autoskillit.cli._update_checks import _run_update_sequence

    info = _make_stable_info()
    upgrade_ok = subprocess.CompletedProcess([], returncode=0)
    install_ok = subprocess.CompletedProcess([], returncode=0)
    calls = iter([upgrade_ok, install_ok])
    monkeypatch.setattr(
        "autoskillit.cli._update_checks.subprocess.run", lambda *a, **kw: next(calls)
    )

    class FakeTG:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr("autoskillit.cli._update_checks.terminal_guard", FakeTG)
    monkeypatch.setattr(
        "autoskillit.cli._update_checks._fetch_latest_version", lambda *a, **kw: "0.9.1"
    )
    monkeypatch.setattr(
        "autoskillit.cli._update_checks._verify_update_result", lambda *a, **kw: True
    )

    restart_called: list[bool] = []
    monkeypatch.setattr(
        "autoskillit.cli._update_checks.perform_restart", lambda: restart_called.append(True)
    )

    _run_update_sequence(info, "0.9.0", tmp_path, {}, {})
    assert restart_called, (
        "_run_update_sequence must call perform_restart() after successful upgrade"
    )


def test_verify_update_result_does_not_write_binary_snoozed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import importlib.metadata

    from autoskillit.cli._update_checks import _verify_update_result

    info = _make_stable_info(commit_id="abc")
    state: dict = {}
    with patch.object(importlib.metadata, "version", return_value="0.9.0"):
        _verify_update_result(info, "0.9.0", "0.9.1", tmp_path, state)
    assert "binary_snoozed" not in state
