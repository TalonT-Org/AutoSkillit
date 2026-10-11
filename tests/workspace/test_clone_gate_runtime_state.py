"""Git visibility of AutoSkillit-owned state during clone checks."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

from autoskillit.core import (
    ManagedHeadlessSessionKind,
    NativeShellCaptureMode,
    resolve_native_shell_capture_decision,
)
from autoskillit.core.io import _AUTOSKILLIT_GITIGNORE_ENTRIES, ensure_project_temp
from autoskillit.execution.session import DefaultManagedHeadlessSessionLineageStore
from autoskillit.workspace.clone import detect_uncommitted_changes
from tests._git_topology import commit_file

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.medium]


@pytest.fixture(autouse=True)
def _isolate_git_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


def _initialized(repo: Path) -> Path:
    commit_file(
        repo,
        ".autoskillit/.gitignore",
        "\n".join(_AUTOSKILLIT_GITIGNORE_ENTRIES) + "\n",
        "add AutoSkillit ignore entries",
    )
    commit_file(repo, ".autoskillit/config.yaml", "project: probe\n", "add project config")
    return repo


def _create_record(anchor: Path):
    return DefaultManagedHeadlessSessionLineageStore().create(
        lineage_anchor=anchor,
        launch_id=uuid.uuid4().hex,
        decision=resolve_native_shell_capture_decision(NativeShellCaptureMode.CAPTURE),
        backend="claude-code",
        session_kind=ManagedHeadlessSessionKind.FOOD_TRUCK,
        dispatch_id=str(uuid.uuid4()),
    )


def test_launch_sequence_leaves_checkout_clean_and_user_edits_still_block(git_repo: Path) -> None:
    repo = _initialized(git_repo)
    gitignore_path = repo / ".autoskillit" / ".gitignore"
    committed_gitignore = gitignore_path.read_bytes()

    ensure_project_temp(repo)
    _create_record(repo)

    records_dir = repo / ".autoskillit" / "managed-headless-session-lineage" / "records"
    assert records_dir.is_dir() and any(records_dir.iterdir())
    assert detect_uncommitted_changes(str(repo)) == []

    (repo / "notes.txt").write_text("keep this note\n", encoding="utf-8")
    config_path = repo / ".autoskillit" / "config.yaml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8") + "user_setting: retained\n",
        encoding="utf-8",
    )
    recipe_path = repo / ".autoskillit" / "recipes" / "mine.yaml"
    recipe_path.parent.mkdir(parents=True, exist_ok=True)
    recipe_path.write_text("name: mine\n", encoding="utf-8")

    changed = detect_uncommitted_changes(str(repo))
    assert "?? notes.txt" in changed
    assert " M .autoskillit/config.yaml" in changed
    assert "?? .autoskillit/recipes/" in changed
    assert gitignore_path.read_bytes() == committed_gitignore


def test_legacy_namespace_is_repaired_by_a_store_read(git_repo: Path) -> None:
    repo = _initialized(git_repo)
    lineage = _create_record(repo)
    namespace = repo / ".autoskillit" / "managed-headless-session-lineage"
    (namespace / ".gitignore").unlink(missing_ok=True)

    assert detect_uncommitted_changes(str(repo))
    DefaultManagedHeadlessSessionLineageStore().load(
        lineage_anchor=repo,
        launch_id=lineage.launch_id,
    )
    assert detect_uncommitted_changes(str(repo)) == []
