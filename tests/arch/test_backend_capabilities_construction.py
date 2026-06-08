"""Arch guard: every BackendCapabilities(...) site must pass all fields as keyword args."""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_CONSTRUCTION_SITES: list[tuple[str, str]] = [
    ("CLAUDE_CODE_CAPABILITIES", "core/types/_type_backend.py"),
    ("CodexBackend.capabilities", "execution/backends/codex.py"),
]


def test_all_backends_explicitly_set_all_capabilities_fields() -> None:
    """Every BackendCapabilities(...) call site must pass all dataclass fields as keyword args."""
    from autoskillit.core import BackendCapabilities, pkg_root

    src_root = pkg_root()
    expected = {f.name for f in dataclasses.fields(BackendCapabilities)}

    for label, relpath in _CONSTRUCTION_SITES:
        source = Path(src_root / relpath).read_text()
        tree = ast.parse(source)

        calls_found: list[ast.Call] = []
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "BackendCapabilities"
            ):
                calls_found.append(node)

        assert calls_found, (
            f"{label} ({relpath}): no BackendCapabilities(...) call found — "
            f"file may have been restructured"
        )
        assert len(calls_found) == 1, (
            f"{label} ({relpath}): expected exactly 1 BackendCapabilities(...) call, "
            f"found {len(calls_found)}"
        )

        call = calls_found[0]
        keyword_names = {kw.arg for kw in call.keywords if kw.arg is not None}
        missing = expected - keyword_names
        unexpected = keyword_names - expected
        assert keyword_names == expected, (
            f"{label} ({relpath}): keyword mismatch — "
            f"missing={sorted(missing)}, unexpected={sorted(unexpected)}"
        )


def test_construction_sites_list_is_exhaustive() -> None:
    """_CONSTRUCTION_SITES must list every BackendCapabilities(...) call site in src."""
    from autoskillit.core import pkg_root

    src_root = pkg_root()
    found_relpaths: list[str] = []

    for py_file in sorted(src_root.rglob("*.py")):
        try:
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except (SyntaxError, UnicodeDecodeError):
            continue

        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "BackendCapabilities"
            ):
                found_relpaths.append(py_file.relative_to(src_root).as_posix())
                break

    listed_relpaths = {relpath for _, relpath in _CONSTRUCTION_SITES}
    undeclared = set(found_relpaths) - listed_relpaths
    assert not undeclared, (
        f"BackendCapabilities(...) call sites found in src but missing from "
        f"_CONSTRUCTION_SITES: {sorted(undeclared)}. "
        f"Add them to _CONSTRUCTION_SITES to ensure they are checked."
    )
