"""The skill_projection module policy docstring must state an accurate invariant (T-B11).

A docstring that states a false invariant is a defect: it licenses the
assumption at every call site. Before B-7, ``workspace/skill_projection.py``'s
module docstring claimed the projection source "cannot be stale" because "it
is the code currently executing" — a package root is a path into a tree
``uv`` owns and is free to relocate or garbage-collect; the executing code
lives in memory, not on that path. The docstring's own next sentence
(destination-replacement policy) already condemned exactly this shape for a
different subject, which is why the false claim was worth catching here too
(issue #4597).
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]


def test_skill_projection_policy_docstring_is_accurate() -> None:
    import autoskillit.workspace.skill_projection as skill_projection

    doc = skill_projection.__doc__ or ""
    assert doc, "skill_projection.py must have a module docstring"
    assert "cannot be stale" not in doc, (
        "module docstring still claims the projection source cannot be stale"
    )
    assert "sealed" in doc and "InstallBinding" in doc, (
        "module docstring must name the sealed InstallBinding as the projection source"
    )
