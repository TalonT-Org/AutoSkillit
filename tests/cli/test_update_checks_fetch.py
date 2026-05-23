"""Tests for cli/_update_checks.py — UC-9 fetch-cache regression coverage."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from autoskillit.cli.update._update_checks_fetch import (
    _fetch_with_cache,
)

from ._update_checks_helpers import (
    _make_mock_client,
)

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]

# ---------------------------------------------------------------------------
# UC-9 Fetch-cache regression coverage
# ---------------------------------------------------------------------------


def test_fetch_latest_version_uses_cache_within_ttl(tmp_path: Path) -> None:
    # Seed a cache entry that is fresh (1 second old, TTL = 30 min)
    import time

    from autoskillit.cli.update._update_checks import _fetch_latest_version
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

    from autoskillit.cli.update._update_checks import _fetch_latest_version

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

    from autoskillit.cli.update._update_checks import _fetch_latest_version

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
    from autoskillit.cli.update._update_checks_fetch import _HTTP_TIMEOUT

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

    from autoskillit.cli.update._update_checks import _fetch_latest_version

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
# Step 1e — _fetch_latest_version contract: target routing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "target,expected_url_fragment",
    [
        ("develop", "contents/pyproject.toml"),
        ("releases/latest", "releases/latest"),
    ],
)
def test_fetch_latest_version_routes_by_target(
    target: str, expected_url_fragment: str, tmp_path: Path
) -> None:
    from autoskillit.cli.update._update_checks import _fetch_latest_version

    fetched_urls: list[str] = []

    def _mock_fetch(url: str, *, home: Path) -> dict | None:
        fetched_urls.append(url)
        if "pyproject.toml" in url:
            import base64

            content = base64.b64encode(b'version = "0.9.300"\n').decode()
            return {"content": content}
        return {"tag_name": "v0.9.300"}

    with patch(
        "autoskillit.cli.update._update_checks_fetch._fetch_with_cache", side_effect=_mock_fetch
    ):
        result = _fetch_latest_version(target, tmp_path)

    assert result is not None
    assert fetched_urls, "Expected _fetch_with_cache to be called"
    assert any(expected_url_fragment in url for url in fetched_urls), (
        f"Expected URL containing '{expected_url_fragment}', got: {fetched_urls}"
    )
