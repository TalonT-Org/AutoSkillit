"""Tests for the criterion-schema-drift semantic rule."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit.core.types import Severity
from autoskillit.recipe.validator import run_semantic_rules
from tests.recipe.conftest import _make_workflow

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


@pytest.mark.parametrize(
    ("criteria", "expected_findings"),
    [
        pytest.param(["Run the task"], 1, id="plain-string-criterion"),
        pytest.param(
            [{"text": "Run the task", "type": "must"}],
            0,
            id="structured-criterion",
        ),
    ],
)
def test_criterion_schema_drift_rejects_plain_strings(
    tmp_path: Path,
    criteria: list[object],
    expected_findings: int,
) -> None:
    """The rule accepts structured criteria and reports plain-string entries."""
    manifest_path = tmp_path / "canaries.json"
    manifest_path.write_text(
        json.dumps(
            [
                {
                    "id": "canary-1",
                    "detection_criteria": criteria,
                }
            ]
        )
    )
    recipe = _make_workflow(
        {
            "parse": {
                "tool": "run_python",
                "with": {
                    "callable": "autoskillit.smoke_utils.parse_agent_eval_manifests",
                    "canary_manifest": str(manifest_path),
                },
                "on_success": "done",
                "on_failure": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
    )

    findings = [
        finding
        for finding in run_semantic_rules(recipe)
        if finding.rule == "criterion-schema-drift"
    ]
    assert len(findings) == expected_findings
    if findings:
        assert findings[0].severity == Severity.ERROR
        assert findings[0].step_name == "parse"
        assert "canary-1" in findings[0].message
