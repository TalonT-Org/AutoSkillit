"""REQ-ARCH-010: Validate post-reorganization subpackage structure."""

import ast
from importlib import import_module
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

SRC = Path(__file__).resolve().parents[2] / "src" / "autoskillit"

_CORE_TYPES_GROUPS: dict[str, frozenset[str]] = {
    "foundation": frozenset(
        {
            "_type_enums",
            "_type_enums_context_admission",
            "_type_exceptions",
            "_type_exploration",
            "_type_dimensions",
            "_type_execution_identity",
        }
    ),
    "install": frozenset(
        {
            "_type_plugin_source",
            "_type_retirement_backstops",
            "_type_install",
            "_type_managed_home",
        }
    ),
    "github": frozenset({"_type_github_review", "_type_github_review_anchor"}),
    "skill": frozenset(
        {"_type_skill_plan_specs", "_type_skill_semantics", "_type_session_invariant_admission"}
    ),
    "audit": frozenset(
        {
            "_type_audit_admission",
            "_type_audit_admission_artifact_ownership",
            "_type_audit_admission_ledger",
            "_type_audit_admission_reference_identity",
            "_type_audit_admission_validation",
            "_type_audit_artifact_ref",
            "_type_audit_cycle_authority",
            "_type_audit_cycle_disposition",
            "_type_closure_report",
            "_type_plan_set_authority",
        }
    ),
    "recipe": frozenset(
        {
            "_type_recipe_binding",
            "_type_recipe_delivery",
            "_type_recipe_execution",
            "_type_recipe_sections",
            "_type_truth",
            "_type_capture",
        }
    ),
    "constants": frozenset(
        {
            "_type_constants",
            "_type_constants_durable_writers",
            "_type_constants_env",
            "_type_constants_features",
            "_type_constants_registries",
            "_type_constants_retirements",
            "_type_constants_skill_contract",
            "_type_invariant_registry",
            "_type_orchestrator_instruction_surfaces",
            "_type_intake_policy",
            "_type_persisted_formats",
        }
    ),
    "results": frozenset(
        {
            "_type_results",
            "_type_results_execution",
            "_type_results_records",
            "_type_token",
            "_type_figure_spec",
        }
    ),
    "execution": frozenset(
        {
            "_type_backend",
            "_type_checkpoint",
            "_type_native_shell_capture",
            "_type_subprocess",
            "_type_inspector",
        }
    ),
    "launch": frozenset(
        {
            "_type_launch",
            "_type_launch_authority",
            "_type_launch_intent",
            "_type_launch_projection",
            "_type_session_shape",
            "_type_dispatch_identity",
            "_type_skill_contract",
            "_type_helpers",
        }
    ),
    "context_admission": frozenset(
        {
            "_type_context_admission",
            "_type_context_admission_base",
            "_type_context_admission_coverage",
            "_type_context_admission_effects",
            "_type_context_admission_events",
            "_type_context_admission_identities",
            "_type_context_admission_persistence",
            "_type_context_admission_persistence_envelope",
            "_type_context_admission_records",
            "_type_context_admission_states",
        }
    ),
    "protocols": frozenset(
        {
            "_type_protocols_backend",
            "_type_protocols_execution",
            "_type_protocols_github",
            "_type_protocols_infra",
            "_type_protocols_logging",
            "_type_protocols_recipe",
            "_type_protocols_workspace",
        }
    ),
}


