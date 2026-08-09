"""Subprocess argv assertions for autoskillit self-invocation tests."""

from __future__ import annotations


def assert_valid_maintenance_install_argv(cmd: list[str]) -> None:
    """Reject flagless or invalid maintenance-install argv.

    Called as the first statement of every fake-runner that handles
    ``autoskillit install --maintenance-update``. There is no opt-out —
    the helper detects skip cases by content.
    """
    if "install" not in cmd:
        return  # Not an install command; skip validation.
    if "--maintenance-update" not in cmd:
        # Direct mode — validate the DIRECT contract instead.
        _assert_valid_direct_install_argv(cmd)
        return
    # Maintenance mode — require --expected-version with a non-empty value.
    if "--expected-version" not in cmd:
        raise AssertionError(f"maintenance-install argv missing --expected-version: {cmd}")
    idx = cmd.index("--expected-version")
    if idx + 1 >= len(cmd) or not cmd[idx + 1]:
        raise AssertionError(f"maintenance-install argv has empty --expected-version: {cmd}")


def _assert_valid_direct_install_argv(cmd: list[str]) -> None:
    """Validate that a direct-install argv does not violate the strict contract."""
    if "--maintenance-update" in cmd:
        return  # Already handled by the caller.
    # Direct mode forbids --expected-version without --maintenance-update.
    if "--expected-version" in cmd:
        raise AssertionError(f"direct-install argv must not include --expected-version: {cmd}")
    if "--require-registered-plugin" in cmd:
        raise AssertionError(
            f"direct-install argv must not include --require-registered-plugin: {cmd}"
        )
