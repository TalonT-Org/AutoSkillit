"""Focused tests for the declare_join_batch binding gate."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit.server.tools.tools_kitchen import _declare_join_batch as declare_module

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def test_invalid_binding_cannot_open_batch_with_retained_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    flag_dir = tmp_path / "flags"
    flag_dir.mkdir()
    (flag_dir / "skill_guard_session-1.flag").write_text(
        json.dumps(
            {
                "binding_valid": False,
                "join_required": True,
                "artifact_digest": "retained-from-prior-load",
                "loaded_skills": [
                    {
                        "skill_name": "join-bearing",
                        "binding_valid": False,
                        "child_spawn_cardinality": {"explicit_slots": 1},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(declare_module, "resolve_flag_dir", lambda _root: flag_dir)

    def unexpected_backend_lookup(_name: str) -> None:
        pytest.fail("invalid bindings must be rejected before backend lookup")

    monkeypatch.setattr(declare_module, "get_backend", unexpected_backend_lookup)

    result = declare_module._declare_join_batch_handler(
        "join-bearing",
        ["assignment"],
        "session-1",
        tmp_path,
    )

    assert result == {
        "success": False,
        "error": "declare_join_batch requires a valid session binding",
    }
