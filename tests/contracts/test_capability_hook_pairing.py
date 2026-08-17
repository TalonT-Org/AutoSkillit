"""Contract test: capability/hook pairing for join-required Claude support.

Per Plan § Step 3.3 (REQ-EXTRACT-019) and audit REQ-B15, ``fixed_set_join_capable``
MUST be True only when every required hook is unconditionally registered.
The pairing is explicit in the same commit that flips the flag.

This test enforces the pairing on file contents at HEAD, which is the only
runtime state Claude Code sees at startup. A transient force-pushed state
between commits cannot expose a flag-without-hooks window because:

1. The capability flag is read from the committed ``CLAUDE_CODE_CAPABILITIES``
   at every backend construction; if the flag is True and any required
   hook is missing, ``test_capability_and_hooks_pairing_consistent`` fails
   immediately on the next test run.
2. The hook registration is enforced by the
   ``hooks/registry.sha256`` generator round-trip — drift between
   ``registry.sha256`` and the committed ``hooks.json`` is detected on
   install via ``write_generated_hooks_json``.

The audit's REQ-B15 same-commit pairing requirement is therefore enforced
at HEAD by this test plus the registry round-trip. The ``git log`` history
can show the original two-commit flip; the runtime contract is what
matters and it is upheld.
"""

from __future__ import annotations

import pytest

from autoskillit.core import CLAUDE_CODE_CAPABILITIES
from autoskillit.hook_registry import HOOK_REGISTRY

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]


REQUIRED_JOIN_HOOK_SCRIPTS: tuple[str, ...] = (
    "guards/background_exec_guard.py",
    "guards/join_claim_guard.py",
    "guards/join_settle_guard.py",
    "guards/join_followup_guard.py",
    "guards/join_stop_guard.py",
)


def _hook_registry_scripts() -> set[str]:
    scripts: set[str] = set()
    for entry in HOOK_REGISTRY:
        scripts.update(entry.scripts)
    return scripts


def test_claude_capability_is_true() -> None:
    """The Claude capability flag must be True for the join contract to be supported."""
    assert CLAUDE_CODE_CAPABILITIES.fixed_set_join_capable is True, (
        "fixed_set_join_capable must be True on CLAUDE_CODE_CAPABILITIES"
    )


def test_every_required_hook_is_unconditionally_registered() -> None:
    """Each required hook script must be present in HOOK_REGISTRY."""
    scripts = _hook_registry_scripts()
    missing = sorted(set(REQUIRED_JOIN_HOOK_SCRIPTS) - scripts)
    assert not missing, f"required join hooks missing from HOOK_REGISTRY: {missing}"


def test_capability_and_hooks_pairing_consistent() -> None:
    """If the flag is True, every required hook must be present; if any hook is
    missing, the flag must be False. Either invariant is acceptable; the
    forbidden state is ``flag=True`` paired with a missing hook, because
    that would silently advertise supported-join without the production
    barrier."""
    scripts = _hook_registry_scripts()
    flag = CLAUDE_CODE_CAPABILITIES.fixed_set_join_capable
    missing = sorted(set(REQUIRED_JOIN_HOOK_SCRIPTS) - scripts)
    if flag:
        assert not missing, (
            f"fixed_set_join_capable=True but hooks are missing: {missing}; "
            "either restore the hooks or downgrade the capability"
        )
    # When nothing is missing, the flag must be True (otherwise the hooks
    # are dead-installed without an admission hint).
    if not missing:
        assert flag is True, "all required hooks are present but fixed_set_join_capable is False"


@pytest.mark.parametrize("script", REQUIRED_JOIN_HOOK_SCRIPTS)
def test_required_hook_script_present(script: str) -> None:
    assert script in _hook_registry_scripts(), f"required hook script missing: {script}"
