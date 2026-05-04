"""Tests for fleet campaign summary schema v1.

Group S: CampaignSummary dataclasses, parse_campaign_summary, validate_campaign_summary,
and serialize_campaign_summary.
"""

from __future__ import annotations

import json

import pytest

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]

_VALID_CAMPAIGN_ID = "test-cid-123"

_VALID_SUMMARY_DICT: dict = {
    "schema_version": 1,
    "campaign_id": _VALID_CAMPAIGN_ID,
    "campaign_name": "Test Campaign",
    "dispatch_count": 2,
    "completed_count": 1,
    "failure_count": 1,
    "skipped_count": 0,
    "per_dispatch": [
        {
            "name": "dispatch-1",
            "status": "success",
            "elapsed_seconds": 10.5,
            "token_usage": {
                "input": 1000,
                "output": 200,
                "cache_read": 50,
                "cache_creation": 10,
            },
            "l3_session_id": "sess-abc",
        },
        {
            "name": "dispatch-2",
            "status": "failure",
            "elapsed_seconds": 5.0,
            "token_usage": {
                "input": 500,
                "output": 100,
                "cache_read": 0,
                "cache_creation": 0,
            },
            "l3_session_id": "sess-def",
        },
    ],
    "error_records": [
        {
            "dispatch_name": "dispatch-2",
            "code": "fleet_l3_timeout",
            "message": "Timed out after 300s",
            "l3_session_id": "sess-def",
        }
    ],
}


def _make_sentinel_text(data: dict, campaign_id: str = _VALID_CAMPAIGN_ID) -> str:
    body = json.dumps(data, indent=2)
    return (
        f"---campaign-summary::{campaign_id}---\n{body}\n---end-campaign-summary::{campaign_id}---"
    )


_VALID_SENTINEL_TEXT = _make_sentinel_text(_VALID_SUMMARY_DICT)


