"""Tests for InvariantDef and INVARIANT_REGISTRY."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_invariant_def_importable_from_core() -> None:
    """InvariantDef must be importable from autoskillit.core."""
    from autoskillit.core import InvariantDef

    assert InvariantDef is not None


def test_invariant_registry_importable_from_core() -> None:
    """INVARIANT_REGISTRY must be importable from autoskillit.core."""
    from autoskillit.core import INVARIANT_REGISTRY

    assert isinstance(INVARIANT_REGISTRY, dict)


def test_invariant_def_is_frozen() -> None:
    """InvariantDef must be a frozen dataclass."""
    from autoskillit.core import InvariantDef

    inv = InvariantDef(
        id="test",
        prohibition="test prohibition",
        source_doc="AGENTS.md",
        gate_target="guards/test_guard.py",
        enforcement_layer="advisory",
        backends=frozenset({"claude-code"}),
    )
    with pytest.raises(AttributeError):
        inv.id = "changed"  # type: ignore[misc]


def test_invariant_def_fields() -> None:
    """InvariantDef must have all required fields."""
    import dataclasses

    from autoskillit.core import InvariantDef

    field_names = {f.name for f in dataclasses.fields(InvariantDef)}
    assert field_names == {
        "id",
        "prohibition",
        "source_doc",
        "gate_target",
        "enforcement_layer",
        "backends",
    }


def test_enforcement_layer_literal_values() -> None:
    """enforcement_layer must accept the four defined Literal values."""
    from autoskillit.core import InvariantDef

    for layer in ("server-side", "sandbox-ci", "hook-deny", "advisory"):
        inv = InvariantDef(
            id="test",
            prohibition="test",
            source_doc="test",
            gate_target="test",
            enforcement_layer=layer,  # type: ignore[arg-type]
            backends=frozenset(),
        )
        assert inv.enforcement_layer == layer


def test_registry_keys_match_ids() -> None:
    """Each registry key must match its InvariantDef.id."""
    from autoskillit.core import INVARIANT_REGISTRY

    for key, inv in INVARIANT_REGISTRY.items():
        assert key == inv.id, f"Key {key!r} != id {inv.id!r}"


def test_bre_grep_pattern_backends() -> None:
    """bre-grep-pattern must have backends=frozenset({'claude-code'}) only."""
    from autoskillit.core import INVARIANT_REGISTRY

    entry = INVARIANT_REGISTRY["bre-grep-pattern"]
    assert entry.backends == frozenset({"claude-code"})


def test_inline_script_enforcement_layer() -> None:
    """inline-script-in-cmd must have enforcement_layer='server-side'."""
    from autoskillit.core import INVARIANT_REGISTRY

    assert INVARIANT_REGISTRY["inline-script-in-cmd"].enforcement_layer == "server-side"


def test_env_key_enforcement_layer() -> None:
    """env-key-in-with-args must have enforcement_layer='server-side'."""
    from autoskillit.core import INVARIANT_REGISTRY

    assert INVARIANT_REGISTRY["env-key-in-with-args"].enforcement_layer == "server-side"


def test_all_gate_targets_are_nonempty() -> None:
    """Every gate_target must be a non-empty string."""
    from autoskillit.core import INVARIANT_REGISTRY

    for key, inv in INVARIANT_REGISTRY.items():
        assert inv.gate_target, f"{key} has empty gate_target"


def test_all_backends_are_known() -> None:
    """Every backends frozenset must only contain known backend names."""
    from autoskillit.core import INVARIANT_REGISTRY, KNOWN_BACKEND_NAMES

    for key, inv in INVARIANT_REGISTRY.items():
        assert inv.backends.issubset(KNOWN_BACKEND_NAMES), (
            f"{key} has unknown backends: {inv.backends - KNOWN_BACKEND_NAMES}"
        )


def test_all_backends_nonempty() -> None:
    """Every entry must have at least one backend."""
    from autoskillit.core import INVARIANT_REGISTRY

    for key, inv in INVARIANT_REGISTRY.items():
        assert inv.backends, f"{key} has empty backends"


def test_registry_keys_are_lowercase_kebab() -> None:
    """All registry keys must be lowercase kebab-case (L1 exception allowed)."""
    import re

    from autoskillit.core import INVARIANT_REGISTRY

    pattern = re.compile(r"^[a-z][a-z0-9]*(-[a-zA-Z0-9]+)*$")
    for key in INVARIANT_REGISTRY:
        assert pattern.match(key), f"Key {key!r} does not match kebab-case pattern"


def test_expected_entry_ids() -> None:
    """Registry must contain exactly the 13 specified entries."""
    from autoskillit.core import INVARIANT_REGISTRY

    expected = {
        "run-in-background",
        "git-amend",
        "git-force-push",
        "git-reset-hard",
        "git-clean-f",
        "recipe-read-headless",
        "write-path-prefix",
        "skill-orchestration-from-L1",
        "interpreter-write-bypass",
        "inline-script-in-cmd",
        "env-key-in-with-args",
        "generated-file-write",
        "bre-grep-pattern",
    }
    assert set(INVARIANT_REGISTRY.keys()) == expected
