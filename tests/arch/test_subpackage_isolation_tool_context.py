from __future__ import annotations

import ast

import pytest

from tests.arch._helpers import SRC_ROOT

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


def test_tool_context_service_fields_use_protocol_types() -> None:
    """REQ-ARCH-002: Every non-exempt ToolContext field must use a Protocol from core/types.py.

    Exempt fields:
    - plugin_authority: PluginArtifactAuthority (lifetime-owning authority)
    - config: AutomationConfig dataclass (configuration container, not a service interface)
    - recipe_initialization_state: lifecycle value union, not a service interface
    - kitchen_open_state: immutable lifecycle value, protected by KitchenTransitionLock
    """
    AUTOSKILLIT_ROOT = SRC_ROOT

    # Collect Protocol class names from core/types.py and its sub-modules via AST.
    # After the types.py split, Protocol definitions live in the _type_protocols_*.py
    # shards and SubprocessRunner lives in _type_subprocess.py; types.py is a thin re-export hub.
    core_protocols: set[str] = set()
    for types_filename in (
        "core/types/__init__.py",
        "core/types/_type_audit_admission.py",
        "core/types/_type_audit_admission_ledger.py",
        "core/types/_type_audit_protocols.py",
        "core/types/_type_protocols_logging.py",
        "core/types/_type_protocols_execution.py",
        "core/types/_type_protocols_github.py",
        "core/types/_type_protocols_workspace.py",
        "core/types/_type_protocols_recipe.py",
        "core/types/_type_protocols_infra.py",
        "core/types/_type_protocols_backend.py",
        "core/types/_type_recipe_execution.py",
        "core/types/_type_subprocess.py",
        "core/types/_type_context_admission_persistence.py",
        "core/types/_type_context_admission_persistence_envelope.py",
        "core/types/_type_native_shell_capture.py",
        "core/types/_type_exploration.py",
    ):
        types_path = AUTOSKILLIT_ROOT / types_filename
        if not types_path.exists():
            continue
        types_tree = ast.parse(types_path.read_text())
        for node in ast.walk(types_tree):
            if isinstance(node, ast.ClassDef):
                for base in node.bases:
                    base_str = ast.unparse(base)
                    if "Protocol" in base_str:
                        core_protocols.add(node.name)
                        break

    # Collect ToolContext field annotations via AST
    context_path = AUTOSKILLIT_ROOT / "pipeline" / "context.py"
    context_tree = ast.parse(context_path.read_text())

    EXEMPT = {
        "config",
        "active_recipe_packs",
        "active_recipe_features",
        "active_recipe_steps",
        "active_recipe_ingredients",
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
                            f"Protocol in core/types.py"
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

    # Parse server/_factory.py via AST
    factory_path = SRC_ROOT / "server" / "_factory.py"
    tree = ast.parse(factory_path.read_text())

    # Find make_context() function body
    assigned_fields: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name == "make_context"):
            continue
        for stmt in ast.walk(ast.Module(body=node.body, type_ignores=[])):
            # Capture keyword args in ToolContext(...) constructor call
            if isinstance(stmt, ast.Call):
                func_str = ast.unparse(stmt.func)
                if "ToolContext" in func_str:
                    for kw in stmt.keywords:
                        if kw.arg:
                            assigned_fields.add(kw.arg)
            # Capture post-construction assignments: ctx.field_name = ...
            if isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name):
                        assigned_fields.add(target.attr)

    unwired = optional_field_names - assigned_fields
    assert not unwired, (
        f"make_context() does not assign these optional ToolContext fields: {unwired}. "
        "Add wiring in server/_factory.py make_context()."
    )
