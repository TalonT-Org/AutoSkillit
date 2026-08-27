from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from autoskillit.core import PluginLoadMode, SkillExecutionRole
from autoskillit.execution.backends.claude import ClaudeCodeBackend
from autoskillit.hooks._hook_settings import DIAGNOSTIC_KEYS
from autoskillit.hooks._session_binding import (
    SESSION_BINDING_SCHEMA_VERSION,
    LoadedSkillEntry,
    SessionBinding,
    resolve_binding_path,
    write_binding,
)
from autoskillit.server.tools.tools_kitchen import _declare_join_batch as declare_module
from autoskillit.workspace import (
    DefaultSkillResolver,
    EffectiveSkillCatalog,
    SkillCatalogEntry,
    project_default_plugin_authority,
)
from tests.conftest import production_interpreter_env

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium]


def _entry(
    skill_name: str = "rectify",
    *,
    join_required: bool = True,
    count: int = 1,
) -> LoadedSkillEntry:
    return LoadedSkillEntry(
        skill_name=skill_name,
        ts="2026-08-26T00:00:00+00:00",
        join_required=join_required,
        child_spawn_cardinality={"workers": count},
        semantic_digest="semantic",
        adaptation_digest="adaptation",
        projected_digest="projected",
        canonical_digest="canonical",
        artifact_incarnation="incarnation",
        binding_valid=True,
        binding_error=None,
    )


def _binding(
    session_id: str,
    *,
    entries: tuple[LoadedSkillEntry, ...] | None = None,
    binding_valid: bool = True,
    artifact_digest: str = "artifact-digest",
) -> SessionBinding:
    loaded = entries if entries is not None else (_entry(),)
    return SessionBinding(
        schema_version=SESSION_BINDING_SCHEMA_VERSION,
        session_id=session_id,
        join_required=any(entry.join_required for entry in loaded),
        binding_valid=binding_valid,
        artifact_digest=artifact_digest,
        loaded_skills=loaded,
    )


def _write_session_binding(
    state_root: Path,
    filename_session_id: str,
    binding: SessionBinding,
) -> Path:
    path = resolve_binding_path(str(state_root), filename_session_id)
    write_binding(path, binding)
    return path


def _capable_backend() -> SimpleNamespace:
    return SimpleNamespace(capabilities=SimpleNamespace(fixed_set_join_capable=True))


def test_handler_rejects_session_id_path_traversal(tmp_path: Path) -> None:
    result = declare_module._declare_join_batch_handler(
        "rectify", ["assignment"], "../escape", tmp_path
    )

    assert result["success"] is False
    assert "path separators" in str(result["error"])
    assert not (tmp_path / "escape.flag").exists()


