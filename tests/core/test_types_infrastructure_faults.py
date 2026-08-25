"""Tests for infrastructure fault contracts + skill command prefix constants."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_skill_command_prefix_constant_exists() -> None:
    """SKILL_COMMAND_PREFIX is the canonical slash prefix for skill invocations."""
    from autoskillit.core.types import SKILL_COMMAND_PREFIX

    assert SKILL_COMMAND_PREFIX == "/"


def test_autoskillit_skill_prefix_constant_exists() -> None:
    """AUTOSKILLIT_SKILL_PREFIX is the canonical prefix for bundled autoskillit skills."""
    from autoskillit.core.types import AUTOSKILLIT_SKILL_PREFIX

    assert AUTOSKILLIT_SKILL_PREFIX == "/autoskillit:"


# ---------------------------------------------------------------------------
# WriteBehaviorSpec and WriteExpectedResolver
# ---------------------------------------------------------------------------


def test_write_expected_skills_frozenset_removed() -> None:
    """WRITE_EXPECTED_SKILLS must not exist — replaced by contract-driven gate."""
    import autoskillit.core.types as types_mod

    assert not hasattr(types_mod, "WRITE_EXPECTED_SKILLS")


def test_write_behavior_spec_dataclass() -> None:
    """WriteBehaviorSpec must be importable with correct defaults."""
    from autoskillit.core import WriteBehaviorSpec

    default = WriteBehaviorSpec()
    assert default.mode is None
    assert default.expected_when == ()
    always = WriteBehaviorSpec(mode="always")
    assert always.mode == "always"
    cond = WriteBehaviorSpec(mode="conditional", expected_when=("pat",))
    assert cond.expected_when == ("pat",)


def test_infrastructure_fault_exceptions_share_marker_base() -> None:
    """InfrastructureFaultError is the shared marker base for environment faults.

    Imported from the ``autoskillit.core`` package gateway, not the internal
    ``_type_exceptions`` module, so this test also proves the gateway re-export
    is wired up correctly.
    """
    from autoskillit.core import (
        InfrastructureFaultError,
        PluginArtifactContentionError,
        PluginArtifactPublicationError,
        PluginArtifactUnavailableError,
        PluginArtifactValidationError,
        ProcessStaleError,
        StaleGeneratorError,
    )

    assert issubclass(StaleGeneratorError, InfrastructureFaultError)
    assert issubclass(ProcessStaleError, InfrastructureFaultError)
    assert issubclass(PluginArtifactContentionError, InfrastructureFaultError)
    assert issubclass(PluginArtifactUnavailableError, InfrastructureFaultError)

    # Marker base derives directly from Exception -- never RuntimeError/OSError --
    # so joining it onto pre-existing hierarchies never widens existing
    # except-RuntimeError/except-OSError handlers.
    assert InfrastructureFaultError.__bases__ == (Exception,)

    # Deliberately excluded: artifact-content-corrupt and publish-failure are
    # not environment faults.
    assert not issubclass(PluginArtifactValidationError, InfrastructureFaultError)
    assert not issubclass(PluginArtifactPublicationError, InfrastructureFaultError)
