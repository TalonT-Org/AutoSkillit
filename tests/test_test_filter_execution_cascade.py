"""REQ-EXEC-001..004: module-level cascade map for execution/ submodules."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._test_filter import (
    MODULE_CASCADE_EXECUTION,
    SUBPKG_CASCADE_EXECUTION,
    FilterMode,
    build_test_scope,
)

pytestmark = [pytest.mark.medium]

_ALL_DIRS = [
    "core",
    "config",
    "execution",
    "pipeline",
    "workspace",
    "recipe",
    "migration",
    "fleet",
    "server",
    "cli",
    "hooks",
    "skills",
    "arch",
    "contracts",
    "infra",
    "docs",
]


def test_all_entries_present() -> None:
    """All documented module stems are present in MODULE_CASCADE_EXECUTION."""
    expected = {
        "anomaly_detection",
        "clone_guard",
        "ci",
        "diff_annotator",
        "pr_analysis",
        "testing",
        "db",
        "recording",
        "github",
        "remote_resolver",
        "quota",
        "session_log",
        "linux_tracing",
        "commands",
    }
    assert expected <= set(MODULE_CASCADE_EXECUTION.keys())


class TestBuildTestScopeExecutionCascade:
    """Routing via MODULE_CASCADE_EXECUTION in build_test_scope (CONSERVATIVE mode)."""

    ALL_DIRS = _ALL_DIRS

    def _make_tests_root(self, tmp_path: Path, dirs: list[str]) -> Path:
        tests_root = tmp_path / "tests"
        for d in dirs:
            (tests_root / d).mkdir(parents=True, exist_ok=True)
        return tests_root

    def test_narrow_module_uses_narrow_scope(self, tmp_path: Path) -> None:
        """anomaly_detection.py change → scope is {execution} only (+ always-run)."""
        tests_root = self._make_tests_root(tmp_path, self.ALL_DIRS)
        result = build_test_scope(
            changed_files={"src/autoskillit/execution/anomaly_detection.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        assert "execution" in dir_names
        # Must NOT include any dirs not declared in the narrow entry
        for excluded in [
            "core",
            "cli",
            "server",
            "workspace",
            "migration",
            "pipeline",
            "recipe",
        ]:
            assert excluded not in dir_names, (
                f"narrow cascade for anomaly_detection should not include {excluded}"
            )
        # Always-run dirs must still be present
        assert "arch" in dir_names
        assert "contracts" in dir_names

    def test_narrow_modules_all_resolve_to_execution_only(self, tmp_path: Path) -> None:
        """Each of the narrowest stems maps to frozenset({"execution"})."""
        narrow_stems = [
            "anomaly_detection",
            "clone_guard",
        ]
        tests_root = self._make_tests_root(tmp_path, self.ALL_DIRS)
        for stem in narrow_stems:
            result = build_test_scope(
                changed_files={f"src/autoskillit/execution/{stem}.py"},
                mode=FilterMode.CONSERVATIVE,
                tests_root=tests_root,
            )
            assert result is not None, f"{stem} should return non-None result"
            dir_names = {p.name for p in result}
            assert "execution" in dir_names, f"{stem} should cascade to 'execution'"
            for excluded in ["core", "cli", "server", "workspace", "migration"]:
                assert excluded not in dir_names, (
                    f"{stem} narrow cascade should not include {excluded}"
                )

    def test_unknown_execution_stem_falls_through_to_cascade(self, tmp_path: Path) -> None:
        """headless.py (not in MODULE_CASCADE_EXECUTION) → cascade_map["execution"]."""
        tests_root = self._make_tests_root(tmp_path, self.ALL_DIRS)
        result = build_test_scope(
            changed_files={"src/autoskillit/execution/headless.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        # LAYER_CASCADE_CONSERVATIVE["execution"] includes execution, core, workspace,
        # migration, server, cli, infra, skills
        for pkg in ["execution", "server", "cli", "workspace"]:
            assert pkg in dir_names, f"fail-open cascade for headless.py should include {pkg}"

    def test_medium_scope_module_ci(self, tmp_path: Path) -> None:
        """ci.py → frozenset({"execution"}) (its MODULE_CASCADE_EXECUTION entry)."""
        tests_root = self._make_tests_root(tmp_path, self.ALL_DIRS)
        result = build_test_scope(
            changed_files={"src/autoskillit/execution/ci.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        assert "execution" in dir_names
        for excluded in ["core", "cli", "server", "workspace", "migration"]:
            assert excluded not in dir_names, f"ci narrow cascade should not include {excluded}"

    def test_recording_medium_scope_includes_server_files(self, tmp_path: Path) -> None:
        """recording.py entry includes specific server/ test files."""
        tests_root = self._make_tests_root(tmp_path, self.ALL_DIRS)
        # Create the specific server test file so it resolves in the result
        (tests_root / "server" / "test_factory_recording.py").write_text("")
        result = build_test_scope(
            changed_files={"src/autoskillit/execution/recording.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        result_paths = {str(p) for p in result}
        result_names = {p.name for p in result}
        assert "execution" in result_names
        assert "test_factory_recording.py" in result_names, (
            "recording.py cascade should include server/test_factory_recording.py"
        )
        assert str(tests_root / "server" / "test_factory_recording.py") in result_paths

    def test_aggressive_mode_skips_execution_cascade_branch(self, tmp_path: Path) -> None:
        """In AGGRESSIVE mode the execution branch is not taken; maps to {"execution"}."""
        tests_root = self._make_tests_root(tmp_path, self.ALL_DIRS)
        result = build_test_scope(
            changed_files={"src/autoskillit/execution/anomaly_detection.py"},
            mode=FilterMode.AGGRESSIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        assert "execution" in dir_names
        # AGGRESSIVE maps execution → {execution} only; no widening via MODULE_CASCADE_EXECUTION
        for excluded in ["core", "cli", "server", "workspace", "migration"]:
            assert excluded not in dir_names, (
                f"AGGRESSIVE mode should not widen execution cascade to {excluded}"
            )

    def test_headless_includes_skills_compliance_test(self, tmp_path: Path) -> None:
        """headless.py change → skills compliance file; skills/ dir excluded."""
        tests_root = self._make_tests_root(tmp_path, self.ALL_DIRS)
        (tests_root / "skills" / "test_skill_output_compliance.py").touch()
        (tests_root / "infra" / "test_pretty_output_hook_infra.py").touch()
        result = build_test_scope(
            changed_files={"src/autoskillit/execution/headless/__init__.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        paths = {p for p in result}
        path_names = {p.name for p in paths}
        assert any(p.name == "test_skill_output_compliance.py" for p in paths), (
            "headless change must include test_skill_output_compliance.py"
        )
        assert any(p.name == "test_pretty_output_hook_infra.py" for p in paths), (
            "headless change must include infra/test_pretty_output_hook_infra.py"
        )
        assert "skills" not in path_names, (
            "headless change must NOT include the entire skills/ dir"
        )

    def test_process_includes_skills_compliance_test(self, tmp_path: Path) -> None:
        """process/*.py change → skills compliance file; skills/ dir excluded."""
        tests_root = self._make_tests_root(tmp_path, self.ALL_DIRS)
        (tests_root / "skills" / "test_skill_output_compliance.py").touch()
        (tests_root / "infra" / "test_pretty_output_hook_infra.py").touch()
        result = build_test_scope(
            changed_files={"src/autoskillit/execution/process/runner.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        paths = {p for p in result}
        path_names = {p.name for p in paths}
        assert any(p.name == "test_skill_output_compliance.py" for p in paths)
        assert any(p.name == "test_pretty_output_hook_infra.py" for p in paths), (
            "process change must include infra/test_pretty_output_hook_infra.py"
        )
        assert "skills" not in path_names

    def test_other_execution_module_excludes_skills(self, tmp_path: Path) -> None:
        """anomaly_detection.py change → no skills/ path (neither dir nor compliance file)."""
        tests_root = self._make_tests_root(tmp_path, self.ALL_DIRS)
        (tests_root / "skills" / "test_skill_output_compliance.py").touch()
        result = build_test_scope(
            changed_files={"src/autoskillit/execution/anomaly_detection.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        paths = {p for p in result}
        path_names = {p.name for p in paths}
        assert "skills" not in path_names
        assert not any(p.name == "test_skill_output_compliance.py" for p in paths)

    def test_execution_fallthrough_uses_infra_file_not_dir(self, tmp_path: Path) -> None:
        """Execution-layer fallthrough uses infra file-level entry, not whole infra/ dir.

        Uses execution/__init__.py: its stem (__init__) is absent from MODULE_CASCADE_EXECUTION
        and the file is not inside a subdirectory covered by SUBPKG_CASCADE_EXECUTION, so it
        truly falls through to LAYER_CASCADE_CONSERVATIVE["execution"].
        """
        tests_root = self._make_tests_root(tmp_path, self.ALL_DIRS)
        (tests_root / "infra" / "test_pretty_output_hook_infra.py").touch()
        result = build_test_scope(
            changed_files={"src/autoskillit/execution/__init__.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        path_names = {p.name for p in result}
        assert "infra" not in path_names, "execution layer fallthrough must not include infra/ dir"
        assert "test_pretty_output_hook_infra.py" in path_names, (
            "execution layer fallthrough must include infra/test_pretty_output_hook_infra.py"
        )

    def test_session_subpkg_uses_narrowed_cli_fleet(self, tmp_path: Path) -> None:
        """execution/session/ change → specific cli/fleet test files, NOT full dirs."""
        tests_root = self._make_tests_root(tmp_path, self.ALL_DIRS)
        for f in [
            "cli/test_session_launch.py",
            "cli/test_order_resume.py",
            "fleet/test_result_parser.py",
            "fleet/test_dispatch_outcome_classifier.py",
        ]:
            (tests_root / f).touch()
        result = build_test_scope(
            changed_files={"src/autoskillit/execution/session/_session_state.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        assert "cli" not in dir_names  # full cli/ dir must not be a scope item
        assert "fleet" not in dir_names  # full fleet/ dir must not be a scope item
        assert (tests_root / "cli" / "test_session_launch.py") in result
        assert (tests_root / "fleet" / "test_result_parser.py") in result
        assert "execution" in dir_names
        assert "server" in dir_names

    def test_session_subpkg_excludes_unrelated_cli_fleet(self, tmp_path: Path) -> None:
        """execution/session/ change must NOT include unrelated cli/fleet tests."""
        tests_root = self._make_tests_root(tmp_path, self.ALL_DIRS)
        (tests_root / "cli" / "test_doctor.py").touch()
        (tests_root / "fleet" / "test_api.py").touch()
        result = build_test_scope(
            changed_files={"src/autoskillit/execution/session/_session_state.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        result_names = {p.name for p in result}
        assert "test_doctor.py" not in result_names
        assert "test_api.py" not in result_names


class TestClosureExecutionNarrowCascade:
    """__init__.py closure expansion for execution package."""

    ALL_DIRS = _ALL_DIRS

    def _make_execution_layout(self, tmp_path: Path, modules: dict[str, str]) -> Path:
        exec_dir = tmp_path / "src" / "autoskillit" / "execution"
        exec_dir.mkdir(parents=True)
        for name, content in modules.items():
            (exec_dir / name).write_text(content)
        tests_root = tmp_path / "tests"
        for d in self.ALL_DIRS:
            (tests_root / d).mkdir(parents=True, exist_ok=True)
        return tests_root

    def test_init_closure_narrow_single_cause(self, tmp_path: Path) -> None:
        """
        Changing anomaly_detection.py triggers closure to add execution/__init__.py.
        Because anomaly_detection is narrow, the __init__ back-propagation
        should still resolve to frozenset({"execution"}).
        """
        tests_root = self._make_execution_layout(
            tmp_path,
            {
                "anomaly_detection.py": "",
                "__init__.py": "from .anomaly_detection import AnomalyDetector\n",
            },
        )
        result = build_test_scope(
            changed_files={"src/autoskillit/execution/anomaly_detection.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        assert "execution" in dir_names
        for excluded in ["core", "cli", "server", "workspace", "migration"]:
            assert excluded not in dir_names, (
                f"narrow closure cascade should not include {excluded}"
            )

    def test_init_closure_mixed_causes_falls_through(self, tmp_path: Path) -> None:
        """
        Changing both anomaly_detection.py (narrow) and headless.py (wide/unknown)
        → __init__ closure must fall through to cascade_map["execution"].
        """
        tests_root = self._make_execution_layout(
            tmp_path,
            {
                "anomaly_detection.py": "",
                "headless.py": "",
                "__init__.py": (
                    "from .anomaly_detection import AnomalyDetector\n"
                    "from .headless import HeadlessSession\n"
                ),
            },
        )
        result = build_test_scope(
            changed_files={
                "src/autoskillit/execution/anomaly_detection.py",
                "src/autoskillit/execution/headless.py",
            },
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        # headless is not in MODULE_CASCADE_EXECUTION → fail-open → full execution cascade
        for pkg in ["execution", "server", "cli"]:
            assert pkg in dir_names, f"mixed-cause closure fallback should include {pkg}"

    def test_init_closure_all_narrow_causes_union(self, tmp_path: Path) -> None:
        """
        Changing ci.py + clone_guard.py (both narrow to {"execution"}) →
        __init__ closure union is still {"execution"}.
        """
        tests_root = self._make_execution_layout(
            tmp_path,
            {
                "ci.py": "",
                "clone_guard.py": "",
                "__init__.py": (
                    "from .ci import CIWatcher\nfrom .clone_guard import CloneGuard\n"
                ),
            },
        )
        result = build_test_scope(
            changed_files={
                "src/autoskillit/execution/ci.py",
                "src/autoskillit/execution/clone_guard.py",
            },
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        assert "execution" in dir_names
        for excluded in ["core", "cli", "server", "workspace", "migration"]:
            assert excluded not in dir_names, (
                f"ci+clone_guard union closure should not include {excluded}"
            )


class TestSubpkgCascadeExecution:
    """REQ-MQ-001..004: subpackage-aware cascade routing for execution/ subpackages."""

    ALL_DIRS = _ALL_DIRS

    def _make_tests_root(self, tmp_path: Path, dirs: list[str]) -> Path:
        tests_root = tmp_path / "tests"
        for d in dirs:
            (tests_root / d).mkdir(parents=True, exist_ok=True)
        return tests_root

    def _make_execution_subpkg_layout(
        self,
        tmp_path: Path,
        subpkg: str,
        modules: dict[str, str],
    ) -> Path:
        subpkg_dir = tmp_path / "src" / "autoskillit" / "execution" / subpkg
        subpkg_dir.mkdir(parents=True)
        for name, content in modules.items():
            (subpkg_dir / name).write_text(content)
        tests_root = tmp_path / "tests"
        for d in self.ALL_DIRS:
            (tests_root / d).mkdir(parents=True, exist_ok=True)
        return tests_root

    def test_merge_queue_init_uses_narrow_cascade(self, tmp_path: Path) -> None:
        """execution/merge_queue/__init__.py → narrow {execution} cascade."""
        tests_root = self._make_tests_root(tmp_path, self.ALL_DIRS)
        result = build_test_scope(
            changed_files={"src/autoskillit/execution/merge_queue/__init__.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        assert "execution" in dir_names
        for excluded in ["core", "cli", "server", "workspace", "migration"]:
            assert excluded not in dir_names, (
                f"merge_queue/__init__ cascade should not include {excluded}"
            )

    def test_merge_queue_private_module_uses_narrow_cascade(self, tmp_path: Path) -> None:
        """execution/merge_queue/_merge_queue_classifier.py → narrow {execution} cascade."""
        tests_root = self._make_tests_root(tmp_path, self.ALL_DIRS)
        result = build_test_scope(
            changed_files={"src/autoskillit/execution/merge_queue/_merge_queue_classifier.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        assert "execution" in dir_names
        for excluded in ["core", "cli", "server", "workspace", "migration"]:
            assert excluded not in dir_names, (
                f"merge_queue private module cascade should not include {excluded}"
            )

    def test_merge_queue_and_non_subpkg_file_fails_open(self, tmp_path: Path) -> None:
        """merge_queue file + headless.py (not in any map) → full execution cascade."""
        tests_root = self._make_tests_root(tmp_path, self.ALL_DIRS)
        result = build_test_scope(
            changed_files={
                "src/autoskillit/execution/merge_queue/_merge_queue_classifier.py",
                "src/autoskillit/execution/headless.py",
            },
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        for pkg in ["execution", "server", "cli", "workspace"]:
            assert pkg in dir_names, (
                f"mixed merge_queue+headless should fail open to include {pkg}"
            )

    def test_merge_queue_closure_expansion_stays_narrow(self, tmp_path: Path) -> None:
        """
        _merge_queue_classifier.py re-exported by merge_queue/__init__.py →
        closure expansion of __init__ stays narrow to {execution}.
        """
        tests_root = self._make_execution_subpkg_layout(
            tmp_path,
            "merge_queue",
            {
                "__init__.py": ("from ._merge_queue_classifier import MergeQueueClassifier\n"),
                "_merge_queue_classifier.py": "",
            },
        )
        result = build_test_scope(
            changed_files={"src/autoskillit/execution/merge_queue/_merge_queue_classifier.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        assert "execution" in dir_names
        for excluded in ["core", "cli", "server", "workspace", "migration"]:
            assert excluded not in dir_names, f"merge_queue closure should not include {excluded}"

    def test_subpkg_cascade_exported(self) -> None:
        """SUBPKG_CASCADE_EXECUTION is importable and contains 'merge_queue' key."""
        assert "merge_queue" in SUBPKG_CASCADE_EXECUTION
        assert SUBPKG_CASCADE_EXECUTION["merge_queue"] == frozenset({"execution"})


def test_headless_and_process_in_subpkg_cascade() -> None:
    """headless and process must be explicit entries in SUBPKG_CASCADE_EXECUTION.

    Files inside execution/headless/ and execution/process/ are detected by
    _file_to_execution_subpkg(), which returns the directory name. The router
    checks SUBPKG_CASCADE_EXECUTION first — MODULE_CASCADE_EXECUTION (stem-keyed)
    is never reached for subpackage files.
    """
    assert "headless" in SUBPKG_CASCADE_EXECUTION, "headless must have explicit cascade entry"
    assert "process" in SUBPKG_CASCADE_EXECUTION, "process must have explicit cascade entry"


def test_session_in_subpkg_cascade() -> None:
    """session must be in SUBPKG_CASCADE_EXECUTION (subpackage, not bare module)."""
    assert "session" in SUBPKG_CASCADE_EXECUTION
