"""Meta-test: every ADR that constrains tool calls has a registered runtime guard.

The mapping is explicit (not a markdown scanner) because deciding whether an ADR
requires a PreToolUse guard is a human judgment call. Update ADR_GUARD_MAPPING when
a new ADR-guard pair is created.
"""

from __future__ import annotations

from pathlib import Path

from autoskillit.hook_registry import HOOK_REGISTRY, HOOKS_DIR

_DECISIONS_DIR = Path(__file__).parents[2] / "docs" / "decisions"
_INFRA_TEST_DIR = Path(__file__).parent

# Explicit mapping: ADR filename → guard script path (as it appears in HookDef.scripts)
ADR_GUARD_MAPPING: dict[str, str] = {
    "0001-prohibit-background-subagent-execution.md": "guards/background_exec_guard.py",
}


def _all_hook_scripts() -> list[str]:
    scripts: list[str] = []
    for hookdef in HOOK_REGISTRY:
        scripts.extend(hookdef.scripts)
    return scripts


def test_every_adr_with_constraint_has_guard():
    all_scripts = _all_hook_scripts()
    for adr_file, guard_path in ADR_GUARD_MAPPING.items():
        assert guard_path in all_scripts, (
            f"ADR {adr_file} requires runtime guard {guard_path!r}, "
            f"but it is not registered in any HookDef.scripts in HOOK_REGISTRY."
        )


def test_guard_script_exists_on_disk():
    for adr_file, guard_path in ADR_GUARD_MAPPING.items():
        full_path = HOOKS_DIR / guard_path
        assert full_path.exists(), (
            f"Guard script {guard_path!r} (required by ADR {adr_file}) "
            f"does not exist at {full_path}."
        )


def test_guard_has_test_file():
    for adr_file, guard_path in ADR_GUARD_MAPPING.items():
        guard_stem = Path(guard_path).stem
        test_file = _INFRA_TEST_DIR / f"test_{guard_stem}.py"
        assert test_file.exists(), (
            f"Guard {guard_path!r} (required by ADR {adr_file}) has no test file at {test_file}."
        )


def test_adr_file_exists():
    for adr_file in ADR_GUARD_MAPPING:
        full_path = _DECISIONS_DIR / adr_file
        assert full_path.exists(), (
            f"ADR file {adr_file!r} does not exist at {full_path}. "
            "Update ADR_GUARD_MAPPING if the file was renamed."
        )
