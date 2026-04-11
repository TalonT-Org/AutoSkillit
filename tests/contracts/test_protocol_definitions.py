"""Tests for Protocol definitions in core/_type_protocols.py.

REQ-PROTO-007: SkillLister Protocol must live in core/_type_protocols.py.
"""

from __future__ import annotations

import inspect


def test_skill_lister_protocol_defined() -> None:
    """REQ-PROTO-007: SkillLister Protocol must live in
    core/_type_protocols.py and define a `list_all() -> list[Any]`
    method, so L2 recipe code can type-annotate against L0 instead of
    binding to the L1 workspace concrete class."""
    from autoskillit.core._type_protocols import SkillLister

    assert hasattr(SkillLister, "list_all")
    sig = inspect.signature(SkillLister.list_all)
    assert "self" in sig.parameters


def test_skill_resolver_satisfies_skill_lister() -> None:
    from autoskillit.core._type_protocols import SkillLister
    from autoskillit.workspace.skills import SkillResolver

    instance: SkillLister = SkillResolver()
    assert callable(instance.list_all)
