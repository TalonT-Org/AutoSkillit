"""Split integrity tests for execution/headless/ _headless_evidence split.

Verifies that symbols moved to _headless_evidence are importable.
"""

import pytest

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


class TestHeadlessEvidenceModuleExists:
    """Symbols moved to _headless_evidence are importable from there."""

    def test__adapt_agent_result_importable(self):
        from autoskillit.execution.headless._headless_evidence import _adapt_agent_result

        assert callable(_adapt_agent_result)

    def test__compute_write_evidence_importable(self):
        from autoskillit.execution.headless._headless_evidence import _compute_write_evidence

        assert callable(_compute_write_evidence)

    def test__build_session_telemetry_importable(self):
        from autoskillit.execution.headless._headless_evidence import _build_session_telemetry

        assert callable(_build_session_telemetry)

    def test__capture_failure_importable(self):
        from autoskillit.execution.headless._headless_evidence import _capture_failure

        assert callable(_capture_failure)
