from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.arch._helpers import SRC_ROOT

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


def _explicit_core_protocol_names() -> set[str]:
    core_protocols: set[str] = set()
    types_dir = SRC_ROOT / "core" / "types"
    for types_path in sorted(types_dir.rglob("*.py")):
        types_tree = ast.parse(types_path.read_text())
        for node in ast.walk(types_tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for base in node.bases:
                if "Protocol" in ast.unparse(base):
                    core_protocols.add(node.name)
                    break
    return core_protocols


def _make_context_assigned_fields(factory_path: Path) -> set[str]:
    tree = ast.parse(factory_path.read_text())
    assigned_fields: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name == "make_context"):
            continue
        for statement in ast.walk(ast.Module(body=node.body, type_ignores=[])):
            if isinstance(statement, ast.Call) and "ToolContext" in ast.unparse(statement.func):
                for keyword in statement.keywords:
                    if keyword.arg:
                        assigned_fields.add(keyword.arg)
            if isinstance(statement, ast.Assign):
                for target in statement.targets:
                    if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name):
                        assigned_fields.add(target.attr)
    return assigned_fields


def test_tool_context_service_fields_use_protocol_types() -> None:
    """REQ-ARCH-002: Every non-exempt ToolContext field must use a Protocol from core/types/.

    Exempt fields:
    - plugin_authority: PluginArtifactAuthority (lifetime-owning authority)
    - config: AutomationConfig dataclass (configuration container, not a service interface)
    - recipe_initialization_state: lifecycle value union, not a service interface
    - kitchen_open_state: immutable lifecycle value, protected by KitchenTransitionLock
    """
    core_protocols = _explicit_core_protocol_names()

    # Collect ToolContext field annotations via AST
    context_path = SRC_ROOT / "pipeline" / "context.py"
    context_tree = ast.parse(context_path.read_text())

    EXEMPT = {
        "config",
        "active_recipe_packs",
        "active_recipe_features",
        "active_recipe_steps",
        "active_recipe_ingredients",
        "active_recipe_projection",
        "recipe_initialization_state",
        "recipe_terminal_response_cache",
        "kitchen_open_state",
        "kitchen_process_identity",
        "kitchen_tracker_key",
        "tracker_leases",
        "tracker_leases_lock",
        "temp_dir",
        "project_dir",
        "ephemeral_root",
        "operation_lease_channel",
        "_baseline_config",
        "_session_config_overrides",
    }
    violations: list[str] = []

    for node in ast.walk(context_tree):
        if isinstance(node, ast.ClassDef) and node.name == "ToolContext":
            for item in node.body:
                if not isinstance(item, ast.AnnAssign):
                    continue
                field_name = ast.unparse(item.target)
                if field_name in EXEMPT:
                    continue

                # Collect all type names from annotation (unwraps Union/Optional)
                ann_str = ast.unparse(item.annotation)
                # Strip Optional[...] / X | None wrappers; collect bare names
                type_names = {
                    n.strip().strip("[]")
                    for n in ann_str.replace("|", ",").split(",")
                    if n.strip() not in ("None", "")
                }
                # Remove generic parameters, e.g. "list[str]" → "list"
                type_names = {n.split("[")[0] for n in type_names}

                for type_name in type_names:
                    if type_name not in core_protocols and type_name not in (
                        "str",
                        "int",
                        "float",
                        "bool",
                        "bytes",
                        "None",
                    ):
                        violations.append(
                            f"ToolContext.{field_name}: '{type_name}' is not a "
                            f"Protocol in core/types/"
                        )

    assert not violations, (
        "ToolContext fields use concrete types instead of core/types.py Protocols:\n"
        + "\n".join(violations)
    )


def test_make_context_wires_all_optional_toolcontext_fields() -> None:
    """REQ-ARCH-002: make_context() must assign every optional ToolContext field.

    Self-closing: parses server/_factory.py via AST to discover all field assignments
    inside make_context(), then cross-checks against all ToolContext fields that have
    field(default=None). Fails if any optional field exists in ToolContext but is
    neither assigned in the ToolContext() constructor call nor in a post-construction
    assignment within make_context().
    """
    from autoskillit.pipeline.context import ToolContext

    # All optional service fields (field(default=None))
    optional_field_names = {
        name for name, f in ToolContext.__dataclass_fields__.items() if f.default is None
    }

    factory_path = SRC_ROOT / "server" / "_factory.py"
    assigned_fields = _make_context_assigned_fields(factory_path)

    unwired = optional_field_names - assigned_fields
    assert not unwired, (
        f"make_context() does not assign these optional ToolContext fields: {unwired}. "
        "Add wiring in server/_factory.py make_context()."
    )
