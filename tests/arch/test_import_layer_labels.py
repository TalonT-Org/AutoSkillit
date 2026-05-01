"""Regression guard: no bare L-number labels in import-layer contexts.

After the IL-N rename (issue #1574), module docstrings and inline comments
must use IL-0/IL-1/IL-2/IL-3 for import-layer annotations.  This test
scans src/autoskillit/ Python files for the patterns that indicate an
import-layer usage and fails if any bare L-number labels remain.
"""

import re

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
        L[0-3]\s*\+\s*L[0-3]
    )
    """,
    re.VERBOSE,
)

# Orchestration-level files where bare L0–L3 is CORRECT — skip them
_SKIP_PATHS = frozenset(
    [
        "fleet/_api.py",
        "fleet/_prompts.py",
        "fleet/result_parser.py",
        "fleet/state.py",
        "fleet/summary.py",
        "fleet/sidecar.py",
        "fleet/_liveness.py",
        "fleet/_semaphore.py",
        "fleet/_sidecar_rpc.py",
        "fleet/_findings_rpc.py",
    ]
)


def test_no_bare_import_layer_labels_in_src() -> None:
    """No module docstrings or inline comments use bare L-number for import layers."""
    from autoskillit.core.paths import pkg_root

    src_root = pkg_root()
    violations: list[str] = []

    for py_file in sorted(src_root.rglob("*.py")):
        rel = py_file.relative_to(src_root)
        rel_str = rel.as_posix()

        # Skip orchestration-level fleet files and test files
        if any(rel_str.endswith(skip) for skip in _SKIP_PATHS):
            continue
        if rel_str.startswith("tests/"):
            continue

        source = py_file.read_text()
        # Only scan docstrings and comments, not string literals in code
        for lineno, line in enumerate(source.splitlines(), 1):
            stripped = line.strip()
            # Check comment lines and lines that are part of docstrings
            if stripped.startswith("#") or '"""' in stripped or stripped.startswith("'"):
                if _IMPORT_LAYER_PATTERNS.search(line):
                    violations.append(f"{rel_str}:{lineno}: {stripped!r}")

    assert not violations, (
        f"Found {len(violations)} bare import-layer L-number label(s) — "
        f"use IL-N instead:\n" + "\n".join(violations)
    )