class TestCoreSubpackages:
    def test_core_types_is_package(self):
        assert (SRC / "core" / "types" / "__init__.py").exists()

    def test_core_types_group_layout(self):
        types_dir = SRC / "core" / "types"
        actual = {
            group_dir.name: frozenset(
                path.stem for path in group_dir.glob("*.py") if path.name != "__init__.py"
            )
            for group_dir in types_dir.iterdir()
            if group_dir.is_dir()
            and not group_dir.name.startswith("_")
            and (group_dir / "__init__.py").is_file()
        }
        assert actual == _CORE_TYPES_GROUPS

        modules = [module for members in _CORE_TYPES_GROUPS.values() for module in members]
        assert len(set(modules)) == len(modules), "A type module appears in multiple groups"

    def test_core_types_root_holds_only_hub(self):
        root_python_files = {path.name for path in (SRC / "core" / "types").glob("*.py")}
        assert root_python_files == {"__init__.py"}

    def test_core_types_group_facades_import_only_own_members(self):
        types_dir = SRC / "core" / "types"
        for group, members in _CORE_TYPES_GROUPS.items():
            facade_path = types_dir / group / "__init__.py"
            tree = ast.parse(facade_path.read_text(encoding="utf-8"), filename=str(facade_path))
            violations: list[str] = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    violations.append(ast.unparse(node))
                elif isinstance(node, ast.ImportFrom):
                    if node.level == 0 and (
                        node.module == "__future__"
                        or (
                            node.module == "typing"
                            and [alias.name for alias in node.names] == ["TYPE_CHECKING"]
                        )
                    ):
                        continue
                    if node.level != 1 or node.module not in members:
                        violations.append(ast.unparse(node))

            assigns_all = any(
                isinstance(node, ast.Assign)
                and any(
                    isinstance(target, ast.Name) and target.id == "__all__"
                    for target in node.targets
                )
                for node in ast.walk(tree)
            )
            assert not violations, f"{facade_path} has non-owned imports: {violations}"
            assert assigns_all, f"{facade_path} must assign __all__"

    def test_core_types_hub_imports_only_groups(self):
        facade_path = SRC / "core" / "types" / "__init__.py"
        tree = ast.parse(facade_path.read_text(encoding="utf-8"), filename=str(facade_path))
        imported_groups: set[str] = set()
        violations: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                violations.append(ast.unparse(node))
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0 and (
                    node.module == "__future__"
                    or (
                        node.module == "typing"
                        and [alias.name for alias in node.names] == ["TYPE_CHECKING"]
                    )
                ):
                    continue
                if node.level != 1 or node.module not in _CORE_TYPES_GROUPS:
                    violations.append(ast.unparse(node))
                elif node.module is not None:
                    imported_groups.add(node.module)
        assert not violations, f"{facade_path} has non-group imports: {violations}"
        assert imported_groups == set(_CORE_TYPES_GROUPS)

    def test_core_types_public_surface_is_union_of_group_facades(self):
        group_exports = {
            name
            for group in _CORE_TYPES_GROUPS
            for name in import_module(f"autoskillit.core.types.{group}").__all__
        }
        public_facade = import_module("autoskillit.core.types")
        assert set(public_facade.__all__) == group_exports
        for name in public_facade.__all__:
            getattr(public_facade, name)

    def test_type_constants_split_completeness(self):
        """Verify __all__ union across all _type_constants*.py modules has no duplicates."""
        from autoskillit.core.types.constants._type_constants import __all__ as remaining
        from autoskillit.core.types.constants._type_constants_durable_writers import (
            __all__ as durable_writers,
        )
        from autoskillit.core.types.constants._type_constants_env import __all__ as env
        from autoskillit.core.types.constants._type_constants_features import __all__ as features
        from autoskillit.core.types.constants._type_constants_registries import (
            __all__ as registries,
        )
        from autoskillit.core.types.constants._type_constants_retirements import (
            __all__ as retirements,
        )
        from autoskillit.core.types.constants._type_constants_skill_contract import (
            __all__ as skill_contract,
        )

        combined = (
            set(remaining)
            | set(durable_writers)
            | set(env)
            | set(features)
            | set(registries)
            | set(retirements)
            | set(skill_contract)
        )
        assert len(combined) == (
            len(remaining)
            + len(durable_writers)
            + len(env)
            + len(features)
            + len(registries)
            + len(retirements)
            + len(skill_contract)
        ), "Duplicate symbols across split modules"
        assert len(combined) == 177, (
            f"Expected 177 symbols total, got {len(combined)} "
            f"(remaining={len(remaining)}, durable_writers={len(durable_writers)}, "
            f"env={len(env)}, features={len(features)}, "
            f"registries={len(registries)}, retirements={len(retirements)}, "
            f"skill_contract={len(skill_contract)})"
        )

    def test_core_runtime_is_package(self):
        assert (SRC / "core" / "runtime" / "__init__.py").exists()

    def test_core_runtime_has_expected_modules(self):
        expected = {
            "artifact_lease",
            "executable_binding",
            "kitchen_state",
            "private_file",
            "readiness",
            "session_provenance",
            "session_registry",
            "worktree_gate_lease",
            "_linux_proc",
            "_reclamation",
        }
        actual = {p.stem for p in (SRC / "core" / "runtime").glob("*.py") if p.stem != "__init__"}
        assert actual == expected

    def test_no_type_modules_remain_flat_in_core(self):
        """No _type_*.py files should remain directly in core/."""
        orphans = list((SRC / "core").glob("_type_*.py"))
        assert not orphans, f"Orphan _type modules in core/: {[p.name for p in orphans]}"

    def test_no_runtime_modules_remain_flat_in_core(self):
        """Runtime modules should not remain directly in core/."""
        names = {
            "kitchen_state.py",
            "readiness.py",
            "session_registry.py",
            "_linux_proc.py",
        }
        orphans = [SRC / "core" / n for n in names if (SRC / "core" / n).exists()]
        assert not orphans, f"Orphan runtime modules in core/: {[p.name for p in orphans]}"


