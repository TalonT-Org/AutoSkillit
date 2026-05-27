"""Structural test: every risky gh subcommand pair must have PreToolUse guard coverage.

Imports RISKY_GH_SUBCOMMANDS from hook_registry and verifies that for each
(sub1, sub2) pair, at least one Bash|run_cmd-matching guard contains detection
logic for that subcommand pair.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

from autoskillit.hook_registry import HOOK_REGISTRY, RISKY_GH_SUBCOMMANDS

pytestmark = [pytest.mark.layer("infra"), pytest.mark.small]

_GUARDS_DIR = Path(__file__).parent.parent.parent / "src" / "autoskillit" / "hooks" / "guards"


def _command_inspecting_guard_scripts() -> list[tuple[str, Path]]:
    """Return (logical_name, path) for guards registered under Bash|run_cmd matchers."""
    found: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for hook_def in HOOK_REGISTRY:
        if hook_def.event_type != "PreToolUse":
            continue
        if not re.fullmatch(hook_def.matcher, "Bash"):
            continue
        for script in hook_def.scripts:
            if not script.startswith("guards/"):
                continue
            guard_name = Path(script).stem
            if guard_name in seen:
                continue
            seen.add(guard_name)
            script_path = _GUARDS_DIR / f"{guard_name}.py"
            if script_path.exists():
                found.append((guard_name, script_path))
    return found


def _load_guard_detection_frozensets(guard_name: str) -> list[frozenset]:
    """Import a guard module and collect all frozenset-of-tuples module-level attributes."""
    spec = importlib.util.spec_from_file_location(
        f"_guard_{guard_name}",
        _GUARDS_DIR / f"{guard_name}.py",
    )
    if spec is None or spec.loader is None:
        return []
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)  # type: ignore[attr-defined]
    except Exception:
        return []
    sets: list[frozenset] = []
    for attr_name in dir(mod):
        val = getattr(mod, attr_name, None)
        if isinstance(val, frozenset) and val:
            sample = next(iter(val))
            if isinstance(sample, tuple):
                sets.append(val)
    return sets


def test_every_risky_gh_subcommand_has_guard_coverage() -> None:
    """For each risky gh subcommand pair, at least one Bash-matching guard detects it.

    Scans source files of guards registered under Bash|run_cmd matchers for
    string literals matching both tokens of each risky pair.
    """
    guard_scripts = _command_inspecting_guard_scripts()
    assert guard_scripts, "Expected at least one command-inspecting guard registered for Bash"

    uncovered: list[tuple[str, str]] = []
    for pair in RISKY_GH_SUBCOMMANDS:
        sub1, sub2 = pair
        covered = False
        for _guard_name, script_path in guard_scripts:
            source = script_path.read_text()
            if (f'"{sub1}"' in source and f'"{sub2}"' in source) or (
                f"'{sub1}'" in source and f"'{sub2}'" in source
            ):
                covered = True
                break
        if not covered:
            uncovered.append(pair)

    assert not uncovered, (
        f"Risky gh subcommand pairs without guard coverage: {sorted(uncovered)}. "
        f"Add detection to an existing guard or create a new one, then register it "
        f"in HOOK_REGISTRY under a Bash|run_cmd matcher."
    )


def test_risky_subcommands_covered_by_guard_detection_sets() -> None:
    """Guards that expose detection frozensets must include all relevant risky pairs.

    Collects all frozenset-of-tuples attributes from command-inspecting guards via
    importlib and asserts that the download subcommand pairs from RISKY_GH_SUBCOMMANDS
    appear in the union. This enforces that artifact_download_guard (and any future
    similar guard) exports its detection set as an importable frozenset rather than
    embedding it as anonymous inline comparisons.

    Guards that detect via inline string matching (e.g. pr_create_guard) are excluded
    from this check; they are covered by test_every_risky_gh_subcommand_has_guard_coverage.
    """
    guard_scripts = _command_inspecting_guard_scripts()

    union_of_detected_pairs: set[tuple[str, str]] = set()
    for guard_name, _ in guard_scripts:
        for fs in _load_guard_detection_frozensets(guard_name):
            for item in fs:
                if isinstance(item, tuple) and len(item) == 2:
                    union_of_detected_pairs.add(item)  # type: ignore[arg-type]

    # Download pairs must appear in at least one guard's exported detection frozenset.
    # These are the pairs covered by artifact_download_guard._DOWNLOAD_SUBCOMMANDS.
    # If this fails, the guard's detection set is missing the risky pair.
    download_pairs: frozenset[tuple[str, str]] = frozenset(
        p for p in RISKY_GH_SUBCOMMANDS if p[1] == "download"
    )
    missing = download_pairs - frozenset(union_of_detected_pairs)

    assert not missing, (
        f"Download subcommand pairs not found in any guard's detection frozenset: "
        f"{sorted(missing)}. "
        f"Add the pair to artifact_download_guard._DOWNLOAD_SUBCOMMANDS or equivalent."
    )
