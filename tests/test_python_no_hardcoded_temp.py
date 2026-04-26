"""Architectural invariant: no literal ``.autoskillit/temp`` outside the whitelist.

Every Python file under ``src/autoskillit/`` is walked. The literal substring
``.autoskillit/temp`` may only appear in files listed in
``_TEMP_PATH_WHITELIST``. Each whitelisted entry carries a justification —
adding new entries requires adding a justification too.
"""

from __future__ import annotations

from pathlib import Path

# Each entry: relative path under src/autoskillit/ → justification.
# When adding a new entry, document WHY the literal is allowed there.
_TEMP_PATH_WHITELIST: dict[str, str] = {
    # Justification: canonical default literal used by resolve_temp_dir +
    # temp_dir_display_str (the single source of truth).
    "core/io.py": "canonical default literal used by resolve_temp_dir",
    # Justification: load_recipe() default parameter value (matches resolver default).
    "recipe/io.py": "load_recipe default arg matches canonical default",
    # Justification: default fallback values for temp_dir_relpath in load_and_validate
    # / _build_active_recipe / validate_from_path (mirrors canonical default).
    "recipe/_api.py": "fallback default for temp_dir_relpath kwargs",
    # Justification: validate_from_path signature now includes temp_dir_relpath with
    # the canonical default value, mirroring the _api.py pattern.
    "recipe/repository.py": "validate_from_path default arg mirrors canonical default",
    # Justification: RecipeRepository protocol method includes temp_dir_relpath with
    # the canonical default value for backward compatibility.
    "core/_type_protocols.py": "RecipeRepository.validate_from_path default arg",
    # Justification: SkillsDirectoryProvider default ctor arg matches canonical default.
    "workspace/session_skills.py": "SkillsDirectoryProvider default arg",
    # Justification: docstring example referencing the canonical default path.
    "hooks/skill_cmd_guard.py": "docstring example",
    # Justification: docstring referencing the canonical default path.
    "workspace/worktree.py": "docstring example",
    # Justification: docstring referencing the canonical default path.
    "server/tools_clone.py": "docstring example",
    # Justification: stdlib-only hook module that cannot import resolve_temp_dir().
    # HOOK_DIR_COMPONENTS = (".autoskillit", "temp") mirrors the canonical bridge path
    # defined by _fmt_primitives._HOOK_CONFIG_PATH_COMPONENTS.
    "hooks/_hook_settings.py": "stdlib-only hook; cannot use resolve_temp_dir()",
    # Justification: sidecar path for worktree base branch detection; reads from
    # <project_root>/.autoskillit/temp/worktrees/<wt_name>/base-branch which is
    # written by implement-worktree skills and must match the canonical layout.
    "execution/testing.py": "sidecar path for worktree base branch detection",
    # Justification: reads fleet dispatch state files from the canonical temp dir
    # (<project_root>/.autoskillit/temp/dispatches/) to determine which campaign IDs
    # have active dispatches; owned by the fleet layer after the L1→L2 refactor.
    "fleet/state.py": "reads fleet dispatch state from canonical temp dir",
    # Justification: docstring for _write_hook_config() references the canonical
    # hook config path so callers know where the file is written.
    "server/tools_kitchen.py": "docstring example",
}

_LITERAL = ".autoskillit/temp"


def _src_root() -> Path:
    # tests/test_python_no_hardcoded_temp.py → repo root
    return Path(__file__).resolve().parent.parent / "src" / "autoskillit"


def test_no_hardcoded_temp_path_in_python_outside_resolver() -> None:
    src = _src_root()
    assert src.is_dir(), f"src/autoskillit not found at {src}"

    offenders: list[str] = []
    for py in src.rglob("*.py"):
        rel = py.relative_to(src).as_posix()
        if rel in _TEMP_PATH_WHITELIST:
            continue
        text = py.read_text(encoding="utf-8")
        if _LITERAL in text:
            offenders.append(rel)

    assert not offenders, (
        f"Python files contain literal {_LITERAL!r} but are not in the whitelist: "
        f"{offenders}. Either replace with resolve_temp_dir(...) or add an entry "
        f"to _TEMP_PATH_WHITELIST in tests/test_python_no_hardcoded_temp.py with "
        f"a justification."
    )


def test_whitelist_entries_actually_contain_the_literal() -> None:
    """Whitelist hygiene: an entry that no longer contains the literal must be removed."""
    src = _src_root()
    stale: list[str] = []
    for rel in _TEMP_PATH_WHITELIST:
        path = src / rel
        if not path.is_file():
            stale.append(f"{rel} (file missing)")
            continue
        if _LITERAL not in path.read_text(encoding="utf-8"):
            stale.append(rel)
    assert not stale, (
        f"Whitelist entries no longer contain {_LITERAL!r}: {stale}. "
        "Remove them from _TEMP_PATH_WHITELIST."
    )
