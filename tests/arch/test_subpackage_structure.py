"""REQ-ARCH-010: Validate post-reorganization subpackage structure."""

from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "autoskillit"


class TestCoreSubpackages:
    def test_core_types_is_package(self):
        assert (SRC / "core" / "types" / "__init__.py").exists()

    def test_core_types_has_all_type_modules(self):
        expected = {
            "_type_enums",
            "_type_constants",
            "_type_results",
            "_type_subprocess",
            "_type_helpers",
            "_type_resume",
            "_type_plugin_source",
            "_type_protocols_execution",
            "_type_protocols_github",
            "_type_protocols_infra",
            "_type_protocols_logging",
            "_type_protocols_recipe",
            "_type_protocols_workspace",
        }
        actual = {p.stem for p in (SRC / "core" / "types").glob("_type_*.py")}
        assert actual == expected

    def test_core_runtime_is_package(self):
        assert (SRC / "core" / "runtime" / "__init__.py").exists()

    def test_core_runtime_has_expected_modules(self):
        expected = {"kitchen_state", "readiness", "session_registry", "_linux_proc"}
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
    @pytest.mark.parametrize("subpkg", ["headless", "process", "session", "merge_queue"])
    def test_subpackage_is_package(self, subpkg):
        assert (SRC / "execution" / subpkg / "__init__.py").exists()

    def test_headless_has_expected_modules(self):
        expected = {
            "_headless_git",
            "_headless_path_tokens",
            "_headless_recovery",
            "_headless_result",
            "_headless_scan",
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
        }
        actual = {p.stem for p in (SRC / "execution" / "process").glob("_process_*.py")}
        assert actual == expected

    def test_session_has_expected_modules(self):
        expected = {
            "_session_model",
            "_session_content",
            "_session_outcome",
            "_retry_fsm",
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