def test_end_to_end_real_projection_real_hook_real_handler(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tool_ctx,
) -> None:
    home = tmp_path / "home"
    state_root = tmp_path / "state-root"
    isolated_cwd = tmp_path / "isolated-project"
    home.mkdir()
    state_root.mkdir()
    isolated_cwd.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("AUTOSKILLIT_STATE_ROOT", str(state_root))
    monkeypatch.setenv("AUTOSKILLIT_AGENT_BACKEND", "claude-code")

    skill = DefaultSkillResolver().resolve("rectify")
    assert skill is not None
    catalog = EffectiveSkillCatalog(
        skills=(SkillCatalogEntry.from_skill_info(skill),),
        execution_role=SkillExecutionRole.SESSION,
    )
    authority = project_default_plugin_authority(
        cwd=tool_ctx.project_dir,
        base_branch="develop",
        catalog=catalog,
    )

    with authority.acquire_launch_binding(
        backend=ClaudeCodeBackend(),
        load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
    ) as launch_binding:
        assert launch_binding.plugin_dir is not None
        hook_path = launch_binding.plugin_dir / "hooks" / "skill_load_post_hook.py"
        env = production_interpreter_env()
        env.update(
            {
                "AUTOSKILLIT_AGENT_BACKEND": "claude-code",
                "AUTOSKILLIT_STATE_ROOT": str(state_root),
            }
        )
        env.pop("AUTOSKILLIT_PROJECTION_MANIFEST_PATH", None)
        completed = subprocess.run(
            [sys.executable, str(hook_path)],
            input=json.dumps(
                {
                    "tool_name": "Skill",
                    "tool_input": {"skill": "autoskillit:rectify"},
                    "session_id": "session-e2e",
                    "cwd": str(isolated_cwd),
                }
            ),
            text=True,
            capture_output=True,
            cwd=isolated_cwd,
            env=env,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        additional_context = json.loads(completed.stdout)["additionalContext"]
        delivered = re.search(
            r'skill_name="([^"]+)".*session_id="([^"]+)"',
            additional_context,
        )
        assert delivered is not None
        delivered_skill_name, delivered_session_id = delivered.groups()
        assert delivered_skill_name == "rectify"
        assert delivered_session_id == "session-e2e"

        result = declare_module._declare_join_batch_handler(
            skill_name=delivered_skill_name,
            assignments=["foundation", "interface", "registry"],
            session_id=delivered_session_id,
            project_root=tool_ctx.project_dir,
        )

    assert launch_binding.closed
    assert result["success"] is True
    assert result["join_batch_id"]


@pytest.mark.parametrize(
    ("case", "expected_error"),
    [
        ("invalid_binding", "requires a valid session binding"),
        ("selected_not_join_bearing", "is not join-bearing"),
        ("skill_not_loaded", "is not loaded in this session"),
        ("backend_not_capable", "does not attest fixed_set_join_capable"),
        ("assignment_count", "declares count=2; received 1 assignments"),
        ("empty_top_level_digest", "non-empty top-level artifact_digest"),
        ("wrong_session", "requested 'requested-session', recorded 'recorded-session'"),
    ],
)
def test_each_refusal_names_a_distinct_cause(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    expected_error: str,
) -> None:
    state_root = tmp_path / "state-root"
    state_root.mkdir()
    monkeypatch.setenv("AUTOSKILLIT_STATE_ROOT", str(state_root))
    monkeypatch.setattr(declare_module, "get_backend", lambda _name: _capable_backend())
    requested_session_id = "requested-session"
    binding = _binding(requested_session_id)

    if case == "invalid_binding":
        binding = _binding(requested_session_id, binding_valid=False)
    elif case == "selected_not_join_bearing":
        binding = _binding(
            requested_session_id,
            entries=(_entry(join_required=False), _entry("other", join_required=True)),
        )
    elif case == "skill_not_loaded":
        binding = _binding(requested_session_id, entries=(_entry("other"),))
    elif case == "backend_not_capable":
        monkeypatch.setattr(
            declare_module,
            "get_backend",
            lambda _name: SimpleNamespace(
                capabilities=SimpleNamespace(fixed_set_join_capable=False)
            ),
        )
    elif case == "assignment_count":
        binding = _binding(requested_session_id, entries=(_entry(count=2),))
    elif case == "empty_top_level_digest":
        binding = _binding(requested_session_id, artifact_digest="")
    elif case == "wrong_session":
        binding = _binding("recorded-session")
        _write_session_binding(state_root, "recorded-session", binding)
        result = declare_module._declare_join_batch_handler(
            "rectify", ["assignment"], requested_session_id, tmp_path
        )
        assert result["success"] is False
        assert expected_error in str(result["error"])
        return

    binding_path = _write_session_binding(state_root, requested_session_id, binding)
    if case == "empty_top_level_digest":
        raw = json.loads(binding_path.read_text(encoding="utf-8"))
        raw["loaded_skills"][0]["artifact_digest"] = "legacy-per-skill-digest"
        binding_path.write_text(json.dumps(raw), encoding="utf-8")

    result = declare_module._declare_join_batch_handler(
        "rectify", ["assignment"], requested_session_id, tmp_path
    )

    assert result["success"] is False
    assert expected_error in str(result["error"])


def test_skill_name_matches_in_both_namespaced_and_bare_form(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_root = tmp_path / "state-root"
    state_root.mkdir()
    monkeypatch.setenv("AUTOSKILLIT_STATE_ROOT", str(state_root))
    monkeypatch.setattr(declare_module, "get_backend", lambda _name: _capable_backend())
    _write_session_binding(state_root, "session", _binding("session"))

    bare = declare_module._declare_join_batch_handler(
        "rectify", ["bare"], "session", tmp_path, top_level_parent="bare-parent"
    )
    namespaced = declare_module._declare_join_batch_handler(
        "autoskillit:rectify",
        ["namespaced"],
        "session",
        tmp_path,
        top_level_parent="namespaced-parent",
    )
    cardinality_violation = declare_module._declare_join_batch_handler(
        "autoskillit:rectify",
        ["one", "two"],
        "session",
        tmp_path,
        top_level_parent="cardinality-parent",
    )

    assert bare["success"] is True
    assert namespaced["success"] is True
    assert bare["wave"]["skill_name"] == "rectify"
    assert namespaced["wave"]["skill_name"] == "rectify"
    assert cardinality_violation["success"] is False
    assert "declares count=1; received 2 assignments" in str(cardinality_violation["error"])


@pytest.mark.parametrize("case", ["single_candidate", "typed_mismatch", "ambiguous"])
def test_wrong_session_id_is_reported_as_such(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    state_root = tmp_path / "state-root"
    state_root.mkdir()
    monkeypatch.setenv("AUTOSKILLIT_STATE_ROOT", str(state_root))
    diagnostics: list[dict[str, object]] = []
    monkeypatch.setattr(declare_module, "_emit_join_diagnostic", diagnostics.append)

    if case == "single_candidate":
        _write_session_binding(state_root, "recorded", _binding("recorded"))
    elif case == "typed_mismatch":
        _write_session_binding(state_root, "requested", _binding("recorded"))
    else:
        _write_session_binding(state_root, "recorded-a", _binding("recorded-a"))
        _write_session_binding(state_root, "recorded-b", _binding("recorded-b"))

    result = declare_module._declare_join_batch_handler(
        "rectify", ["assignment"], "requested", tmp_path
    )

    assert result["success"] is False
    assert "requested" in str(result["error"])
    if case == "ambiguous":
        assert "ambiguous" in str(result["error"])
    else:
        assert "recorded" in str(result["error"])
    assert diagnostics
    assert set(diagnostics[-1]) <= DIAGNOSTIC_KEYS
    assert diagnostics[-1]["status"] in {
        "wrong_session_id",
        "ambiguous_session_bindings",
    }


def test_ledger_and_binding_share_the_state_root_aware_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_root = tmp_path / "external-state-root"
    state_root.mkdir()
    monkeypatch.setenv("AUTOSKILLIT_STATE_ROOT", str(state_root))
    _write_session_binding(state_root, "session", _binding("session"))
    resolved_paths: list[Path] = []
    ledger_dirs: list[Path] = []
    original_resolver = declare_module.resolve_binding_path

    def resolve_spy(payload_cwd: str, session_id: str) -> Path:
        path = original_resolver(payload_cwd, session_id)
        resolved_paths.append(path)
        return path

    def declare_spy(channel_dir: Path, **kwargs):
        ledger_dirs.append(channel_dir)
        return {"join_batch_id": "batch-id", **kwargs}

    monkeypatch.setattr(declare_module, "resolve_binding_path", resolve_spy)
    monkeypatch.setattr(declare_module, "declare_batch", declare_spy)
    monkeypatch.setattr(declare_module, "get_backend", lambda _name: _capable_backend())

    result = declare_module._declare_join_batch_handler(
        "rectify", ["assignment"], "session", tmp_path
    )

    assert result["success"] is True
    assert resolved_paths[0].parent == ledger_dirs[0]
    assert ledger_dirs[0] == state_root / ".autoskillit" / "temp"
