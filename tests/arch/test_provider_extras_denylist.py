"""Provider-extras denylist: structural and AST enforcement."""

from __future__ import annotations

import ast

import pytest

from autoskillit.core import pkg_root
from autoskillit.execution.backends._claude_prompt import (
    _PROVIDER_EXTRAS_BASE_DENYLIST,
    _SKILL_SESSION_EXTRAS_DENYLIST,
)

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


def test_provider_extras_base_denylist_is_frozen() -> None:
    assert isinstance(_PROVIDER_EXTRAS_BASE_DENYLIST, frozenset)


def test_skill_session_denylist_is_superset_of_base() -> None:
    assert _SKILL_SESSION_EXTRAS_DENYLIST >= _PROVIDER_EXTRAS_BASE_DENYLIST


def test_all_provider_extras_filter_sites_use_named_constant() -> None:
    backends_dir = pkg_root() / "execution" / "backends"
    violations: list[str] = []

    for filename in ("codex.py", "claude.py"):
        filepath = backends_dir / filename
        source = filepath.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=filename)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            if not any(isinstance(op, ast.NotIn) for op in node.ops):
                continue
            for comparator in node.comparators:
                if not isinstance(comparator, ast.Tuple):
                    continue
                str_values = set()
                for elt in comparator.elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                        str_values.add(elt.value)
                if (
                    "AUTOSKILLIT_SESSION_TYPE" in str_values
                    and "AUTOSKILLIT_HEADLESS" in str_values
                ):
                    violations.append(
                        f"{filename}:{node.lineno}: inline tuple literal in "
                        f"provider_extras/env_extras filter — use a *_DENYLIST constant"
                    )

    assert not violations, "Inline filter tuples found:\n" + "\n".join(violations)
