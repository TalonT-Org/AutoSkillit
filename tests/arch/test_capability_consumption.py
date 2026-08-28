"""Architectural invariant: every BackendCapabilities field must be consumed in production."""

from __future__ import annotations

import ast
import dataclasses
from datetime import date
from pathlib import Path

import pytest

from tests.arch._deferred_debt import (
    TrackedDeferral,
    assert_deferrals_have_regression_tests,
    assert_entries_still_apply,
    assert_not_stale,
    assert_rationale_present,
)

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


_FORWARD_DECLARED: dict[str, TrackedDeferral] = {
    "supports_thinking_blocks": TrackedDeferral(
        issue=3497,
        rationale="thinking-block rendering gating",
        added_date=date(2026, 5, 31),
        regression_test=(
            "tests/arch/test_capability_consumption.py::"
            "test_forward_declared_capability_remains_present_and_unconsumed[supports_thinking_blocks]"
        ),
    ),
    "mcp_env_forward_vars": TrackedDeferral(
        issue=3458,
        rationale="MCP env forwarding — enforcement arch test exists, awaiting src/ consumer",
        added_date=date(2026, 5, 31),
        regression_test=(
            "tests/arch/test_capability_consumption.py::"
            "test_forward_declared_capability_remains_present_and_unconsumed[mcp_env_forward_vars]"
        ),
    ),
    "required_session_files": TrackedDeferral(
        issue=3134,
        rationale=(
            "production consumer moved to CodexBackend.setup_session_dir — "
            "field retained for validate_session_layout"
        ),
        added_date=date(2026, 6, 2),
        regression_test=(
            "tests/arch/test_capability_consumption.py::"
            "test_forward_declared_capability_remains_present_and_unconsumed[required_session_files]"
        ),
    ),
    "patch_format": TrackedDeferral(
        issue=3776,
        rationale="patch path extraction routing — P2-A3-WP1 (#3787) co-lands consumer",
        added_date=date(2026, 6, 5),
        regression_test=(
            "tests/arch/test_capability_consumption.py::"
            "test_forward_declared_capability_remains_present_and_unconsumed[patch_format]"
        ),
    ),
    "github_api_callable": TrackedDeferral(
        issue=4204,
        rationale=(
            "Behavioral documentation field; production consumer added when "
            "network-capability gate is wired to a BackendCapabilities check"
        ),
        added_date=date(2026, 7, 7),
        regression_test=(
            "tests/arch/test_capability_consumption.py::"
            "test_forward_declared_capability_remains_present_and_unconsumed[github_api_callable]"
        ),
    ),
}


def _collect_attribute_reads(src_root: Path, field_names: frozenset[str]) -> dict[str, list[str]]:
    """Scan src/ for .field_name attribute access, excluding definition file."""
    reads: dict[str, list[str]] = {name: [] for name in field_names}
    definition_file = src_root / "core" / "types" / "_type_backend.py"
    for py_file in src_root.rglob("*.py"):
        if py_file == definition_file:
            continue
        relpath = str(py_file.relative_to(src_root))
        try:
            tree = ast.parse(py_file.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in field_names:
                reads[node.attr].append(f"{relpath}:{node.lineno}")
    return reads


def test_all_capability_fields_have_production_consumers():
    """Every BackendCapabilities field must be read somewhere in src/ (excluding definition)."""
    from autoskillit.core import BackendCapabilities, paths

    src_root = paths.pkg_root()
    field_names = frozenset(f.name for f in dataclasses.fields(BackendCapabilities))
    reads = _collect_attribute_reads(src_root, field_names)

    unconsumed = {
        name for name, sites in reads.items() if not sites and name not in _FORWARD_DECLARED
    }
    assert not unconsumed, (
        f"BackendCapabilities fields with zero production read sites "
        f"(add a consumer or add to _FORWARD_DECLARED as "
        f"TrackedDeferral(issue=NNNN, rationale='...', added_date=date(YYYY, M, D), "
        f"regression_test='tests/...::test_name')): "
        f"{sorted(unconsumed)}"
    )


def test_hook_trust_policy_has_a_real_production_consumer() -> None:
    from autoskillit.core import paths

    codex_path = paths.pkg_root() / "execution" / "backends" / "codex.py"
    tree = ast.parse(codex_path.read_text(encoding="utf-8"), filename=str(codex_path))
    backend_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "CodexBackend"
    )
    interactive_builder = next(
        node
        for node in backend_class.body
        if isinstance(node, ast.FunctionDef) and node.name == "build_interactive_cmd"
    )
    translation_calls = [
        call
        for call in ast.walk(interactive_builder)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "_should_bypass_hook_trust"
    ]

    assert "hook_trust_policy" not in _FORWARD_DECLARED
    assert len(translation_calls) == 1
    translation_call = translation_calls[0]
    assert len(translation_call.args) == 1
    policy_arg = translation_call.args[0]
    assert (
        isinstance(policy_arg, ast.Attribute)
        and policy_arg.attr == "hook_trust_policy"
        and isinstance(policy_arg.value, ast.Attribute)
        and policy_arg.value.attr == "capabilities"
        and isinstance(policy_arg.value.value, ast.Name)
        and policy_arg.value.value.id == "self"
    ), "interactive launch must translate self.capabilities.hook_trust_policy"
    automated_keyword = next(
        keyword for keyword in translation_call.keywords if keyword.arg == "automated_session"
    )
    assert isinstance(automated_keyword.value, ast.Constant)
    assert automated_keyword.value.value is False


@pytest.mark.parametrize("field", _FORWARD_DECLARED)
def test_forward_declared_capability_remains_present_and_unconsumed(field: str) -> None:
    """Forward declarations retain structural evidence until their consumer lands."""
    from autoskillit.core import BackendCapabilities, paths

    src_root = paths.pkg_root()
    field_names = frozenset(f.name for f in dataclasses.fields(BackendCapabilities))
    reads = _collect_attribute_reads(src_root, field_names)

    assert field in field_names
    assert not reads[field], (
        f"_FORWARD_DECLARED entry {field!r} now has production consumers "
        f"(remove it from the registry): {reads[field]}"
    )


def test_every_tracked_deferral_names_a_resolvable_regression_test(
    request: pytest.FixtureRequest,
) -> None:
    from autoskillit.core import BackendCapabilities

    assert_entries_still_apply(
        _FORWARD_DECLARED,
        registry_name="_FORWARD_DECLARED",
        live_keys={field.name for field in dataclasses.fields(BackendCapabilities)},
    )
    assert_not_stale(_FORWARD_DECLARED, registry_name="_FORWARD_DECLARED")
    assert_rationale_present(_FORWARD_DECLARED, registry_name="_FORWARD_DECLARED")
    assert_deferrals_have_regression_tests(
        _FORWARD_DECLARED,
        registry_name="_FORWARD_DECLARED",
        collected_node_ids={item.nodeid for item in request.session.items},
    )
