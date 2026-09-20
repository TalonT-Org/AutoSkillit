"""Tests for preserving an order launch registry identity across resumes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import autoskillit.cli.session._session_order as _patch_session__session_order
import autoskillit.cli.ui._menu as _patch_ui__menu
import autoskillit.execution.backends._claude_session_locator as _locator_module
from autoskillit import cli
from autoskillit.cli.session._session_constants import SESSION_TYPE_COOK
from autoskillit.cli.session._session_launch_intent import pick_session
from autoskillit.core import (
    LAUNCH_ID_ENV_VAR,
    SESSION_TYPE_ENV_VAR,
    RestoreSession,
    SessionType,
    bridge_claude_session_id,
    read_registry,
    release_session_claim,
    write_registry_entry,
)
from autoskillit.execution.backends import ClaudeSessionLocator
from autoskillit.hooks.guards.open_kitchen_guard import _bridge_session_registry
from tests.cli.conftest import _SCRIPT_YAML

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium]

_SESSION_ID = "fa910a41-d1ca-4cae-b878-01028a0c7c1c"


@pytest.fixture(autouse=True)
def _interactive_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)


def _add_recipe(project_dir: Path) -> None:
    recipes_dir = project_dir / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True)
    (recipes_dir / "test-script.yaml").write_text(_SCRIPT_YAML)


def _stage_claude_index(
    monkeypatch: pytest.MonkeyPatch,
    project_dir: Path,
    session_id: str,
) -> ClaudeSessionLocator:
    index_dir = project_dir / "claude-index"
    index_dir.mkdir()
    (index_dir / "sessions-index.json").write_text(
        json.dumps(
            [
                {
                    "sessionId": session_id,
                    "cwd": str(project_dir),
                    "firstPrompt": "Cook session",
                    "summary": "",
                    "gitBranch": "develop",
                    "modified": None,
                    "isSidechain": False,
                }
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        _locator_module,
        "claude_code_project_dir",
        lambda _cwd: index_dir,
    )
    return ClaudeSessionLocator()


def test_fresh_order_writes_a_new_registry_entry_without_a_resume_claim(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Fresh open-kitchen order launch writes, rather than claiming, its own entry."""
    from autoskillit.cli.ui._menu import SLOT_ZERO_SELECTED

    monkeypatch.chdir(tmp_path)
    _add_recipe(tmp_path)
    captured: dict[str, object] = {}
    released: list[tuple[Path, str]] = []

    def write_entry(
        project_dir: Path,
        recipe_name: str | None,
        managed_join_parent_id: str | None = None,
    ) -> tuple[str, dict[str, str]]:
        del managed_join_parent_id
        captured["write"] = (project_dir, recipe_name)
        return "fresh-launch", {
            SESSION_TYPE_ENV_VAR: SessionType.ORCHESTRATOR.value,
            LAUNCH_ID_ENV_VAR: "fresh-launch",
        }

    def launch(**kwargs: object) -> None:
        captured["launch"] = kwargs

    monkeypatch.setattr(
        _patch_ui__menu,
        "run_selection_menu",
        lambda *_args, **_kwargs: SLOT_ZERO_SELECTED,
    )
    monkeypatch.setattr(_patch_session__session_order, "_write_order_entry", write_entry)
    monkeypatch.setattr(
        _patch_session__session_order,
        "release_session_claim",
        lambda project_dir, launch_id: released.append((project_dir, launch_id)),
    )
    monkeypatch.setattr(
        _patch_session__session_order,
        "claim_launch_for_session",
        lambda *_args, **_kwargs: pytest.fail("fresh order must not claim a launch entry"),
    )
    monkeypatch.setattr(_patch_session__session_order, "_launch_cook_session", launch)

    cli.order()

    assert captured["write"] == (tmp_path, None)
    launch_kwargs = captured["launch"]
    assert isinstance(launch_kwargs, dict)
    assert launch_kwargs["launch_id"] == "fresh-launch"
    assert launch_kwargs["extra_env"] == {
        SESSION_TYPE_ENV_VAR: SessionType.ORCHESTRATOR.value,
        LAUNCH_ID_ENV_VAR: "fresh-launch",
    }
    assert released == [(tmp_path, "fresh-launch")]


