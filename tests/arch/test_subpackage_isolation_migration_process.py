from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

import pytest

from tests.arch._helpers import SRC_ROOT, _extract_module_level_internal_imports

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


def test_migration_api_module_exists() -> None:
    """P14-F3: migration/_api.py must exist and be importable."""
    import autoskillit.migration._api  # noqa: F401


def test_migration_engine_no_module_level_recipe_imports() -> None:
    """P4-F1: migration/engine.py must have no module-level recipe imports."""
    engine_path = SRC_ROOT / "migration" / "engine.py"
    recipe_violations = [
        (stem, ln)
        for stem, ln in _extract_module_level_internal_imports(engine_path)
        if stem == "recipe"
    ]
    assert not recipe_violations, f"module-level recipe imports remain: {recipe_violations}"


class TestGroupCMigration:
    """REQ-SIG-001..008: anyio task group replaces asyncio task scaffolding."""

    def test_no_asyncio_create_task(self):
        source = Path("src/autoskillit/execution/process/__init__.py").read_text()
        assert "asyncio.create_task(" not in source  # REQ-SIG-001

    def test_no_asyncio_wait_call(self):
        source = Path("src/autoskillit/execution/process/__init__.py").read_text()
        assert "asyncio.wait(" not in source  # REQ-SIG-001

    def test_no_asyncio_import_at_runtime(self):
        source = Path("src/autoskillit/execution/process/__init__.py").read_text()
        assert "import asyncio" not in source  # REQ-SIG-001

    def test_anyio_create_task_group_present(self):
        source = Path("src/autoskillit/execution/process/__init__.py").read_text()
        assert "anyio.create_task_group()" in source  # REQ-SIG-002

    def test_scan_done_signals_absent(self):
        source = Path("src/autoskillit/execution/process/__init__.py").read_text()
        assert "def scan_done_signals(" not in source  # REQ-SIG-003

    def test_race_accumulator_present(self):
        source = Path("src/autoskillit/execution/process/_process_race.py").read_text()
        assert "class RaceAccumulator" in source  # REQ-SIG-003

    def test_cancel_scope_cancel_present(self):
        source = Path("src/autoskillit/execution/process/__init__.py").read_text()
        assert "cancel_scope.cancel()" in source  # REQ-SIG-004

    def test_resolve_termination_preserved(self):
        source = Path("src/autoskillit/execution/process/_process_race.py").read_text()
        assert "def resolve_termination(" in source  # REQ-SIG-005

    def test_channel_b_drain_wait_uses_move_on_after(self):
        source = Path("src/autoskillit/execution/process/__init__.py").read_text()
        assert "anyio.move_on_after(" in source  # REQ-SIG-006

    def test_watch_process_present(self):
        source = Path("src/autoskillit/execution/process/_process_race.py").read_text()
        assert "async def _watch_process(" in source  # REQ-SIG-007

    def test_watch_heartbeat_present(self):
        source = Path("src/autoskillit/execution/process/_process_race.py").read_text()
        assert "async def _watch_heartbeat(" in source  # REQ-SIG-007

    def test_watch_session_log_present(self):
        source = Path("src/autoskillit/execution/process/_process_race.py").read_text()
        assert "async def _watch_session_log(" in source  # REQ-SIG-007

    def test_watch_child_activity_present(self):
        source = Path("src/autoskillit/execution/process/_process_race.py").read_text()
        assert "async def _watch_child_activity(" in source  # REQ-SIG-007

    def test_race_signals_fields_unchanged(self):
        from autoskillit.execution.process import RaceSignals

        fields = {f.name for f in dataclasses.fields(RaceSignals)}
        assert fields == {
            "process_exited",
            "process_returncode",
            "channel_a_confirmed",
            "channel_b_status",
            "channel_b_session_id",
            "stdout_session_id",
            "idle_stall",
            "process_exited_event",
            "channel_b_orphaned_tool_result",
            "exit_snapshot",
            "inspector_verdict",
            "lifecycle_observation_complete",
            "pending_task_ids",
            "terminal_task_ids",
            "schedule_wakeup_violation",
            "completion_ceiling_expired",
            "process_observation_snapshot",
        }  # REQ-SIG-008

    def test_race_signals_still_frozen(self):
        from autoskillit.execution.process import RaceSignals

        assert dataclasses.fields(RaceSignals)  # confirms it's a dataclass
        sig = RaceSignals(
            process_exited=False,
            process_returncode=None,
            channel_a_confirmed=False,
            channel_b_status=None,
            channel_b_session_id="",
        )
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            sig.process_exited = True  # REQ-SIG-008: frozen=True preserved