class TestExecutionSubpackages:
    @pytest.mark.parametrize(
        "subpkg",
        ["headless", "process", "session", "merge_queue", "evidence", "recording", "session_log"],
    )
    def test_subpackage_is_package(self, subpkg):
        assert (SRC / "execution" / subpkg / "__init__.py").exists()

    def test_headless_has_expected_modules(self):
        expected = {
            "_headless_adjudication",
            "_headless_evidence",
            "_headless_execute",
            "_headless_git",
            "_headless_helpers",
            "_headless_launch",
            "_headless_model_evidence",
            "_headless_model",
            "_headless_outcome",
            "_headless_path_tokens",
            "_headless_prepare",
            "_headless_recovery",
            "_headless_result",
            "_headless_terminal",
        }
        actual = {p.stem for p in (SRC / "execution" / "headless").glob("_headless_*.py")}
        assert actual == expected

    def test_process_has_expected_modules(self):
        expected = {
            "_process_io",
            "_process_jsonl",
            "_process_kill",
            "_process_monitor",
            "_process_pty",
            "_process_race",
            "_process_tether",
        }
        actual = {p.stem for p in (SRC / "execution" / "process").glob("_process_*.py")}
        assert actual == expected

    def test_session_has_expected_modules(self):
        expected = {
            "_session_model",
            "_session_content",
            "_session_outcome",
            "_session_state",
            "_skill_session_contract_store",
            "_skill_session_contract_codec",
            "_retry_fsm",
            "_exit_classification",
            "_provider_parse",
            "turn_usage",
            "_managed_headless_session_lineage",
            "_managed_headless_session_lineage_codec",
            "_managed_headless_session_lineage_indexes",
            "_managed_headless_session_lineage_records",
            "_managed_headless_session_lineage_runner",
        }
        actual = {
            p.stem for p in (SRC / "execution" / "session").glob("*.py") if p.stem != "__init__"
        }
        assert actual == expected

    def test_merge_queue_has_expected_modules(self):
        expected = {
            "_merge_queue_classifier",
            "_merge_queue_group_ci",
            "_merge_queue_repo_state",
        }
        actual = {p.stem for p in (SRC / "execution" / "merge_queue").glob("_merge_queue_*.py")}
        assert actual == expected

    def test_no_headless_modules_remain_flat(self):
        orphans = list((SRC / "execution").glob("_headless_*.py"))
        assert not orphans

    def test_no_process_modules_remain_flat(self):
        orphans = list((SRC / "execution").glob("_process_*.py"))
        assert not orphans

    def test_no_session_private_modules_remain_flat(self):
        names = {
            "_session_model.py",
            "_session_content.py",
            "_session_outcome.py",
            "_retry_fsm.py",
        }
        orphans = [SRC / "execution" / n for n in names if (SRC / "execution" / n).exists()]
        assert not orphans

    def test_no_merge_queue_modules_remain_flat(self):
        orphans = list((SRC / "execution").glob("_merge_queue_*.py"))
        assert not orphans
