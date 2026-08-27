"""Contract coverage for versioned hook-channel artifacts."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from autoskillit.hooks import _join_ledger
from autoskillit.workspace._projected_artifact import _hook_repair

pytestmark = [pytest.mark.small]


def test_join_ledger_refuses_an_unsupported_schema_version(tmp_path: Path) -> None:
    """A ledger from an unknown schema must not be treated as current state."""
    ledger_path = tmp_path / _join_ledger.LEDGER_FILENAME
    ledger_path.write_text(
        json.dumps({"schema_version": 999, "sessions": {}}),
        encoding="utf-8",
    )

    with pytest.raises(
        _join_ledger._CorruptedLedger,
        match="unsupported join ledger schema_version",
    ):
        _join_ledger._read_locked(ledger_path)

    with pytest.raises(
        _join_ledger.JoinLedgerError,
        match="unsupported join ledger schema_version",
    ):
        _join_ledger.claim_assignment(
            tmp_path,
            session_id="session",
            top_level_parent="parent",
            tool_use_id="tool-use",
        )


def test_projection_hook_repair_refuses_an_unsupported_manifest_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repair must leave a projection untouched when its manifest is unversioned or future."""
    projection_dir = tmp_path / "projection"
    hooks_json_path = projection_dir / "hooks" / "hooks.json"
    hooks_json_path.parent.mkdir(parents=True)
    original_hooks = '{"hooks": {}}\n'
    hooks_json_path.write_text(original_hooks, encoding="utf-8")
    manifest_path = _hook_repair.projected_artifact_manifest_path(projection_dir)
    original_manifest = '{"schema_version": 999}\n'
    manifest_path.write_text(original_manifest, encoding="utf-8")

    checks = 0

    def broken_hooks(*_args: object, **_kwargs: object) -> list[str]:
        nonlocal checks
        checks += 1
        return ["broken"] if checks < 3 else []

    monkeypatch.setattr(_hook_repair, "find_broken_hook_scripts", broken_hooks)
    monkeypatch.setattr(_hook_repair, "_relocate_existing_hooks", lambda _data: {"hooks": {}})

    outcomes = _hook_repair.repair_broken_projection_hooks(tmp_path)

    assert len(outcomes) == 1
    assert outcomes[0].status is _hook_repair.PluginHookRepairStatus.FAILED
    assert "schema" in (outcomes[0].detail or "").lower()
    assert hooks_json_path.read_text(encoding="utf-8") == original_hooks
    assert manifest_path.read_text(encoding="utf-8") == original_manifest


def test_projection_hook_repair_uses_versioned_manifest_helpers() -> None:
    """Manifest repair must stay on the version-validating helper path."""
    source_path = Path(_hook_repair.__file__)
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    repair = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "repair_broken_projection_hooks"
    )
    versioned_calls = [
        node
        for node in ast.walk(repair)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"read_versioned_json", "write_versioned_json"}
    ]

    assert {call.func.id for call in versioned_calls} == {
        "read_versioned_json",
        "write_versioned_json",
    }
    for call in versioned_calls:
        assert len(call.args) >= 2
        assert ast.unparse(call.args[0]) == "manifest_path"
        assert ast.unparse(call.args[1]) == "PROJECTION_ARTIFACT_MANIFEST_SCHEMA_VERSION"