def test_pipeline_fidelity_module_deleted():
    """P2-F1: pipeline/fidelity.py must not exist after groupB."""
    with pytest.raises(ModuleNotFoundError):
        import autoskillit.pipeline.fidelity  # noqa: F401


def test_pipeline_pr_gates_no_longer_has_domain_paths():
    """P2-F2: DOMAIN_PATHS must not be defined in pipeline/pr_gates.py."""
    from pathlib import Path

    src = (
        Path(__file__).parent.parent.parent / "src/autoskillit/pipeline/pr_gates.py"
    ).read_text()
    assert "DOMAIN_PATHS" not in src


def test_pipeline_init_no_longer_exports_domain_paths():
    """P2-F2: DOMAIN_PATHS must not appear in pipeline.__all__."""
    import autoskillit.pipeline as m

    assert "DOMAIN_PATHS" not in m.__all__
    assert "partition_files_by_domain" not in m.__all__


def test_singleton_exemption_comment_matches_both_windows() -> None:
    """The _install_info exemption comment in SINGLETON_ALLOWED_MODULES must
    accurately reflect both the _STABLE_DISMISS_WINDOW and _DEV_DISMISS_WINDOW values."""

    from autoskillit.cli.install._install_info import _DEV_DISMISS_WINDOW, _STABLE_DISMISS_WINDOW

    singleton_guard_file = Path(__file__).with_name("test_subpackage_isolation_singleton_io.py")
    content = singleton_guard_file.read_text(encoding="utf-8")

    def _fmt_td(td: object) -> str:
        import datetime

        if not isinstance(td, datetime.timedelta):
            return repr(td)
        total_seconds = td.total_seconds()
        if total_seconds % 86400 == 0:
            return f"timedelta(days={int(total_seconds // 86400)})"
        if total_seconds % 3600 == 0:
            return f"timedelta(hours={int(total_seconds // 3600)})"
        return repr(td)

    stable_fragment = _fmt_td(_STABLE_DISMISS_WINDOW)
    dev_fragment = _fmt_td(_DEV_DISMISS_WINDOW)

    assert stable_fragment in content, (
        f"Exemption comment in SINGLETON_ALLOWED_MODULES is stale. "
        f"Expected to find '{stable_fragment}' "
        f"(current _STABLE_DISMISS_WINDOW={_STABLE_DISMISS_WINDOW!r}). "
        "Update the comment on the '_install_info' entry."
    )
    assert dev_fragment in content, (
        f"Exemption comment in SINGLETON_ALLOWED_MODULES is stale. "
        f"Expected to find '{dev_fragment}' "
        f"(current _DEV_DISMISS_WINDOW={_DEV_DISMISS_WINDOW!r}). "
        "Update the comment on the '_install_info' entry."
    )


def test_update_checks_docstring_describes_both_windows() -> None:
    """The _update_checks module docstring and _is_dismissed docstring must
    mention both branch-aware window values."""

    src_root = Path(__file__).parent.parent.parent / "src"
    module_path = src_root / "autoskillit" / "cli" / "update" / "_update_checks.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"))

    module_doc = ast.get_docstring(tree) or ""
    assert "timedelta(days=7)" in module_doc or "days=7" in module_doc, (
        "_update_checks module docstring must mention the 7-day stable window"
    )
    assert "timedelta(hours=12)" in module_doc or "hours=12" in module_doc, (
        "_update_checks module docstring must mention the 12-hour dev window"
    )

    # Also verify _is_dismissed has a docstring mentioning both windows
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_is_dismissed":
            func_doc = ast.get_docstring(node) or ""
            assert "days=7" in func_doc or "7 days" in func_doc, (
                "_is_dismissed docstring must mention the 7-day window"
            )
            assert "hours=12" in func_doc or "12 hours" in func_doc, (
                "_is_dismissed docstring must mention the 12-hour window"
            )
            break
