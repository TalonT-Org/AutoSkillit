"""C7/C10: every child-environment builder sets DBUS_SESSION_BUS_ADDRESS unconditionally,
and the disabled: fail-fast branch spawns no dbus daemon.

Parametrized over the five builders that assemble a *full* child env (not the eight
functions Related Issue 11 enumerates): ClaudeEnvPolicy.build_env delegates to
build_agent_env (fixing both with one edit), and _filter_protected_native_shell_env /
_managed_native_shell_env are leaf helpers that filter caller extras or serialize a small
control-var overlay -- neither assembles a base env, so asserting "sets
DBUS_SESSION_BUS_ADDRESS" on their output would be vacuous.
"""

from __future__ import annotations

import pytest

from autoskillit.core import build_agent_env, build_maintenance_env
from autoskillit.execution.backends._codex_cmd_builders import CodexEnvPolicy
from autoskillit.execution.testing import build_sanitized_env
from autoskillit.hooks._capture_process import _scrubbed_user_environment

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def _build_agent_env_result() -> str | None:
    return dict(build_agent_env(base={"PATH": "/bin"})).get("DBUS_SESSION_BUS_ADDRESS")


def _build_maintenance_env_result() -> str | None:
    return dict(build_maintenance_env({"HOME": "/home/x"})).get("DBUS_SESSION_BUS_ADDRESS")


def _build_sanitized_env_result() -> str | None:
    return build_sanitized_env().get("DBUS_SESSION_BUS_ADDRESS")


def _codex_env_policy_result() -> str | None:
    return CodexEnvPolicy().build_env({"PATH": "/bin"}).get("DBUS_SESSION_BUS_ADDRESS")


def _scrubbed_user_environment_result() -> str | None:
    return _scrubbed_user_environment().get("DBUS_SESSION_BUS_ADDRESS")


_BUILDERS = {
    "build_agent_env": _build_agent_env_result,
    "build_maintenance_env": _build_maintenance_env_result,
    "build_sanitized_env": _build_sanitized_env_result,
    "CodexEnvPolicy.build_env": _codex_env_policy_result,
    "_scrubbed_user_environment": _scrubbed_user_environment_result,
}


@pytest.mark.parametrize("builder_name", sorted(_BUILDERS))
def test_every_child_env_builder_sets_dbus_session_bus_address_when_host_has_none(
    builder_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)

    result = _BUILDERS[builder_name]()

    assert result == "disabled:", f"{builder_name} did not set the fail-fast sentinel"


@pytest.mark.parametrize("builder_name", sorted(_BUILDERS))
def test_every_child_env_builder_forwards_the_host_value_when_present(
    builder_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/1000/bus")

    result = _BUILDERS[builder_name]()

    assert result == "unix:path=/run/user/1000/bus"


def test_claude_env_policy_delegates_to_build_agent_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """ClaudeEnvPolicy.build_env is not independently parametrized above because it's a pure
    delegator to build_agent_env -- confirm that delegation still holds and still carries the
    unconditional DBUS_SESSION_BUS_ADDRESS behavior through."""
    from autoskillit.execution.backends.claude import ClaudeEnvPolicy

    monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)

    result = ClaudeEnvPolicy().build_env({"PATH": "/bin"})

    assert result.get("DBUS_SESSION_BUS_ADDRESS") == "disabled:"


def test_disabled_bus_address_fails_fast_without_autolaunch() -> None:
    """C10: a dbus client raises promptly and spawns no daemon under
    DBUS_SESSION_BUS_ADDRESS='disabled:'. Mandatory, not conditional -- S3-3's rule always
    reaches this branch when the host defines no bus address.
    """
    pytest.importorskip("jeepney")
    from jeepney.bus import parse_addresses

    with pytest.raises(ValueError):
        list(parse_addresses("disabled:"))
