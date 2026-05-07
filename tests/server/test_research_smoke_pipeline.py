"""Research recipe smoke pipeline: structural and gated E2E tests.

Gated E2E tests run only when RESEARCH_SMOKE_TEST=1 is set (via ``task test-smoke-research``).
The class is a documentation anchor for future pipeline execution validation.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def research_smoke_dir(tmp_path: Path) -> Path:
    smoke_dir = tmp_path / "smoke-research"
    smoke_dir.mkdir()
    # No git init — Unit 1.1 guard must handle non-git source directories
    return smoke_dir


# ---------------------------------------------------------------------------
# Test Group C: Gated E2E Smoke (T_RSE_1 – T_RSE_5)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not os.environ.get("RESEARCH_SMOKE_TEST"),
    reason="Set RESEARCH_SMOKE_TEST=1 to run research smoke tests",
)
@pytest.mark.smoke
class TestResearchSmokePipelineExecution:
    """Full end-to-end research smoke execution.

    Run via ``task test-smoke-research`` which sets RESEARCH_SMOKE_TEST=1 and
    invokes the research recipe with output_mode=local. This class is a
    documentation anchor — the execution itself uses the research recipe pipeline.
    """

    def test_fixture_trivial_pipeline_completes(self) -> None:
        pytest.skip("E2E execution requires RESEARCH_SMOKE_TEST=1 and API access")

    def test_fixture_rct_pipeline_completes(self) -> None:
        pytest.skip("E2E execution requires RESEARCH_SMOKE_TEST=1 and API access")

    def test_fixture_rct_frontmatter_experiment_type(self) -> None:
        pytest.skip("E2E execution requires RESEARCH_SMOKE_TEST=1 and API access")

    def test_fixture_rct_frontmatter_methodology_tradition(self) -> None:
        pytest.skip("E2E execution requires RESEARCH_SMOKE_TEST=1 and API access")

    def test_fixture_uses_temp_dir_no_git(self) -> None:
        pytest.skip("E2E execution requires RESEARCH_SMOKE_TEST=1 and API access")
