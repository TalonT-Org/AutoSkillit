"""Tests for quota and output-budget hook-config snapshots."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.medium]

_ENV_VARS = (
    "AUTOSKILLIT_QUOTA_GUARD__CACHE_PATH",
    "AUTOSKILLIT_QUOTA_GUARD__CACHE_MAX_AGE",
    "AUTOSKILLIT_QUOTA_GUARD__DISABLED",
)


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def _write_hook_config(tmp_path: Path, quota_guard: dict) -> None:
    hook_cfg = tmp_path / ".autoskillit" / "temp" / ".hook_config.json"
    hook_cfg.parent.mkdir(parents=True, exist_ok=True)
    hook_cfg.write_text(json.dumps({"quota_guard": quota_guard}))


def _write_blocking_cache(cache_path: Path) -> None:
    cache_path.write_text(
        json.dumps(
            {
                "fetched_at": datetime.now(UTC).isoformat(),
                "credential_scope": "",
                "binding": {
                    "utilization": 95.0,
                    "should_block": True,
                    "effective_threshold": 85.0,
                    "window_name": "five_hour",
                    "resets_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
                },
            }
        )
    )


def _run_quota_hook() -> tuple[str, int]:
    from autoskillit.hooks.guards.quota_guard import main

    stdout = io.StringIO()
    with patch("sys.stdin", io.StringIO(json.dumps({"tool_name": "run_skill"}))):
        with redirect_stdout(stdout), pytest.raises(SystemExit) as exit_info:
            main()
    return stdout.getvalue(), exit_info.value.code


@pytest.mark.parametrize("overlay", ([], {"order": []}, {"quota_guard": []}))
def test_hook_bridge_rejects_invalid_overlay_shapes(tmp_path: Path, overlay: object) -> None:
    from autoskillit.hooks._runtime._hook_settings import read_merged_hook_config

    _write_hook_config(tmp_path, {"disabled": False})
    overlay_path = tmp_path / ".autoskillit" / "temp" / ".hook_config_overlay.json"
    overlay_path.write_text(json.dumps(overlay))

    assert read_merged_hook_config(tmp_path) == {}


def test_hook_reads_cache_path_from_hook_config_without_denying(tmp_path, monkeypatch) -> None:
    cache = tmp_path / "quota-cache.json"
    _write_blocking_cache(cache)
    _write_hook_config(
        tmp_path,
        {"cache_path": str(cache), "cache_max_age": 300, "disabled": False},
    )
    monkeypatch.chdir(tmp_path)
    _clear_env(monkeypatch)

    with patch("autoskillit.hooks.guards.quota_guard.write_quota_log_event") as write_event:
        output, exit_code = _run_quota_hook()

    assert output == ""
    assert exit_code == 0
    assert write_event.call_args.args[0]["constraint_observed"] is True


def test_disabled_hook_config_skips_quota_observation(tmp_path, monkeypatch) -> None:
    cache = tmp_path / "quota-cache.json"
    _write_blocking_cache(cache)
    _write_hook_config(
        tmp_path,
        {"cache_path": str(cache), "cache_max_age": 300, "disabled": True},
    )
    monkeypatch.chdir(tmp_path)
    _clear_env(monkeypatch)

    with patch("autoskillit.hooks.guards.quota_guard.write_quota_log_event") as write_event:
        output, exit_code = _run_quota_hook()

    assert output == ""
    assert exit_code == 0
    write_event.assert_not_called()


def test_hook_config_quota_guard_keys_match_payload_keys(tmp_path) -> None:
    from autoskillit.config.settings import QuotaGuardConfig
    from autoskillit.hooks._runtime._hook_settings import QUOTA_GUARD_HOOK_PAYLOAD_KEYS
    from autoskillit.server.tools.tools_kitchen import _quota_guard_hook_payload

    payload = {"quota_guard": _quota_guard_hook_payload(QuotaGuardConfig())}
    hook_cfg = tmp_path / ".autoskillit" / "temp" / ".hook_config.json"
    hook_cfg.parent.mkdir(parents=True, exist_ok=True)
    hook_cfg.write_text(json.dumps(payload))

    data = json.loads(hook_cfg.read_text())
    assert set(data["quota_guard"].keys()) == QUOTA_GUARD_HOOK_PAYLOAD_KEYS
    assert "buffer_seconds" not in data["quota_guard"]


def test_output_budget_policy_serializer_matches_stdlib_bridge_keys() -> None:
    from autoskillit.config import OutputBudgetConfig
    from autoskillit.hooks._runtime._hook_settings import OUTPUT_BUDGET_POLICY_HOOK_PAYLOAD_KEYS
    from autoskillit.server.tools.tools_kitchen import _output_budget_policy_hook_payload

    payload = _output_budget_policy_hook_payload(
        OutputBudgetConfig(
            guard_enabled=False,
            shell_max_inline_bytes=654,
            capture_capacity={"max_operational_records": 2048},
        )
    )

    assert set(payload) == OUTPUT_BUDGET_POLICY_HOOK_PAYLOAD_KEYS
    assert payload == {
        "disabled": True,
        "shell_max_inline_bytes": 654,
        "capture_capacity": {"max_operational_records": 2048},
    }


def test_output_budget_policy_overlay_overrides_snapshot(tmp_path) -> None:
    from autoskillit.hooks._runtime._hook_settings import read_merged_hook_config

    config_dir = tmp_path / ".autoskillit" / "temp"
    config_dir.mkdir(parents=True)
    (config_dir / ".hook_config.json").write_text(
        json.dumps(
            {
                "output_budget_policy": {
                    "disabled": False,
                    "shell_max_inline_bytes": 12000,
                }
            }
        )
    )
    (config_dir / ".hook_config_overlay.json").write_text(
        json.dumps({"output_budget_policy": {"disabled": True}})
    )

    policy = read_merged_hook_config(tmp_path)["output_budget_policy"]

    assert policy == {"disabled": True, "shell_max_inline_bytes": 12000}


def test_hook_config_round_trip_via_resolve_quota_settings(tmp_path, monkeypatch) -> None:
    from autoskillit.config.settings import QuotaGuardConfig
    from autoskillit.hooks._runtime._hook_settings import resolve_quota_settings
    from autoskillit.server.tools.tools_kitchen import _quota_guard_hook_payload

    monkeypatch.chdir(tmp_path)
    _clear_env(monkeypatch)
    cfg = QuotaGuardConfig(cache_max_age=999, cache_path="/round-trip.json", enabled=True)
    hook_cfg = tmp_path / ".autoskillit" / "temp" / ".hook_config.json"
    hook_cfg.parent.mkdir(parents=True, exist_ok=True)
    hook_cfg.write_text(json.dumps({"quota_guard": _quota_guard_hook_payload(cfg)}))

    settings = resolve_quota_settings()

    assert settings.cache_max_age == 999
    assert settings.cache_path == "/round-trip.json"
    assert settings.disabled is False
