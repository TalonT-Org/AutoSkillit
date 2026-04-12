"""T5-T6: Protocol naming and DefaultSkillResolver export smoke tests."""

from __future__ import annotations

# ---------------------------------------------------------------------------
# T5 — Renamed protocols are importable from autoskillit.core
# ---------------------------------------------------------------------------


def test_renamed_protocols_importable() -> None:
    """New protocol names (AuditLog etc.) must be importable from autoskillit.core."""
    from autoskillit.core import (  # noqa: F401
        AuditLog,
        GateState,
        McpResponseLog,
        SkillResolver,
        TimingLog,
        TokenLog,
    )

    assert all(
        p is not None
        for p in [AuditLog, TokenLog, TimingLog, McpResponseLog, GateState, SkillResolver]
    )


def test_old_protocol_names_not_exported() -> None:
    """Old protocol names must NOT be exported from autoskillit.core after rename."""
    import autoskillit.core as core

    for old_name in [
        "AuditStore",
        "TokenStore",
        "TimingStore",
        "McpResponseStore",
        "GatePolicy",
        "TargetSkillResolver",
    ]:
        assert not hasattr(core, old_name), f"{old_name} should not be exported after rename"


# ---------------------------------------------------------------------------
# T6 — DefaultSkillResolver is exported from autoskillit.workspace
# ---------------------------------------------------------------------------


def test_default_skill_resolver_importable() -> None:
    """DefaultSkillResolver must be importable from autoskillit.workspace."""
    from autoskillit.workspace import DefaultSkillResolver

    r = DefaultSkillResolver()
    assert hasattr(r, "resolve")
    assert hasattr(r, "list_all")
