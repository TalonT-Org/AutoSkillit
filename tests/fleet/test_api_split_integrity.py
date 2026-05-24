"""Structural guard: fleet._api.py split into cohesive modules."""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]


class TestExpressionModuleExists:
    def test_evaluate_skip_when_importable(self):
        from autoskillit.fleet._expressions import evaluate_skip_when
        assert callable(evaluate_skip_when)

    def test_interpolate_campaign_refs_importable(self):
        from autoskillit.fleet._expressions import _interpolate_campaign_refs
        assert callable(_interpolate_campaign_refs)

    def test_campaign_ref_re_importable(self):
        from autoskillit.fleet._expressions import _CAMPAIGN_REF_RE
        assert _CAMPAIGN_REF_RE is not None


class TestCaptureModuleExists:
    def test_extract_captures_importable(self):
        from autoskillit.fleet._capture import _extract_captures
        assert callable(_extract_captures)

    def test_capture_completeness_error_importable(self):
        from autoskillit.fleet._capture import CaptureCompletenessError
        assert issubclass(CaptureCompletenessError, RuntimeError)

    def test_normalize_capture_spec_importable(self):
        from autoskillit.fleet._capture import _normalize_capture_spec
        assert callable(_normalize_capture_spec)


class TestOutcomeModuleExists:
    def test_classify_dispatch_outcome_importable(self):
        from autoskillit.fleet._outcome import classify_dispatch_outcome
        assert callable(classify_dispatch_outcome)


class TestPublicAPISurfacePreserved:
    def test_all_public_symbols_accessible_via_gateway(self):
        from autoskillit.fleet import (
            CaptureCompletenessError,
            classify_dispatch_outcome,
            evaluate_skip_when,
            execute_dispatch,
            _write_pid,
        )
        assert callable(execute_dispatch)
        assert callable(evaluate_skip_when)
        assert callable(classify_dispatch_outcome)
        assert issubclass(CaptureCompletenessError, RuntimeError)
        assert callable(_write_pid)
