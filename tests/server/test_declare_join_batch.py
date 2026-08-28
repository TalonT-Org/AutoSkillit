"""Focused tests for the declare_join_batch binding gate."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.hooks._session_binding import (
    SESSION_BINDING_SCHEMA_VERSION,
    LoadedSkillEntry,
    SessionBinding,
)
from autoskillit.server.tools.tools_kitchen import _declare_join_batch as declare_module

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def test_invalid_binding_cannot_open_batch_with_retained_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binding_path = tmp_path / "flags" / "skill_guard_session-1.flag"
    binding_path.parent.mkdir()
    binding_path.write_text(
        SessionBinding(
            schema_version=SESSION_BINDING_SCHEMA_VERSION,
            session_id="session-1",
            join_required=True,
            binding_valid=False,
            artifact_digest="retained-from-prior-load",
            loaded_skills=(
                LoadedSkillEntry(
                    skill_name="join-bearing",
                    ts="2026-08-26T00:00:00+00:00",
                    join_required=True,
                    child_spawn_cardinality={"explicit_slots": 1},
                    semantic_digest="semantic",
                    adaptation_digest="adaptation",
                    projected_digest="projected",
                    canonical_digest="canonical",
                    source_artifact_digest="retained-from-prior-load",
                    source_artifact_incarnation_id="incarnation",
                    binding_valid=False,
                    binding_error="invalid test binding",
                ),
            ),
        ).to_json(),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        declare_module,
        "resolve_binding_path",
        lambda _root, _session_id: binding_path,
    )

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
