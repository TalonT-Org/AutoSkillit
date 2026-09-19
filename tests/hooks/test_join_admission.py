"""Focused contracts for the canonical join-admission reader."""

from __future__ import annotations

import pytest

from autoskillit.hooks._session_binding import (
    JoinAdmissionOutcome,
    SessionBinding,
    admit_join,
    write_binding,
)

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.small]


def test_missing_binding_is_not_enforced(tmp_path) -> None:
    admission = admit_join(tmp_path / "missing.flag", session_id="session", skill_name="scope")

    assert admission.outcome is JoinAdmissionOutcome.NO_BINDING
    assert not admission.enforce


def test_wrong_session_is_not_enforced(tmp_path) -> None:
    path = tmp_path / "binding.flag"
    write_binding(
        path,
        SessionBinding(
            schema_version=3,
            session_id="other",
            join_required=True,
            binding_valid=True,
            artifact_digest="",
            loaded_skills=(),
        ),
    )

    admission = admit_join(path, session_id="session", skill_name="scope")

    assert admission.outcome is JoinAdmissionOutcome.WRONG_SESSION
    assert not admission.enforce


def test_malformed_binding_is_fail_closed(tmp_path) -> None:
    path = tmp_path / "binding.flag"
    path.write_text("not json", encoding="utf-8")

    admission = admit_join(path, session_id="session", skill_name="scope")

    assert admission.outcome is JoinAdmissionOutcome.INVALID_BINDING
    assert admission.enforce