class TestCampaignSummarySchema:
    def test_campaign_summary_schema_valid_example(self):
        from autoskillit.fleet import CampaignSummary, parse_campaign_summary

        result = parse_campaign_summary(_VALID_SENTINEL_TEXT, _VALID_CAMPAIGN_ID)
        assert isinstance(result, CampaignSummary)
        assert result.schema_version == 1
        assert result.campaign_id == _VALID_CAMPAIGN_ID
        assert result.campaign_name == "Test Campaign"
        assert result.dispatch_count == 2
        assert result.completed_count == 1
        assert result.failure_count == 1
        assert result.skipped_count == 0
        assert len(result.per_dispatch) == 2
        assert len(result.error_records) == 1

    def test_campaign_summary_rejects_aggregate_fields(self):
        from autoskillit.fleet import validate_campaign_summary

        data = {**_VALID_SUMMARY_DICT, "total_input_tokens": 999}
        errors = validate_campaign_summary(data)
        assert any("total_input_tokens" in e for e in errors)

        data2 = {**_VALID_SUMMARY_DICT, "total_output_tokens": 1}
        errors2 = validate_campaign_summary(data2)
        assert any("total_output_tokens" in e for e in errors2)

        data3 = {**_VALID_SUMMARY_DICT, "total_duration": 60}
        errors3 = validate_campaign_summary(data3)
        assert any("total_duration" in e for e in errors3)

    def test_campaign_summary_status_enum_strict(self):
        from autoskillit.fleet import CampaignSummaryStatus, validate_campaign_summary

        assert set(m.value for m in CampaignSummaryStatus) == {"success", "failure", "skipped"}
        assert len(CampaignSummaryStatus) == 3

        bad_entry = {**_VALID_SUMMARY_DICT["per_dispatch"][0], "status": "running"}
        data = {**_VALID_SUMMARY_DICT, "per_dispatch": [bad_entry]}
        errors = validate_campaign_summary(data)
        assert any("status" in e for e in errors)

    def test_per_dispatch_token_usage_exactly_4_keys(self):
        import dataclasses

        from autoskillit.fleet import DispatchTokenUsage

        fields = {f.name for f in dataclasses.fields(DispatchTokenUsage)}
        assert fields == {"input", "output", "cache_read", "cache_creation"}

    def test_sentinel_anchored_to_campaign_id(self):
        from autoskillit.fleet import ParseFailure, ParseFailureKind, parse_campaign_summary

        result = parse_campaign_summary(_VALID_SENTINEL_TEXT, "wrong-id")
        assert isinstance(result, ParseFailure)
        assert result.kind == ParseFailureKind.CAMPAIGN_ID_MISMATCH

    def test_sentinel_parse_missing_end_marker(self):
        from autoskillit.fleet import ParseFailure, ParseFailureKind, parse_campaign_summary

        text_no_end = f"---campaign-summary::{_VALID_CAMPAIGN_ID}---\n{{}}\n"
        result = parse_campaign_summary(text_no_end, _VALID_CAMPAIGN_ID)
        assert isinstance(result, ParseFailure)
        assert result.kind == ParseFailureKind.SENTINEL_MISSING

    def test_campaign_summary_schema_version_is_1(self):
        from autoskillit.fleet import (
            CampaignSummary,
            parse_campaign_summary,
            validate_campaign_summary,
        )

        result = parse_campaign_summary(_VALID_SENTINEL_TEXT, _VALID_CAMPAIGN_ID)
        assert isinstance(result, CampaignSummary)
        assert result.schema_version == 1

        data_v2 = {**_VALID_SUMMARY_DICT, "schema_version": 2}
        errors = validate_campaign_summary(data_v2)
        assert any("schema_version" in e for e in errors)

    def test_campaign_summary_count_fields_required(self):
        from autoskillit.fleet import validate_campaign_summary

        for required_field in (
            "dispatch_count",
            "completed_count",
            "failure_count",
            "skipped_count",
        ):
            data = {k: v for k, v in _VALID_SUMMARY_DICT.items() if k != required_field}
            errors = validate_campaign_summary(data)
            assert errors, f"Expected error for missing {required_field}"

    def test_campaign_summary_roundtrip(self):
        from autoskillit.fleet import (
            CampaignSummary,
            parse_campaign_summary,
            serialize_campaign_summary,
        )

        original = parse_campaign_summary(_VALID_SENTINEL_TEXT, _VALID_CAMPAIGN_ID)
        assert isinstance(original, CampaignSummary)
        text = serialize_campaign_summary(original)
        restored = parse_campaign_summary(text, _VALID_CAMPAIGN_ID)
        assert isinstance(restored, CampaignSummary)
        assert restored.campaign_id == original.campaign_id
        assert restored.dispatch_count == original.dispatch_count
        assert restored.per_dispatch[0].name == original.per_dispatch[0].name
        assert (
            restored.per_dispatch[0].token_usage.input
            == original.per_dispatch[0].token_usage.input
        )
        assert restored.error_records[0].code == original.error_records[0].code

    def test_campaign_summary_error_records_have_code_field(self):
        from autoskillit.fleet import CampaignSummary, SummaryErrorRecord, parse_campaign_summary

        result = parse_campaign_summary(_VALID_SENTINEL_TEXT, _VALID_CAMPAIGN_ID)
        assert isinstance(result, CampaignSummary)
        assert len(result.error_records) == 1
        rec = result.error_records[0]
        assert isinstance(rec, SummaryErrorRecord)
        assert rec.dispatch_name == "dispatch-2"
        assert rec.code == "fleet_l3_timeout"
        assert rec.message == "Timed out after 300s"
        assert rec.l3_session_id == "sess-def"

    def test_campaign_summary_no_cross_dispatch_aggregates(self):
        from autoskillit.fleet import validate_campaign_summary

        for forbidden_key in ("total_anything", "total_cost", "total_tokens"):
            data = {**_VALID_SUMMARY_DICT, forbidden_key: 0}
            errors = validate_campaign_summary(data)
            assert any(forbidden_key in e for e in errors), (
                f"Expected rejection of key {forbidden_key!r}"
            )

    def test_parse_failure_sentinel_missing(self):
        from autoskillit.fleet import ParseFailure, ParseFailureKind, parse_campaign_summary

        result = parse_campaign_summary("no sentinel block here", _VALID_CAMPAIGN_ID)
        assert isinstance(result, ParseFailure)
        assert result.kind == ParseFailureKind.SENTINEL_MISSING
        assert isinstance(result.message, str) and result.message

    def test_parse_failure_campaign_id_mismatch(self):
        from autoskillit.fleet import ParseFailure, ParseFailureKind, parse_campaign_summary

        result = parse_campaign_summary(_VALID_SENTINEL_TEXT, "wrong-campaign-id")
        assert isinstance(result, ParseFailure)
        assert result.kind == ParseFailureKind.CAMPAIGN_ID_MISMATCH

    def test_parse_failure_json_decode_error(self):
        from autoskillit.fleet import ParseFailure, ParseFailureKind, parse_campaign_summary

        text = (
            f"---campaign-summary::{_VALID_CAMPAIGN_ID}---\n"
            "not valid json {{{\n"
            f"---end-campaign-summary::{_VALID_CAMPAIGN_ID}---"
        )
        result = parse_campaign_summary(text, _VALID_CAMPAIGN_ID)
        assert isinstance(result, ParseFailure)
        assert result.kind == ParseFailureKind.JSON_DECODE_ERROR

    def test_parse_failure_schema_validation_error(self):
        from autoskillit.fleet import ParseFailure, ParseFailureKind, parse_campaign_summary

        bad_schema = {**_VALID_SUMMARY_DICT, "schema_version": 99}
        text = _make_sentinel_text(bad_schema)
        result = parse_campaign_summary(text, _VALID_CAMPAIGN_ID)
        assert isinstance(result, ParseFailure)
        assert result.kind == ParseFailureKind.SCHEMA_VALIDATION_ERROR

    def test_parse_failure_field_error(self):
        from autoskillit.fleet import ParseFailure, ParseFailureKind, parse_campaign_summary

        bad_entry = {
            "name": "d1",
            "status": "success",
            # elapsed_seconds omitted → KeyError
            "token_usage": {"input": 1, "output": 1, "cache_read": 0, "cache_creation": 0},
            "l3_session_id": "s",
        }
        data = {**_VALID_SUMMARY_DICT, "per_dispatch": [bad_entry]}
        text = _make_sentinel_text(data)
        result = parse_campaign_summary(text, _VALID_CAMPAIGN_ID)
        assert isinstance(result, ParseFailure)
        assert result.kind == ParseFailureKind.FIELD_ERROR
        assert "elapsed_seconds" in result.message
