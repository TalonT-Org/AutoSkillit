"""Conformance coverage for retained Claude Code provider-failure evidence."""

from __future__ import annotations

import json

import pytest

from autoskillit.execution.session._session_model import (
    _HANDLED_RECORD_TYPES,
    parse_session_result,
)
from tests.fixtures.claude_code import (
    ALL_FIXTURE_NAMES,
    API_ERROR_404_TERMINAL_V1,
    AUTHENTICATION_FAILED_V1,
    WEEKLY_RATE_LIMIT_REJECTED_V1,
    fixture_path,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


def _fixture_text(name: str) -> str:
    return fixture_path(name).read_text()


@pytest.mark.parametrize("fixture_name", ALL_FIXTURE_NAMES)
def test_fixture_record_types_match_the_parser_vocabulary(fixture_name: str) -> None:
    """Typed fixture records and parser branches stay bidirectionally aligned."""
    records = [json.loads(line) for line in _fixture_text(fixture_name).splitlines()]
    typed_record_types = {
        record["type"] for record in records if isinstance(record.get("type"), str)
    }
    assert typed_record_types <= _HANDLED_RECORD_TYPES


def test_every_handled_record_type_has_a_conformance_fixture() -> None:
    """A parser branch cannot be added without a matching retained transcript shape."""
    fixture_types = {
        record["type"]
        for fixture_name in ALL_FIXTURE_NAMES
        for record in (json.loads(line) for line in _fixture_text(fixture_name).splitlines())
        if isinstance(record.get("type"), str)
    }
    assert fixture_types == _HANDLED_RECORD_TYPES


def test_weekly_rate_limit_rejection_retains_reset_evidence() -> None:
    session = parse_session_result(_fixture_text(WEEKLY_RATE_LIMIT_REJECTED_V1))

    assert session.rate_limit_type == "seven_day"
    assert session.rate_limit_status == "rejected"
    assert session.rate_limit_resets_at_epoch == 1735689600


def test_terminal_api_error_retains_terminal_evidence() -> None:
    session = parse_session_result(_fixture_text(API_ERROR_404_TERMINAL_V1))

    assert session.terminal_reason == "api_error"
    assert session.api_error_message_seen is True


def test_untyped_authentication_failure_retains_the_provider_code() -> None:
    session = parse_session_result(_fixture_text(AUTHENTICATION_FAILED_V1))

    assert session.provider_error_code == "authentication_failed"


def test_typed_api_retry_error_does_not_become_a_terminal_provider_code() -> None:
    session = parse_session_result(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "system",
                        "subtype": "api_retry",
                        "error": "authentication_failed",
                    }
                ),
                json.dumps(
                    {
                        "type": "result",
                        "subtype": "empty_output",
                        "is_error": True,
                        "result": "",
                        "session_id": "typed-api-retry",
                    }
                ),
            ]
        )
    )

    assert session.provider_error_code == ""
