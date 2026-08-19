"""Tests for cli/_update_checks.py — UC-11 fetch cache lifecycle, UC-12 state
transitions, and T2/T6 automatic update sequencing."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from autoskillit import __version__
from autoskillit.cli.install._install_contract import InstallMode, InstallRequest
from autoskillit.cli.update._transaction import (
    UpdateTransactionOutcome,
    UpdateTransactionResult,
)
from autoskillit.cli.update._update_checks_fetch import (
    _fetch_with_cache,
)

from ._update_checks_helpers import (
    _make_develop_info,
    _make_mock_client,
    _make_stable_info,
)

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


def _direct_request() -> InstallRequest:
    return InstallRequest(
        scope="user",
        mode=InstallMode.DIRECT,
        require_registered_plugin=True,
        expected_version=__version__,
    )


# ---------------------------------------------------------------------------
# UC-11 Fetch cache lifecycle — version-epoch and invalidation
# ---------------------------------------------------------------------------


def test_stale_fetch_cache_after_install_detected_by_epoch(
    tmp_path: Path,
) -> None:
    """Cache entry with stale installed_version is treated as a miss even within TTL."""
    import time

    from autoskillit.core.types._type_constants_env import (
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

    from autoskillit.cli.update._update_checks import resolve_reference_sha

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

    monkeypatch.setattr(
        "autoskillit.cli.update._update_checks_source.find_source_repo", lambda: None
    )

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
    from autoskillit.cli.update._transaction import (
        UpdateTransactionOutcome,
        UpdateTransactionResult,
    )
    from autoskillit.cli.update._update_checks import _run_update_sequence

    cache_file = tmp_path / ".autoskillit" / "github_fetch_cache.json"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text('{"some": "data"}', encoding="utf-8")

    monkeypatch.setattr(
        "autoskillit.cli.update._update_checks.run_update_transaction",
        lambda **kwargs: UpdateTransactionResult(
            outcome=UpdateTransactionOutcome.COMPLETED,
            expected_version="0.9.1",
        ),
    )
    monkeypatch.setattr("autoskillit.cli.update._update_checks.perform_restart", lambda: None)
    _run_update_sequence(tmp_path, {})
    assert not cache_file.exists(), "Fetch cache must be deleted after successful update"


def test_run_update_command_invalidates_fetch_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """run_update_command must delete github_fetch_cache.json on success."""
    from autoskillit.cli.update._update import run_update_command

    cache_file = tmp_path / ".autoskillit" / "github_fetch_cache.json"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text('{"some": "data"}', encoding="utf-8")

    from autoskillit.cli.update._transaction import (
        UpdateTransactionOutcome,
        UpdateTransactionResult,
    )

    monkeypatch.setattr(
        "autoskillit.cli.update._update.run_update_transaction",
        lambda **kwargs: UpdateTransactionResult(
            outcome=UpdateTransactionOutcome.COMPLETED,
            expected_version="0.9.1",
        ),
    )
    monkeypatch.setattr("autoskillit.cli.update._update.perform_restart", lambda: None)

    run_update_command(home=tmp_path)
    assert not cache_file.exists(), "Fetch cache must be deleted after successful update command"


def test_install_invalidates_fetch_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """_marketplace.install() must delete github_fetch_cache.json after install."""
    import importlib
    from types import SimpleNamespace

    _app_mod = importlib.import_module("autoskillit.cli.install._marketplace")

    cache_file = tmp_path / ".autoskillit" / "github_fetch_cache.json"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text('{"some": "data"}', encoding="utf-8")

    monkeypatch.setattr(_app_mod, "is_git_worktree", lambda path: False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)

    from autoskillit import __version__
    from autoskillit.core import PluginArtifactIdentity, new_plugin_artifact_incarnation_id

    incarnation_id = new_plugin_artifact_incarnation_id()
    gen_dir = (
        tmp_path
        / ".autoskillit"
        / "plugin-generations"
        / "autoskillit"
        / __version__
        / incarnation_id
    )
    gen_dir.mkdir(parents=True, exist_ok=True)
    fake_identity = PluginArtifactIdentity(
        semantic_key=f"autoskillit@autoskillit-local:{__version__}",
        incarnation_id=incarnation_id,
        manifest_schema_version=1,
        artifact_digest="a" * 64,
        managed_path=gen_dir,
        manifest_path=gen_dir.parent / f".{incarnation_id}.autoskillit-artifact.json",
    )

    monkeypatch.setattr(
        "autoskillit.cli.install._marketplace.evict_direct_mcp_entry", lambda _: False
    )
    monkeypatch.setattr("autoskillit.cli._hooks._evict_stale_autoskillit_hooks", lambda _: None)
    monkeypatch.setattr(
        "autoskillit.cli.install._marketplace._ensure_marketplace", lambda **_kw: None
    )
    monkeypatch.setattr("autoskillit.workspace.reconcile_install_artifacts", lambda: ())
    monkeypatch.setattr(
        "autoskillit.workspace.publish_generation",
        lambda **_kw: fake_identity,
    )
    monkeypatch.setattr(
        "autoskillit.core.read_installed_plugin_artifact_identity",
        lambda *_a, **_kw: fake_identity,
    )

    backend = SimpleNamespace(capabilities=SimpleNamespace(plugin_install_capable=True))
    config = SimpleNamespace(agent_backend=SimpleNamespace(backend="claude-code"))
    monkeypatch.setattr("autoskillit.config.load_config", lambda _path: config)
    monkeypatch.setattr("autoskillit.execution.get_backend", lambda _name: backend)
    monkeypatch.setattr(_app_mod, "_ensure_workspace_ready", lambda **_kw: None)

    from autoskillit.cli.install._marketplace import install as _install

    _install(request=_direct_request())
    assert not cache_file.exists(), "Fetch cache must be deleted after plugin install"


def test_api_sha_with_seeded_cache_returns_cached_sha(tmp_path: Path) -> None:
    """_api_sha returns cached SHA when cache epoch matches current version."""
    import time

    from autoskillit.cli.update._update_checks_source import _api_sha
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

    from autoskillit.cli.update._update_checks_source import _api_sha

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

    from autoskillit.cli.update._update_checks_source import _api_sha

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
    from autoskillit.cli.update._update_checks_source import _api_sha

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

    from autoskillit.cli.update._update_checks import _binary_signal, invalidate_fetch_cache
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
    entry_kwargs: dict[str, Any],
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
        assert result == {"tag_name": "v0.9.0"}


# ---------------------------------------------------------------------------
# T2 — automatic adapter suppresses completed-only effects on every non-success
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "outcome",
    [
        pytest.param(outcome, id=outcome.value)
        for outcome in UpdateTransactionOutcome
        if outcome is not UpdateTransactionOutcome.COMPLETED
    ],
)
def test_run_update_sequence_has_no_completed_only_effects_for_every_noncompleted_outcome(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    outcome: UpdateTransactionOutcome,
) -> None:
    from autoskillit.cli.update._update_checks import _run_update_sequence

    monkeypatch.setattr(
        "autoskillit.cli.update._update_checks.run_update_transaction",
        lambda **kwargs: UpdateTransactionResult(
            outcome=outcome,
            expected_version="0.9.1",
        ),
    )
    effects: list[str] = []
    monkeypatch.setattr(
        "autoskillit.cli.update._update_checks._write_dismiss_state",
        lambda *_args: effects.append("write"),
    )
    monkeypatch.setattr(
        "autoskillit.cli.update._update_checks.invalidate_fetch_cache",
        lambda *_args: effects.append("invalidate"),
    )
    monkeypatch.setattr(
        "autoskillit.cli.update._update_checks.perform_restart",
        lambda: effects.append("restart"),
    )
    state = {
        "update_prompt": {"conditions": ["binary"]},
        "binary_snoozed": True,
        "preserved": "value",
    }
    _run_update_sequence(tmp_path, state)
    assert state == {
        "update_prompt": {"conditions": ["binary"]},
        "binary_snoozed": True,
        "preserved": "value",
    }
    assert effects == []
    assert "updated successfully" not in capsys.readouterr().out


def test_run_update_sequence_passes_home_and_fresh_process_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.cli.update import _update_checks

    captured: list[dict[str, object]] = []

    def transaction(**kwargs: object) -> UpdateTransactionResult:
        captured.append(kwargs)
        return UpdateTransactionResult(
            outcome=UpdateTransactionOutcome.FAILED_UPGRADE,
        )

    monkeypatch.setattr(_update_checks, "run_update_transaction", transaction)
    _update_checks._run_update_sequence(tmp_path, {})

    assert captured == [
        {
            "home": tmp_path,
            "process_runner": _update_checks.subprocess.run,
        }
    ]


# ---------------------------------------------------------------------------
# T6 — successful coordinator cleanup and restart
# ---------------------------------------------------------------------------


def test_run_update_sequence_restarts_on_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """After a successful upgrade, _run_update_sequence must call perform_restart."""
    from autoskillit.cli.update._update_checks import _run_update_sequence

    monkeypatch.setattr(
        "autoskillit.cli.update._update_checks.run_update_transaction",
        lambda **kwargs: UpdateTransactionResult(
            outcome=UpdateTransactionOutcome.COMPLETED,
            expected_version="0.9.1",
        ),
    )

    effects: list[str] = []
    written_states: list[dict[str, object]] = []

    def write_state(_home: Path, state: dict[str, object]) -> None:
        written_states.append(dict(state))
        effects.append("write")

    monkeypatch.setattr(
        "autoskillit.cli.update._update_checks._write_dismiss_state",
        write_state,
    )
    monkeypatch.setattr(
        "autoskillit.cli.update._update_checks.invalidate_fetch_cache",
        lambda *_args: effects.append("invalidate"),
    )
    monkeypatch.setattr(
        "autoskillit.cli.update._update_checks.perform_restart",
        lambda: effects.append("restart"),
    )

    state = {
        "update_prompt": {"conditions": ["binary"]},
        "binary_snoozed": True,
        "preserved": "value",
    }
    _run_update_sequence(tmp_path, state)
    assert state == {"preserved": "value"}
    assert written_states == [{"preserved": "value"}]
    assert effects == ["write", "invalidate", "restart"]
