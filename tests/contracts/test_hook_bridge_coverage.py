"""
REQ-BRIDGE-001: The quota guard hook config bridge must produce exactly
the keys that resolve_quota_settings() reads.

Analogous to test_config_field_coverage.py (REQ-CONFIG-001), which guards
dynaconf loading completeness, this test guards bridge serialization completeness.
Prevents silent omission when fields are added to QuotaGuardConfig that need
to cross the stdlib-only boundary.
"""

from autoskillit.config.settings import QuotaGuardConfig
from autoskillit.hooks._hook_settings import QUOTA_GUARD_HOOK_PAYLOAD_KEYS
from autoskillit.server.tools_kitchen import _quota_guard_hook_payload


def test_quota_guard_hook_payload_keys_match_payload_keys() -> None:
    """_quota_guard_hook_payload() must produce exactly the keys that
    resolve_quota_settings() reads from hook_config['quota_guard'].

    If this test fails after adding a field to QuotaGuardConfig or
    QuotaHookSettings, update _quota_guard_hook_payload() and
    QUOTA_GUARD_HOOK_PAYLOAD_KEYS together.
    """
    cfg = QuotaGuardConfig()
    payload = _quota_guard_hook_payload(cfg)
    assert set(payload.keys()) == QUOTA_GUARD_HOOK_PAYLOAD_KEYS, (
        f"Bridge payload keys {set(payload.keys())} do not match "
        f"payload keys {QUOTA_GUARD_HOOK_PAYLOAD_KEYS}. "
        f"Update _quota_guard_hook_payload() and QUOTA_GUARD_HOOK_PAYLOAD_KEYS together."
    )


def test_quota_guard_hook_payload_enabled_true_produces_disabled_false() -> None:
    cfg = QuotaGuardConfig(enabled=True)
    payload = _quota_guard_hook_payload(cfg)
    assert payload["disabled"] is False


def test_quota_guard_hook_payload_enabled_false_produces_disabled_true() -> None:
    cfg = QuotaGuardConfig(enabled=False)
    payload = _quota_guard_hook_payload(cfg)
    assert payload["disabled"] is True


def test_quota_guard_hook_payload_bridges_cache_fields() -> None:
    cfg = QuotaGuardConfig(cache_max_age=999, cache_path="/x/y.json", buffer_seconds=42)
    payload = _quota_guard_hook_payload(cfg)
    assert payload["cache_max_age"] == 999
    assert payload["cache_path"] == "/x/y.json"
    assert payload["buffer_seconds"] == 42
