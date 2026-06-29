"""Hook efficacy contract tests.

Machine-verifies four contracts on every HookDef in the live HOOK_REGISTRY:

1. Mechanism presence — every hook declares a non-None mechanism field.
2. Backend strength coverage — enforcement_strength contains a key for every
   backend in KNOWN_BACKEND_NAMES (normalized: hyphens → underscores).
3. Codex hard-claim consistency — a hook may claim enforcement_strength
   codex='hard' only if its matcher includes 'Bash' (the only tool Codex
   exposes that hooks can intercept with hard enforcement).
4. Codex status/strength coherence — codex_status='not-applicable' if and
   only if enforcement_strength['codex']='not-applicable'.
"""

from __future__ import annotations

import re

import pytest

from autoskillit.core import KNOWN_BACKEND_NAMES
from autoskillit.hook_registry import HOOK_REGISTRY

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

MIN_EXPECTED_HOOK_COUNT = 30

_HOOK_IDS = [
    f"{h.event_type}:{h.matcher[:40]}:{h.scripts[0] if h.scripts else 'no-script'}"
    for h in HOOK_REGISTRY
]

_EXPECTED_STRENGTH_KEYS: frozenset[str] = frozenset(
    name.replace("-", "_") for name in KNOWN_BACKEND_NAMES
)


def test_registry_not_truncated() -> None:
    assert len(HOOK_REGISTRY) >= MIN_EXPECTED_HOOK_COUNT, (
        f"HOOK_REGISTRY appears truncated: {len(HOOK_REGISTRY)} entries "
        f"(expected >= {MIN_EXPECTED_HOOK_COUNT})"
    )


class TestMechanismFieldPresent:
    @pytest.mark.parametrize("hook", HOOK_REGISTRY, ids=_HOOK_IDS)
    def test_mechanism_is_not_none(self, hook) -> None:
        assert hook.mechanism is not None, (
            f"Hook matcher={hook.matcher!r} scripts={hook.scripts} has mechanism=None"
        )


class TestBackendStrengthCoverage:
    @pytest.mark.parametrize("hook", HOOK_REGISTRY, ids=_HOOK_IDS)
    def test_all_backends_have_strength_entry(self, hook) -> None:
        missing = _EXPECTED_STRENGTH_KEYS - hook.enforcement_strength.keys()
        assert not missing, (
            f"Hook matcher={hook.matcher!r} scripts={hook.scripts} "
            f"missing enforcement_strength keys: {sorted(missing)}"
        )


class TestCodexHardClaimConsistency:
    @pytest.mark.parametrize("hook", HOOK_REGISTRY, ids=_HOOK_IDS)
    def test_codex_hard_requires_bash_matcher(self, hook) -> None:
        if hook.enforcement_strength.get("codex") != "hard":
            return
        assert re.search(r"Bash", hook.matcher) is not None, (
            f"Hook matcher={hook.matcher!r} scripts={hook.scripts} claims "
            f"enforcement_strength codex='hard' but matcher does not "
            f"include 'Bash'"
        )


class TestCodexStatusStrengthCoherence:
    @pytest.mark.parametrize("hook", HOOK_REGISTRY, ids=_HOOK_IDS)
    def test_codex_status_and_strength_agree(self, hook) -> None:
        codex_strength = hook.enforcement_strength.get("codex")
        assert (hook.codex_status == "not-applicable") == (codex_strength == "not-applicable"), (
            f"Hook matcher={hook.matcher!r} scripts={hook.scripts}: "
            f"codex_status={hook.codex_status!r} but "
            f"enforcement_strength['codex']={codex_strength!r} — "
            f"these must both be 'not-applicable' or both be something else"
        )