def test_order_resume_preserves_cook_identity_through_hook_and_real_picker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    _add_recipe(tmp_path)
    captured: dict[str, object] = {}

    write_registry_entry(tmp_path, "cook-launch", SESSION_TYPE_COOK, None)
    bridge_claude_session_id(tmp_path, "cook-launch", _SESSION_ID)
    assert release_session_claim(tmp_path, "cook-launch")
    monkeypatch.setattr(
        _patch_session__session_order,
        "_launch_cook_session",
        lambda **kwargs: captured.update(kwargs),
    )

    cli.order("test-script", resume=True, session_id=_SESSION_ID)

    extra_env = captured["extra_env"]
    assert isinstance(extra_env, dict)
    launch_id = extra_env[LAUNCH_ID_ENV_VAR]
    assert launch_id == "cook-launch"
    monkeypatch.setenv("AUTOSKILLIT_LAUNCH_ID", launch_id)
    monkeypatch.setenv("AUTOSKILLIT_STATE_ROOT", str(tmp_path))
    _bridge_session_registry(_SESSION_ID, str(tmp_path))

    locator = _stage_claude_index(monkeypatch, tmp_path, _SESSION_ID)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "1")
    registry = read_registry(tmp_path)

    assert len(registry) == 1
    assert registry["cook-launch"]["claude_session_id"] == _SESSION_ID
    assert registry["cook-launch"]["session_type"] == SESSION_TYPE_COOK
    assert pick_session(SESSION_TYPE_COOK, tmp_path, locator) == _SESSION_ID


def test_resumed_order_claims_real_row_without_changing_row_count(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    _add_recipe(tmp_path)
    captured: dict[str, object] = {}
    write_registry_entry(
        tmp_path,
        "cook-launch",
        SESSION_TYPE_COOK,
        None,
        claude_session_id=_SESSION_ID,
    )
    assert release_session_claim(tmp_path, "cook-launch")
    row_count = len(read_registry(tmp_path))
    monkeypatch.setattr(
        _patch_session__session_order,
        "_launch_cook_session",
        lambda **kwargs: captured.update(kwargs),
    )

    cli.order("test-script", resume=True, session_id=_SESSION_ID)

    assert captured["launch"] == RestoreSession(session_id=_SESSION_ID)
    assert captured["launch_id"] == "cook-launch"
    assert captured["extra_env"] == {
        SESSION_TYPE_ENV_VAR: SessionType.ORCHESTRATOR.value,
        LAUNCH_ID_ENV_VAR: "cook-launch",
    }
    assert len(read_registry(tmp_path)) == row_count == 1


def test_unknown_resume_is_identified_before_launch_handoff(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    observed: dict[str, object] = {}

    def inspect_before_handoff(**kwargs: object) -> None:
        observed["launch"] = kwargs
        observed["registry"] = read_registry(tmp_path)

    monkeypatch.setattr(
        _patch_session__session_order,
        "_launch_cook_session",
        inspect_before_handoff,
    )

    cli.order(resume=True, session_id=_SESSION_ID)

    registry = observed["registry"]
    assert isinstance(registry, dict)
    assert len(registry) == 1
    row = next(iter(registry.values()))
    assert row["claude_session_id"] == _SESSION_ID
    launch_kwargs = observed["launch"]
    assert isinstance(launch_kwargs, dict)
    launch_env = launch_kwargs["extra_env"]
    assert isinstance(launch_env, dict)
    assert launch_env[LAUNCH_ID_ENV_VAR] in registry


def test_resumed_order_releases_claim_when_launch_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The resume claim is released even when managed or raw launch setup raises."""
    monkeypatch.chdir(tmp_path)
    released: list[tuple[Path, str]] = []
    expected = RuntimeError("interactive binding failed")

    monkeypatch.setattr(
        _patch_session__session_order,
        "claim_launch_for_session",
        lambda *_args, **_kwargs: "original-order-launch",
    )
    monkeypatch.setattr(
        _patch_session__session_order,
        "release_session_claim",
        lambda project_dir, launch_id: released.append((project_dir, launch_id)),
    )
    monkeypatch.setattr(
        _patch_session__session_order,
        "_launch_cook_session",
        lambda **_kwargs: (_ for _ in ()).throw(expected),
    )

    with pytest.raises(RuntimeError) as caught:
        cli.order(resume=True, session_id=_SESSION_ID)

    assert caught.value is expected
    assert released == [(tmp_path, "original-order-launch")]
