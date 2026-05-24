"""Split integrity tests for _headless_helpers and _headless_execute modules."""

from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]

SRC = Path(__file__).resolve().parents[3] / "src" / "autoskillit" / "execution" / "headless"


class TestHeadlessHelpersModuleExists:
    """Symbols moved to _headless_helpers are importable from there."""

    def test_session_log_dir_importable(self):
        from autoskillit.execution.headless._headless_helpers import _session_log_dir

        assert callable(_session_log_dir)

    def test_resolve_pty_mode_importable(self):
        from autoskillit.execution.headless._headless_helpers import _resolve_pty_mode

        assert callable(_resolve_pty_mode)

    def test_resolve_session_log_dir_importable(self):
        from autoskillit.execution.headless._headless_helpers import _resolve_session_log_dir

        assert callable(_resolve_session_log_dir)

    def test_resolve_model_importable(self):
        from autoskillit.execution.headless._headless_helpers import _resolve_model

        assert callable(_resolve_model)

    def test_derive_step_name_importable(self):
        from autoskillit.execution.headless._headless_helpers import (
            _derive_step_name_from_skill_command,
        )

        assert callable(_derive_step_name_from_skill_command)

    def test_recursive_snapshot_importable(self):
        from autoskillit.execution.headless._headless_helpers import _recursive_snapshot

        assert callable(_recursive_snapshot)

    def test_post_session_metrics_importable(self):
        from autoskillit.execution.headless._headless_helpers import PostSessionMetrics

        assert PostSessionMetrics is not None

    def test_compute_post_session_metrics_importable(self):
        from autoskillit.execution.headless._headless_helpers import (
            _compute_post_session_metrics,
        )

        assert callable(_compute_post_session_metrics)


class TestHeadlessExecuteModuleExists:
    """_execute_claude_headless is importable from _headless_execute."""

    def test_execute_claude_headless_importable(self):
        from autoskillit.execution.headless._headless_execute import (
            _execute_claude_headless,
        )

        assert callable(_execute_claude_headless)
