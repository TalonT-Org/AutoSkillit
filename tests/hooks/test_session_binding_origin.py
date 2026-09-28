"""Session-binding entries are typed by origin; binding reads never collapse invalid state."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import autoskillit.hooks._session_binding as session_binding
from autoskillit.hooks._runtime._session_registry_bridge import (
    is_authenticated_top_level_cook_session,
)
from autoskillit.hooks._session_binding import (
    PROJECTION_MANIFEST_SCHEMA_VERSION,
    SESSION_BINDING_SCHEMA_VERSION,
    BindingReadOutcome,
    JoinAdmissionOutcome,
    LoadedSkillOrigin,
    SessionBinding,
    SessionBindingError,
    admit_join,
    classify_invoked_skill,
    merge_binding,
    read_binding_outcome,
    resolve_binding_path,
    write_binding,
)
from tests.hooks._session_binding_helpers import copy_projected_hook, write_projection_manifest

pytestmark = [pytest.mark.layer("infra"), pytest.mark.small]

_TS = "2026-09-26T00:00:00+00:00"
_MANIFEST: dict[str, object] = {
    "schema_version": PROJECTION_MANIFEST_SCHEMA_VERSION,
    "artifact_digest": "artifact",
    "incarnation_id": "incarnation",
    "skills": {
        "investigate": {
            "join_required": True,
            "child_spawn_cardinality": {},
            "write_scope": ["{{AUTOSKILLIT_TEMP}}/investigate/"],
        }
    },
}


def _merge(*names: str, session_id: str = "session-1") -> SessionBinding:
    binding: SessionBinding | None = None
    for name in names:
        binding = merge_binding(
            binding,
            session_id=session_id,
            new_entry=classify_invoked_skill(_MANIFEST, name, _TS),
            artifact_digest="artifact",
        )
    assert binding is not None
    return binding


@pytest.mark.parametrize(
    ("raw_name", "origin", "binding_valid", "skill_name"),
    [
        ("investigate", LoadedSkillOrigin.AUTOSKILLIT, True, "investigate"),
        ("autoskillit:investigate", LoadedSkillOrigin.AUTOSKILLIT, True, "investigate"),
        ("code-review", LoadedSkillOrigin.FOREIGN, True, "code-review"),
        ("anthropic-skills:docs", LoadedSkillOrigin.FOREIGN, True, "anthropic-skills:docs"),
        ("autoskillit:ghost", LoadedSkillOrigin.UNRESOLVED, False, "ghost"),
        ("", LoadedSkillOrigin.UNRESOLVED, False, ""),
    ],
)
def test_classify_invoked_skill(
    raw_name: str, origin: LoadedSkillOrigin, binding_valid: bool, skill_name: str
) -> None:
    entry = classify_invoked_skill(_MANIFEST, raw_name, _TS)

    assert entry.origin is origin
    assert entry.binding_valid is binding_valid
    assert entry.skill_name == skill_name
    if origin is LoadedSkillOrigin.FOREIGN:
        assert entry.join_required is False
        assert entry.binding_error is None


def test_foreign_entry_keeps_the_envelope_valid_and_join_flag_from_autoskillit() -> None:
    binding = _merge("investigate", "code-review")

    assert binding.binding_valid is True
    assert binding.join_required is True
    assert binding.artifact_digest == "artifact"
    assert [entry.origin for entry in binding.loaded_skills] == [
        LoadedSkillOrigin.AUTOSKILLIT,
        LoadedSkillOrigin.FOREIGN,
    ]
    assert _merge("code-review").join_required is False


def test_unresolved_entry_invalidates_the_envelope() -> None:
    assert _merge("autoskillit:ghost").binding_valid is False


def test_binding_round_trips_with_origin() -> None:
    binding = _merge("investigate", "code-review", "autoskillit:ghost")

    assert SessionBinding.from_json(binding.to_json()) == binding


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda doc: doc.update(binding_valid=True), id="valid-with-unresolved"),
        pytest.param(lambda doc: doc["loaded_skills"][0].pop("origin"), id="missing-origin"),
        pytest.param(
            lambda doc: doc["loaded_skills"][0].update(origin="plugin"), id="unknown-origin"
        ),
    ],
)
def test_from_json_rejects_inconsistent_envelopes(mutate) -> None:
    document = json.loads(_merge("investigate", "autoskillit:ghost").to_json())
    mutate(document)

    with pytest.raises(SessionBindingError):
        SessionBinding.from_json(document)


def test_valid_envelope_rejects_invalid_marking() -> None:
    document = json.loads(_merge("investigate").to_json())
    document["binding_valid"] = False

    with pytest.raises(SessionBindingError):
        SessionBinding.from_json(document)


def _flag(tmp_path: Path, session_id: str = "session-1") -> Path:
    (tmp_path / ".autoskillit").mkdir(exist_ok=True)
    return resolve_binding_path(str(tmp_path), session_id)


def test_read_binding_outcome_distinguishes_every_state(tmp_path: Path) -> None:
    flag = _flag(tmp_path)
    assert (
        read_binding_outcome(str(tmp_path), "session-1").outcome is BindingReadOutcome.NO_BINDING
    )

    write_binding(flag, _merge("investigate"))
    valid = read_binding_outcome(str(tmp_path), "session-1")
    assert valid.outcome is BindingReadOutcome.VALID
    assert valid.binding is not None
    assert valid.binding.loaded_skills[0].skill_name == "investigate"

    write_binding(flag, _merge("autoskillit:ghost"))
    invalid = read_binding_outcome(str(tmp_path), "session-1")
    assert invalid.outcome is BindingReadOutcome.INVALID
    assert invalid.error is not None
    assert "ghost" in invalid.error

    flag.write_text("not-json", encoding="utf-8")
    unreadable = read_binding_outcome(str(tmp_path), "session-1")
    assert unreadable.outcome is BindingReadOutcome.INVALID
    assert unreadable.error is not None
    assert "invalid JSON" in unreadable.error

    flag.write_text(json.dumps({"schema_version": 3, "loaded_skills": []}), encoding="utf-8")
    stale = read_binding_outcome(str(tmp_path), "session-1")
    assert stale.outcome is BindingReadOutcome.INVALID
    assert stale.error == "unsupported session-binding schema_version: 3"


def test_read_binding_outcome_flags_a_tampered_session_id(tmp_path: Path) -> None:
    flag = _flag(tmp_path)
    write_binding(flag, _merge("investigate", session_id="someone-else"))

    assert (
        read_binding_outcome(str(tmp_path), "session-1").outcome
        is BindingReadOutcome.WRONG_SESSION
    )


def test_collapsing_binding_read_api_is_gone() -> None:
    assert not hasattr(session_binding, "read_session_binding")


def test_foreign_entry_is_not_join_bearing(tmp_path: Path) -> None:
    flag = _flag(tmp_path)
    write_binding(flag, _merge("code-review"))

    admission = admit_join(flag, session_id="session-1", skill_name="code-review")

    assert admission.outcome is JoinAdmissionOutcome.NOT_JOIN_BEARING
    assert admission.enforce is False


def test_codex_cook_authentication_reads_the_typed_binding(tmp_path: Path) -> None:
    (tmp_path / ".autoskillit" / "temp").mkdir(parents=True)
    (tmp_path / ".autoskillit" / "temp" / "session_registry.json").write_text(
        json.dumps({"parent-1": {"session_type": "cook"}}), encoding="utf-8"
    )
    binding = merge_binding(
        None,
        session_id="parent-1",
        new_entry=classify_invoked_skill(_MANIFEST, "investigate", _TS),
        artifact_digest="artifact",
        managed_parent_id="parent-1",
        managed_route="interactive-parent",
    )
    write_binding(resolve_binding_path(str(tmp_path), "parent-1"), binding)

    assert is_authenticated_top_level_cook_session(
        str(tmp_path),
        "parent-1",
        headless=False,
        backend="codex",
        launch_id="",
        managed_parent_id="parent-1",
    )


@pytest.mark.parametrize("prior", ["not-json", json.dumps({"schema_version": 3})])
def test_unreadable_prior_flag_is_quarantined_by_the_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, prior: str
) -> None:
    from autoskillit.hooks import skill_load_post_hook as hook_module

    projection_root, _ = copy_projected_hook(tmp_path)
    manifest_path = write_projection_manifest(projection_root, skill_name="investigate")
    monkeypatch.setattr(hook_module, "resolve_projection_manifest_path", lambda _p: manifest_path)
    state_root = tmp_path / "state"
    (state_root / ".autoskillit").mkdir(parents=True)
    flag = resolve_binding_path(str(state_root), "session-1")
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text(prior, encoding="utf-8")

    hook_module._write_skill_binding(
        flag, raw_skill_name="investigate", session_id="session-1", ts=_TS
    )

    rewritten = SessionBinding.from_json(flag.read_text(encoding="utf-8"))
    assert rewritten.schema_version == SESSION_BINDING_SCHEMA_VERSION
    assert rewritten.binding_valid is False
    quarantine, loaded = rewritten.loaded_skills
    assert quarantine.origin is LoadedSkillOrigin.UNRESOLVED
    assert quarantine.skill_name == session_binding.UNREADABLE_PRIOR_BINDING_ENTRY
    assert loaded.origin is LoadedSkillOrigin.AUTOSKILLIT
    read = read_binding_outcome(str(state_root), "session-1")
    assert read.outcome is BindingReadOutcome.INVALID
    assert read.error is not None
    assert read.error.startswith("existing session binding unreadable: ")
