"""Interactive review writes use the loaded skill's projected boundary."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from autoskillit.hook_registry import HOOKS_DIR
from autoskillit.hooks._session_binding import (
    loaded_skill_from_manifest,
    merge_binding,
    resolve_binding_path,
    write_binding,
)
from tests.conftest import production_interpreter_env

pytestmark = [pytest.mark.layer("skills"), pytest.mark.medium]


def _runtime(tmp_path: Path, boundaries: dict[str, list[str] | None]) -> tuple[Path, Path]:
    project = tmp_path / "project"
    (project / ".autoskillit").mkdir(parents=True)
    plugin = tmp_path / "plugin"
    shutil.copytree(HOOKS_DIR, plugin / "hooks")
    manifest = {
        "schema_version": 2,
        "artifact_digest": "test-artifact",
        "incarnation_id": "test-incarnation",
        "skills": {name: {"write_paths": paths} for name, paths in boundaries.items()},
    }
    (tmp_path / ".plugin.autoskillit-projection.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    return project, plugin


def _load(
    project: Path, tmp_path: Path, names: tuple[str, ...], session_id: str = "review"
) -> None:
    manifest = json.loads(
        (tmp_path / ".plugin.autoskillit-projection.json").read_text(encoding="utf-8")
    )
    binding = None
    for name in names:
        entry = loaded_skill_from_manifest(manifest, name, "2026-09-20T00:00:00Z")
        binding = merge_binding(
            binding,
            session_id=session_id,
            new_entry=entry,
            artifact_digest="test-artifact",
        )
    assert binding is not None
    write_binding(resolve_binding_path(str(project), "review"), binding)


def _run(plugin: Path, project: Path, target: Path, script: str = "write_guard.py") -> str:
    env = {
        **production_interpreter_env(),
        "AUTOSKILLIT_HEADLESS": "",
        "AUTOSKILLIT_AGENT_BACKEND": "claude-code",
        "AUTOSKILLIT_ALLOWED_WRITE_PREFIX": "",
        "AUTOSKILLIT_ALLOWED_WRITE_PREFIXES": "",
        "AUTOSKILLIT_STATE_ROOT": str(project),
    }
    result = subprocess.run(
        [sys.executable, str(plugin / "hooks" / "guards" / script)],
        input=json.dumps(
            {
                "tool_name": "Write",
                "session_id": "review",
                "cwd": str(project),
                "tool_input": {"file_path": str(target)},
            }
        ),
        capture_output=True,
        text=True,
        env=env,
        cwd=project,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def _denied(stdout: str) -> bool:
    if not stdout:
        return False
    return json.loads(stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_review_pr_boundary_denies_outside_and_allows_inside(tmp_path: Path) -> None:
    project, plugin = _runtime(tmp_path, {"review-pr": ["{{AUTOSKILLIT_TEMP}}/review-pr/"]})
    _load(project, tmp_path, ("review-pr",))

    assert _denied(_run(plugin, project, project / "src" / "outside.py"))
    assert not _denied(_run(plugin, project, project / ".autoskillit/temp/review-pr/report.md"))


def test_missing_or_stale_binding_leaves_skill_scope_inactive(tmp_path: Path) -> None:
    project, plugin = _runtime(tmp_path, {"review-pr": ["{{AUTOSKILLIT_TEMP}}/review-pr/"]})
    ordinary = project / "src" / "ordinary.py"
    assert not _denied(_run(plugin, project, ordinary))
    protected = tmp_path / "site-packages/autoskillit/__init__.py"
    assert _denied(_run(plugin, project, protected, "installation_integrity_guard.py"))

    _load(project, tmp_path, ("review-pr",), session_id="stale")
    assert not _denied(_run(plugin, project, ordinary))


@pytest.mark.parametrize(
    ("second_boundary", "target_suffix", "allowed"),
    [
        (["{{AUTOSKILLIT_TEMP}}/review-pr/nested/"], "review-pr/nested/ok.md", True),
        (["{{AUTOSKILLIT_TEMP}}/review-pr/nested/"], "review-pr/outer.md", False),
        (["{{AUTOSKILLIT_TEMP}}/other/"], "review-pr/nested/ok.md", False),
        ([], "review-pr/nested/ok.md", False),
        (None, "review-pr/nested/ok.md", True),
    ],
)
def test_loaded_skill_boundaries_only_narrow(
    tmp_path: Path, second_boundary: list[str] | None, target_suffix: str, allowed: bool
) -> None:
    project, plugin = _runtime(
        tmp_path,
        {
            "review-pr": ["{{AUTOSKILLIT_TEMP}}/review-pr/"],
            "second": second_boundary,
        },
    )
    _load(project, tmp_path, ("review-pr", "second"))
    target = project / ".autoskillit" / "temp" / target_suffix
    assert _denied(_run(plugin, project, target)) is not allowed
