"""Tests for preserving an order launch registry identity across resumes."""

from __future__ import annotations

from pathlib import Path

import pytest

import autoskillit.cli.session._session_order as _patch_session__session_order
import autoskillit.cli.ui._menu as _patch_ui__menu
from autoskillit import cli
from autoskillit.core import (
    LAUNCH_ID_ENV_VAR,
    SESSION_TYPE_ENV_VAR,
    RestoreSession,
    SessionType,
)
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

    def write_entry(project_dir: Path, recipe_name: str | None) -> tuple[str, dict[str, str]]:
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


def test_resumed_order_claims_original_entry_and_preserves_launch_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A resumed order uses the claimed launch id in both launch identity env values."""
    monkeypatch.chdir(tmp_path)
    _add_recipe(tmp_path)
    claimed: list[tuple[Path, dict[str, object]]] = []
    released: list[tuple[Path, str]] = []
    captured: dict[str, object] = {}

    def claim(project_dir: Path, **kwargs: object) -> str:
        claimed.append((project_dir, kwargs))
        return "original-order-launch"

    def release(project_dir: Path, launch_id: str) -> None:
        released.append((project_dir, launch_id))

    def launch(**kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(_patch_session__session_order, "claim_launch_for_session", claim)
    monkeypatch.setattr(_patch_session__session_order, "release_session_claim", release)
    monkeypatch.setattr(_patch_session__session_order, "_launch_cook_session", launch)

    cli.order("test-script", resume=True, session_id=_SESSION_ID)

    assert claimed == [
        (
            tmp_path,
            {
                "claude_session_id": _SESSION_ID,
                "session_type": "order",
                "recipe_name": "test-script",
            },
        )
    ]
    assert captured["launch"] == RestoreSession(session_id=_SESSION_ID)
    assert captured["launch_id"] == "original-order-launch"
    assert captured["extra_env"] == {
        SESSION_TYPE_ENV_VAR: SessionType.ORCHESTRATOR.value,
        LAUNCH_ID_ENV_VAR: "original-order-launch",
    }
    assert released == [(tmp_path, "original-order-launch")]


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
