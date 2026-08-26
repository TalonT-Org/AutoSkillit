"""Cross-process session-binding channel tests."""

from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import production_feature_env, production_interpreter_env

pytestmark = [pytest.mark.medium]

_HOOKS_DIR = Path(__file__).resolve().parents[2] / "src" / "autoskillit" / "hooks"
_HOOK = _HOOKS_DIR / "skill_load_post_hook.py"


def _state_root(tmp_path: Path) -> Path:
    root = tmp_path / "worktree"
    (root / ".autoskillit").mkdir(parents=True)
    return root


def _skill_event(*, cwd: Path, skill: str = "join-bearing", session_id: str = "session-1") -> dict:
    return {
        "tool_name": "Skill",
        "tool_input": {"skill": skill},
        "session_id": session_id,
        "cwd": str(cwd.resolve()),
    }


def _hook_env(state_root: Path, **extra: str) -> dict[str, str]:
    """Return an isolated hook environment rooted at ``state_root``."""
    env = production_feature_env()
    env.update(extra)
    env["AUTOSKILLIT_STATE_ROOT"] = str(state_root.resolve())
    env.setdefault("AUTOSKILLIT_AGENT_BACKEND", "claude-code")
    return env


def _run_hook(
    hook_path: Path,
    payload: dict,
    *,
    cwd: Path,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    run_env = production_interpreter_env()
    run_env.update(env)
    return subprocess.run(
        [sys.executable, str(hook_path)],
        cwd=cwd,
        env=run_env,
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
    )


def _copy_projected_hook(tmp_path: Path, name: str) -> tuple[Path, Path]:
    """Build a projection-shaped, stdlib-only hook tree."""
    root = tmp_path / name
    hooks_dir = root / "hooks"
    hooks_dir.mkdir(parents=True)
    for filename in (
        "skill_load_post_hook.py",
        "_hook_payload.py",
        "_hook_settings.py",
        "_session_binding.py",
    ):
        shutil.copy2(_HOOKS_DIR / filename, hooks_dir / filename)
    return root, hooks_dir / "skill_load_post_hook.py"


def _write_manifest(
    projection_root: Path,
    *,
    skill: str = "join-bearing",
    join_required: bool = True,
    schema_version: int = 2,
    artifact_digest: str = "artifact-abc",
) -> Path:
    manifest = projection_root.parent / f".{projection_root.name}.autoskillit-projection.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": schema_version,
                "artifact_kind": "projection",
                "projection_version": 1,
                "semantic_key": "autoskillit@channel-test:1",
                "incarnation_id": "00000000000040008000000000000001",
                "artifact_digest": artifact_digest,
                "skills": {
                    skill: {
                        "join_required": join_required,
                        "child_spawn_cardinality": {"explicit_slots": 1},
                        "semantic_digest": "semantic-abc",
                        "adaptation_digest": "adaptation-abc",
                        "projected_digest": "projected-abc",
                        "canonical_digest": "canonical-abc",
                        "artifact_digest": "",
                        "artifact_incarnation": "",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return manifest


def _binding_path(state_root: Path, session_id: str) -> Path:
    return state_root / ".autoskillit" / "temp" / f"skill_guard_{session_id}.flag"


def test_flag_is_written_when_provider_profile_is_absent(tmp_path: Path) -> None:
    """The production-default child environment still receives a binding flag."""
    state_root = _state_root(tmp_path)
    interpreter_env = production_interpreter_env()
    feature_env = production_feature_env()
    assert interpreter_env
    assert "AUTOSKILLIT_FEATURES__EXPERIMENTAL_ENABLED" not in feature_env

    result = _run_hook(
        _HOOK,
        _skill_event(cwd=state_root),
        cwd=state_root,
        env=_hook_env(state_root),
    )

    assert result.returncode == 0, result.stderr
    assert _binding_path(state_root, "session-1").exists()


def test_manifest_resolves_from_the_installed_hook_location(tmp_path: Path) -> None:
    """The hook locates its sidecar from its bound projection, not process CWD."""
    state_root = _state_root(tmp_path)
    projection_root, hook_path = _copy_projected_hook(tmp_path, "live-projection")
    _write_manifest(projection_root)
    unrelated_cwd = tmp_path / "unrelated"
    unrelated_cwd.mkdir()

    result = _run_hook(
        hook_path,
        _skill_event(cwd=state_root),
        cwd=unrelated_cwd,
        env=_hook_env(state_root),
    )

    assert result.returncode == 0, result.stderr
    binding = json.loads(_binding_path(state_root, "session-1").read_text())
    assert binding["binding_valid"] is True
    assert binding["loaded_skills"][0].get("binding_error") is None


def test_manifest_top_level_artifact_digest_lands_in_the_envelope_top_level(
    tmp_path: Path,
) -> None:
    """The manifest-wide artifact digest is not fabricated per loaded skill."""
    state_root = _state_root(tmp_path)
    projection_root, hook_path = _copy_projected_hook(tmp_path, "digest-projection")
    _write_manifest(projection_root, artifact_digest="artifact-top-level")

    result = _run_hook(
        hook_path,
        _skill_event(cwd=state_root),
        cwd=state_root,
        env=_hook_env(state_root),
    )

    assert result.returncode == 0, result.stderr
    binding = json.loads(_binding_path(state_root, "session-1").read_text())
    assert binding["artifact_digest"] == "artifact-top-level"
    assert "artifact_digest" not in binding["loaded_skills"][0]


def test_unknown_manifest_schema_version_is_refused_not_defaulted(tmp_path: Path) -> None:
    """An unrecognized sidecar version leaves an explicitly unresolved binding."""
    state_root = _state_root(tmp_path)
    log_dir = tmp_path / "logs"
    projection_root, hook_path = _copy_projected_hook(tmp_path, "unknown-version")
    _write_manifest(projection_root, schema_version=99)

    result = _run_hook(
        hook_path,
        _skill_event(cwd=state_root),
        cwd=state_root,
        env=_hook_env(state_root, AUTOSKILLIT_LOG_DIR=str(log_dir)),
    )

    assert result.returncode == 0, result.stderr
    binding = json.loads(_binding_path(state_root, "session-1").read_text())
    entry = binding["loaded_skills"][0]
    assert binding["binding_valid"] is False
    assert entry["binding_valid"] is False
    assert "99" in entry["binding_error"]
    assert any(
        event["event"] == "skill_load_binding_unresolved"
        for event in map(json.loads, (log_dir / "quota_events.jsonl").read_text().splitlines())
    )


def test_orphaned_schema_1_sidecar_is_not_selected_over_the_hook_bound_live_manifest(
    tmp_path: Path,
) -> None:
    """A live hook resolves only the sidecar adjacent to its own projection root."""
    state_root = _state_root(tmp_path)
    orphan_root, _ = _copy_projected_hook(tmp_path, "orphaned-projection")
    _write_manifest(orphan_root, schema_version=1, artifact_digest="orphaned")
    live_root, hook_path = _copy_projected_hook(tmp_path, "live-projection")
    _write_manifest(live_root, artifact_digest="live")

    result = _run_hook(
        hook_path,
        _skill_event(cwd=state_root),
        cwd=state_root,
        env=_hook_env(state_root),
    )

    assert result.returncode == 0, result.stderr
    binding = json.loads(_binding_path(state_root, "session-1").read_text())
    assert binding["binding_valid"] is True
    assert binding["artifact_digest"] == "live"


def test_binding_envelope_round_trips_through_one_shared_type() -> None:
    """The authority owns versioned serialization and legacy unresolved state."""
    from autoskillit.hooks._session_binding import (  # noqa: PLC0415
        LoadedSkillEntry,
        SessionBinding,
        SessionBindingError,
    )

    entry = LoadedSkillEntry(
        skill_name="join-bearing",
        ts="2026-08-26T00:00:00+00:00",
        join_required=True,
        child_spawn_cardinality={"explicit_slots": 1},
        semantic_digest="semantic",
        adaptation_digest="adaptation",
        projected_digest="projected",
        canonical_digest="canonical",
        artifact_incarnation="",
        binding_valid=True,
        binding_error=None,
    )
    binding = SessionBinding(
        schema_version=2,
        session_id="session-1",
        join_required=True,
        binding_valid=True,
        artifact_digest="artifact",
        loaded_skills=(entry,),
    )

    serialized = binding.to_json()
    assert SessionBinding.from_json(serialized) == binding
    with pytest.raises(SessionBindingError):
        SessionBinding.from_json({**json.loads(serialized), "schema_version": 99})

    legacy = SessionBinding.from_json(
        {
            "schema_version": 1,
            "session_id": "session-1",
            "join_required": True,
            "binding_valid": True,
            "loaded_skills": [
                {
                    **json.loads(entry.to_json()),
                    "artifact_incarnation": "legacy-incarnation",
                }
            ],
        }
    )
    assert legacy.schema_version == 2
    assert legacy.binding_valid is False
    assert legacy.loaded_skills[0].binding_valid is False
    assert legacy.loaded_skills[0].binding_error == "legacy session-binding schema 1 is unresolved"


@pytest.mark.parametrize(
    "field",
    (
        "skill_name",
        "ts",
        "semantic_digest",
        "adaptation_digest",
        "projected_digest",
        "canonical_digest",
        "artifact_incarnation",
    ),
)
def test_loaded_skill_rejects_non_string_schema_fields(field: str) -> None:
    """Persisted string fields are validated instead of silently coerced."""
    from autoskillit.hooks._session_binding import (  # noqa: PLC0415
        LoadedSkillEntry,
        SessionBindingError,
    )

    payload: dict[str, object] = {
        "skill_name": "join-bearing",
        "ts": "2026-08-26T00:00:00+00:00",
        "join_required": True,
        "child_spawn_cardinality": {"explicit_slots": 1},
        "semantic_digest": "semantic",
        "adaptation_digest": "adaptation",
        "projected_digest": "projected",
        "canonical_digest": "canonical",
        "artifact_incarnation": "",
        "binding_valid": True,
        "binding_error": None,
    }
    payload[field] = 1

    with pytest.raises(SessionBindingError, match=rf"^{field} must be a string$"):
        LoadedSkillEntry.from_json(payload)


def test_loaded_skill_rejects_boolean_cardinality() -> None:
    """Boolean values are not admitted as integer cardinalities."""
    from autoskillit.hooks._session_binding import (  # noqa: PLC0415
        LoadedSkillEntry,
        SessionBindingError,
    )

    with pytest.raises(SessionBindingError, match="child_spawn_cardinality"):
        LoadedSkillEntry.from_json(
            {
                "child_spawn_cardinality": {"explicit_slots": True},
            }
        )


def test_binding_path_rejects_an_empty_session_id(tmp_path: Path) -> None:
    """The authority never constructs the ambiguous skill_guard_.flag path."""
    from autoskillit.hooks._session_binding import (  # noqa: PLC0415
        SessionBindingError,
        resolve_binding_path,
    )

    with pytest.raises(SessionBindingError, match="session_id must be a non-empty string"):
        resolve_binding_path(str(tmp_path), "")


def test_write_binding_closes_descriptor_when_fdopen_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The atomic writer retains no descriptor when ownership transfer fails."""
    from autoskillit.hooks import _session_binding as binding_module  # noqa: PLC0415

    fd, temporary_path = binding_module.tempfile.mkstemp(dir=tmp_path)
    monkeypatch.setattr(
        binding_module.tempfile,
        "mkstemp",
        lambda **_kwargs: (fd, temporary_path),
    )

    def fail_fdopen(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("fdopen failed")

    monkeypatch.setattr(binding_module.os, "fdopen", fail_fdopen)
    binding = binding_module.SessionBinding(
        schema_version=2,
        session_id="session-1",
        join_required=False,
        binding_valid=True,
        artifact_digest="artifact",
        loaded_skills=(),
    )

    with pytest.raises(RuntimeError, match="fdopen failed"):
        binding_module.write_binding(tmp_path / "binding.json", binding)
    with pytest.raises(OSError):
        os.fstat(fd)
    assert not Path(temporary_path).exists()


def test_writer_and_every_hook_side_reader_resolve_the_same_channel_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The server reader is deliberately excluded until the next implementation part."""
    from autoskillit.hooks._join_ledger import resolve_flag_dir  # noqa: PLC0415
    from autoskillit.hooks._session_binding import (  # noqa: PLC0415
        resolve_binding_path,
        resolve_channel_dir,
    )

    state_root = _state_root(tmp_path)
    unrelated_cwd = tmp_path / "unrelated"
    unrelated_cwd.mkdir()
    expected = state_root / ".autoskillit" / "temp"
    monkeypatch.setenv("AUTOSKILLIT_STATE_ROOT", str(state_root))

    assert resolve_channel_dir(state_root) == expected
    assert resolve_binding_path(str(unrelated_cwd), "session-1").parent == expected
    assert resolve_flag_dir(state_root) == expected

    writer_tree = ast.parse(_HOOK.read_text())
    writer_calls = [
        node.func.id
        for node in ast.walk(writer_tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    assert "resolve_binding_path" in writer_calls
    assert "find_project_root" not in writer_calls

    reader_paths = (
        _HOOKS_DIR / "guards" / "skill_load_guard.py",
        _HOOKS_DIR / "guards" / "join_claim_guard.py",
        _HOOKS_DIR / "guards" / "join_settle_guard.py",
        _HOOKS_DIR / "guards" / "join_followup_guard.py",
        _HOOKS_DIR / "guards" / "join_stop_guard.py",
    )
    for path in reader_paths:
        tree = ast.parse(path.read_text())
        calls = [
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        ]
        assert "resolve_state_root" in calls, path
        assert "find_project_root" not in calls, path
        expected_resolver = (
            "resolve_channel_dir" if path.name == "skill_load_guard.py" else "resolve_flag_dir"
        )
        assert expected_resolver in calls, path


def test_channel_dir_converges_from_every_anchor_including_a_subdirectory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A nested payload CWD normalizes to the same state-root channel directory."""
    from autoskillit.hooks._session_binding import (  # noqa: PLC0415
        resolve_binding_path,
        resolve_channel_dir,
    )

    state_root = _state_root(tmp_path)
    nested = state_root / "nested" / "directory"
    nested.mkdir(parents=True)
    expected = state_root / ".autoskillit" / "temp"
    monkeypatch.setenv("AUTOSKILLIT_STATE_ROOT", str(state_root))

    assert resolve_channel_dir(state_root) == expected
    assert resolve_channel_dir(Path(os.environ["AUTOSKILLIT_STATE_ROOT"])) == expected
    assert resolve_channel_dir(nested) == expected
    assert resolve_binding_path(str(nested), "session-1").parent == expected


def test_skill_load_guard_finds_a_flag_written_from_a_worktree_cwd(tmp_path: Path) -> None:
    """A gated guard permits after the hook writes through the state-root authority."""
    state_root = _state_root(tmp_path)
    projection_root, hook_path = _copy_projected_hook(tmp_path, "guard-projection")
    _write_manifest(projection_root)
    other_cwd = tmp_path / "other-cwd"
    other_cwd.mkdir()
    payload = _skill_event(cwd=state_root)

    hook_result = _run_hook(
        hook_path,
        payload,
        cwd=other_cwd,
        env=_hook_env(state_root, AUTOSKILLIT_PROVIDER_PROFILE="minimax"),
    )
    guard_result = _run_hook(
        _HOOKS_DIR / "guards" / "skill_load_guard.py",
        payload,
        cwd=other_cwd,
        env=_hook_env(
            state_root,
            AUTOSKILLIT_PROVIDER_PROFILE="minimax",
            AUTOSKILLIT_HEADLESS="1",
            AUTOSKILLIT_SESSION_TYPE="skill",
            AUTOSKILLIT_APPLICABLE_GUARDS="skill_load_guard",
        ),
    )

    assert hook_result.returncode == 0, hook_result.stderr
    assert guard_result.returncode == 0, guard_result.stderr
    assert "SKILL LOADING REQUIRED" not in guard_result.stdout
