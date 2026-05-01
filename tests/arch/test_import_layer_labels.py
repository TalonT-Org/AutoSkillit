"""Regression guard: no bare L-number labels in import-layer contexts.

After the IL-N rename (issue #1574), module docstrings and inline comments
must use IL-0/IL-1/IL-2/IL-3 for import-layer annotations.  This test
scans src/autoskillit/ Python files for the patterns that indicate an
import-layer usage and fails if any bare L-number labels remain.
"""

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


# Patterns that indicate "this L-number is import-layer, not orchestration"
_IMPORT_LAYER_PATTERNS = re.compile(
    r"""
    (?:
        # "(L0)", "(L1)", "(L2)", "(L3)" in isolation
        \(L[0-3]\)
        |
        # "L0 module", "L1 module", etc.
        \bL[0-3]\s+module\b
        |
        # "L0 foundation", "L1 service"
        \bL[0-3]\s+(?:foundation|service|module|layer|contract|peer)\b
        |
        # "L0 core", "L1 config", "L2 recipe", etc.
        \bL[0-3]\s+(?:core|config|pipeline|execution|workspace|recipe|migration|fleet|server|cli)\b
        |
        # "imports only from L0"
        imports\s+only\s+(?:from\s+)?L[0-3]\b
        |
        # "any L1+ layer"
        \bL[0-3]\+\s+layer\b
        |
        # "L0-accessible"
        \bL[0-3]-accessible\b
        |
        # "at L0 (core/)"
        \bat\s+L[0-3]\s+\(
        |
        # "Layer 0" (import-layer verbiage)
        \bLayer\s+[0-3]\b
        |
        # "L0/L1", "L1/L2", "L1/L2/L3" sub-package
        \bL[0-3]/L[0-3]
        |
        # "both L3" when referring to server/ and cli/ as import siblings
        \bboth\s+L[0-3]\b
        |
        # "L2 + L1", "L0 + L1"
        \bL[0-3]\s*\+\s*L[0-3]
    )
    """,
    re.VERBOSE,
)

# Files that MUST be skipped: they contain import-layer-looking L-number patterns
# that are legitimate orchestration-level vocabulary, not import-layer annotations.
# test_load_bearing_skip_paths_still_match verifies these files still contain matches.
_LOAD_BEARING_SKIP_PATHS: frozenset[str] = frozenset(
    {
        "hooks/leaf_orchestration_guard.py",  # (L2)+(L3) in docstring: guard invariants
        "fleet/summary.py",  # "L3 fleet sessions" in module docstring
        "cli/_prompts.py",  # "L1/L3 orchestration sessions" in docstring
    }
)

# Files that are skipped as a precaution against future regex expansion.
# These files currently PASS the scan even without a skip entry.
# test_precautionary_skip_paths_do_not_contain_matching_files verifies they still pass.
# When a file in this set starts failing (gains matching content), move it to
# _LOAD_BEARING_SKIP_PATHS.
_PRECAUTIONARY_SKIP_PATHS: frozenset[str] = frozenset(
    {
        # fleet/ files use orchestration vocabulary ("L2 food truck", "L2 dispatch", etc.)
        # that escapes the current regex but could match after a future regex expansion.
        "fleet/_api.py",
        "fleet/_prompts.py",
        "fleet/result_parser.py",
    }
)

# Combined set for the scanner loop (backwards-compatible)
_SKIP_PATHS: frozenset[str] = _LOAD_BEARING_SKIP_PATHS | _PRECAUTIONARY_SKIP_PATHS


