"""Tests for ordering-violation telemetry detection over pipeline session records."""

from __future__ import annotations

import pytest

from autoskillit.server.tools._ordering_telemetry import detect_ordering_violations

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


class TestDetectOrderingViolations:
    def test_review_before_plan_detected(self):
        records = [
            {
                "kitchen_id": "k1",
                "step_name": "review_approach",
                "timestamp": "2026-01-01T00:00:00Z",
            },
            {
                "kitchen_id": "k1",
                "step_name": "rectify",
                "timestamp": "2026-01-01T00:05:00Z",
            },
        ]
        violations = detect_ordering_violations(records)
        assert len(violations) == 1
        assert violations[0]["kitchen_id"] == "k1"
        assert violations[0]["violation_type"] == "REVIEW_BEFORE_PLAN"
        assert violations[0]["step_name"] == "review_approach"

    def test_in_order_no_violation(self):
        records = [
            {
                "kitchen_id": "k1",
                "step_name": "rectify",
                "timestamp": "2026-01-01T00:00:00Z",
            },
            {
                "kitchen_id": "k1",
                "step_name": "review_approach",
                "timestamp": "2026-01-01T00:05:00Z",
            },
        ]
        assert detect_ordering_violations(records) == []

    def test_make_plan_predecessor_also_satisfies(self):
        records = [
            {
                "kitchen_id": "k1",
                "step_name": "make_plan",
                "timestamp": "2026-01-01T00:00:00Z",
            },
            {
                "kitchen_id": "k1",
                "step_name": "review_approach",
                "timestamp": "2026-01-01T00:05:00Z",
            },
        ]
        assert detect_ordering_violations(records) == []

    def test_review_without_plan_step_flagged(self):
        records = [
            {
                "kitchen_id": "k1",
                "step_name": "review_approach",
                "timestamp": "2026-01-01T00:00:00Z",
            },
        ]
        violations = detect_ordering_violations(records)
        assert len(violations) == 1

    def test_multiple_kitchens_scoped_independently(self):
        records = [
            {
                "kitchen_id": "k1",
                "step_name": "review_approach",
                "timestamp": "2026-01-01T00:00:00Z",
            },
            {
                "kitchen_id": "k1",
                "step_name": "rectify",
                "timestamp": "2026-01-01T00:05:00Z",
            },
            {
                "kitchen_id": "k2",
                "step_name": "rectify",
                "timestamp": "2026-01-01T00:00:00Z",
            },
            {
                "kitchen_id": "k2",
                "step_name": "review_approach",
                "timestamp": "2026-01-01T00:05:00Z",
            },
        ]
        violations = detect_ordering_violations(records)
        assert len(violations) == 1
        assert violations[0]["kitchen_id"] == "k1"

    def test_empty_input_returns_no_violations(self):
        assert detect_ordering_violations([]) == []