def _scan_file_for_violations(path: Path) -> list[str]:
    """Return formatted violation strings for lines containing import-layer label patterns.

    Each entry is "{lineno}: {stripped!r}". Tracks docstring state using the same
    two-boolean logic as the main test to avoid false positives from mixed quote styles.
    """
    violations = []
    in_triple_double = False
    in_triple_single = False
    for lineno, line in enumerate(
        path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
    ):
        stripped = line.strip()
        if not in_triple_single and '"""' in stripped:
            count = stripped.count('"""')
            if count % 2 == 1:
                in_triple_double = not in_triple_double
        elif not in_triple_double and "'''" in stripped:
            count = stripped.count("'''")
            if count % 2 == 1:
                in_triple_single = not in_triple_single
        inside_docstring = in_triple_double or in_triple_single
        if (
            stripped.startswith("#")
            or '"""' in stripped
            or stripped.startswith("'")
            or inside_docstring
        ):
            if _IMPORT_LAYER_PATTERNS.search(line):
                violations.append(f"{lineno}: {stripped!r}")
    return violations


def test_no_bare_import_layer_labels_in_src() -> None:
    """No module docstrings or inline comments use bare L-number for import layers."""
    from autoskillit.core.paths import pkg_root

    src_root = pkg_root()
    violations: dict[str, list[str]] = {}

    for py_file in sorted(src_root.rglob("*.py")):
        rel = py_file.relative_to(src_root)
        rel_str = rel.as_posix()

        if rel_str in _SKIP_PATHS:
            continue

        file_violations = _scan_file_for_violations(py_file)
        if file_violations:
            violations[rel_str] = file_violations

    assert not violations, (
        f"Found {sum(len(v) for v in violations.values())} bare import-layer L-number label(s) — "
        f"use IL-N instead:\n"
        + "\n".join(
            f"  {path}:\n" + "\n".join(f"    {line}" for line in lines)
            for path, lines in sorted(violations.items())
        )
    )


def test_load_bearing_skip_paths_still_match() -> None:
    """Every path in _LOAD_BEARING_SKIP_PATHS must contain at least one match.
    If this fails, the skip entry is stale and should be removed."""
    from autoskillit.core.paths import pkg_root

    src_root = pkg_root()
    for skip_path in sorted(_LOAD_BEARING_SKIP_PATHS):
        full_path = src_root / skip_path
        assert full_path.exists(), (
            f"Load-bearing skip path does not exist: {skip_path!r}. "
            "Remove it from _LOAD_BEARING_SKIP_PATHS or rename it to match."
        )
        violations = _scan_file_for_violations(full_path)
        assert violations, (
            f"Load-bearing skip path {skip_path!r} no longer contains any "
            "import-layer label patterns. Remove it from _LOAD_BEARING_SKIP_PATHS."
        )


def test_all_skip_paths_resolve_to_existing_files() -> None:
    """Every path in both skip sets must correspond to a real file.
    If this fails, a file was renamed or deleted without updating the skip list."""
    from autoskillit.core.paths import pkg_root

    src_root = pkg_root()
    all_skips = _LOAD_BEARING_SKIP_PATHS | _PRECAUTIONARY_SKIP_PATHS
    for skip_path in sorted(all_skips):
        full_path = src_root / skip_path
        assert full_path.exists(), (
            f"Skip path does not exist: {skip_path!r}. Remove it or update to the renamed path."
        )


def test_precautionary_skip_paths_do_not_contain_matching_files() -> None:
    """Files in _PRECAUTIONARY_SKIP_PATHS must NOT currently match the import-layer
    pattern. If a precautionary-listed file gains matching content, it must be moved
    to _LOAD_BEARING_SKIP_PATHS (which triggers test_no_bare_import_layer_labels_in_src
    if absent from that set, providing the right signal)."""
    from autoskillit.core.paths import pkg_root

    src_root = pkg_root()
    for skip_path in sorted(_PRECAUTIONARY_SKIP_PATHS):
        full_path = src_root / skip_path
        if not full_path.exists():
            continue  # test_all_skip_paths_resolve_to_existing_files handles missing
        violations = _scan_file_for_violations(full_path)
        assert not violations, (
            f"Precautionary skip {skip_path!r} now contains import-layer label patterns: "
            f"{violations!r}. Move it to _LOAD_BEARING_SKIP_PATHS."
        )
