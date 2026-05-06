"""Campaign summary schema v1 — dataclasses, parser, and validator.

Provides the structured contract for the campaign summary sentinel block
emitted by L3 fleet sessions before exit.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from autoskillit.core import FleetErrorCode


class CampaignSummaryStatus(StrEnum):
    """Strict 3-value status enum for per-dispatch summary entries."""

    SUCCESS = "success"
    FAILURE = "failure"
    SKIPPED = "skipped"


class ParseFailureKind(StrEnum):
    SENTINEL_MISSING = "sentinel_missing"
    CAMPAIGN_ID_MISMATCH = "campaign_id_mismatch"
    JSON_DECODE_ERROR = "json_decode_error"
    SCHEMA_VALIDATION_ERROR = "schema_validation_error"
    FIELD_ERROR = "field_error"


@dataclass(frozen=True)
class ParseFailure:
    kind: ParseFailureKind
    message: str

    def __post_init__(self) -> None:
        if not self.message:
            raise ValueError("ParseFailure.message must not be empty")


@dataclass(frozen=True)
class DispatchTokenUsage:
    """Per-dispatch token usage — exactly 4 fields, no extras."""

    input: int
    output: int
    cache_read: int
    cache_creation: int


@dataclass(frozen=True)
class PerDispatchEntry:
    """One entry per dispatch in execution order."""

    name: str
    status: CampaignSummaryStatus
    elapsed_seconds: float
    token_usage: DispatchTokenUsage
    dispatched_session_id: str


@dataclass(frozen=True)
class SummaryErrorRecord:
    """One entry per failed dispatch."""

    dispatch_name: str
    code: FleetErrorCode
    message: str
    dispatched_session_id: str


@dataclass(frozen=True)
class CampaignSummary:
    """Campaign summary schema v1 — top-level contract."""

    schema_version: int
    campaign_id: str
    campaign_name: str
    dispatch_count: int
    completed_count: int
    failure_count: int
    skipped_count: int
    per_dispatch: list[PerDispatchEntry]
    error_records: list[SummaryErrorRecord]


CampaignParseResult = CampaignSummary | ParseFailure

_SUMMARY_PATTERN = re.compile(
    r"---campaign-summary::(?P<cid>.+?)---[^\n]*\n"
    r"(?P<body>.*?)\n"
    r"---end-campaign-summary::(?P<cid_end>.+?)---",
    re.DOTALL,
)

_FORBIDDEN_AGGREGATE_KEYS = frozenset(
    {
        "total_input_tokens",
        "total_output_tokens",
        "total_cache_read_tokens",
        "total_cache_creation_tokens",
        "total_duration",
        "total_elapsed_seconds",
    }
)

_REQUIRED_TOP_KEYS = frozenset(
    {
        "schema_version",
        "campaign_id",
        "campaign_name",
        "dispatch_count",
        "completed_count",
        "failure_count",
        "skipped_count",
        "per_dispatch",
        "error_records",
    }
)


def validate_campaign_summary(data: dict[str, Any]) -> list[str]:
    """Validate raw JSON dict against campaign summary schema v1.

    Returns list of error strings. Empty list = valid.
    """
    errors: list[str] = []
    for key in _FORBIDDEN_AGGREGATE_KEYS:
        if key in data:
            errors.append(f"Forbidden aggregate field present: {key}")
    for key in data:
        if key.startswith("total_") and key not in _REQUIRED_TOP_KEYS:
            errors.append(f"Forbidden aggregate field present: {key}")
    missing = _REQUIRED_TOP_KEYS - set(data.keys())
    if missing:
        errors.append(f"Missing required fields: {sorted(missing)}")
    if data.get("schema_version") != 1:
        errors.append(f"schema_version must be 1, got {data.get('schema_version')}")
    for i, entry in enumerate(data.get("per_dispatch", [])):
        status = entry.get("status")
        try:
            CampaignSummaryStatus(status)
        except (ValueError, KeyError):
            errors.append(f"per_dispatch[{i}].status invalid: {status!r}")
        tu = entry.get("token_usage")
        if isinstance(tu, dict):
            tu_keys = set(tu.keys())
            expected = {"input", "output", "cache_read", "cache_creation"}
            if tu_keys != expected:
                errors.append(
                    f"per_dispatch[{i}].token_usage must have exactly "
                    f"{sorted(expected)}, got {sorted(tu_keys)}"
                )
    return errors


def parse_campaign_summary(text: str, campaign_id: str) -> CampaignParseResult:
    """Parse campaign summary from sentinel-wrapped text.

    Returns CampaignSummary on success, ParseFailure with a specific kind on any failure.
    """
    match = _SUMMARY_PATTERN.search(text)
    if match is None:
        return ParseFailure(
            ParseFailureKind.SENTINEL_MISSING, "No campaign summary sentinel found"
        )
    if match.group("cid") != campaign_id or match.group("cid_end") != campaign_id:
        cid, cid_end = match.group("cid"), match.group("cid_end")
        return ParseFailure(
            ParseFailureKind.CAMPAIGN_ID_MISMATCH,
            f"Sentinel ids {cid!r}/{cid_end!r} do not match expected {campaign_id!r}",
        )
    try:
        data = json.loads(match.group("body"))
    except json.JSONDecodeError as exc:
        return ParseFailure(
            ParseFailureKind.JSON_DECODE_ERROR,
            f"{exc.msg} (line {exc.lineno} col {exc.colno})",
        )
    errors = validate_campaign_summary(data)
    if errors:
        return ParseFailure(ParseFailureKind.SCHEMA_VALIDATION_ERROR, "; ".join(errors))
    try:
        per_dispatch = [
            PerDispatchEntry(
                name=e["name"],
                status=CampaignSummaryStatus(e["status"]),
                elapsed_seconds=float(e["elapsed_seconds"]),
                token_usage=DispatchTokenUsage(
                    input=e["token_usage"]["input"],
                    output=e["token_usage"]["output"],
                    cache_read=e["token_usage"]["cache_read"],
                    cache_creation=e["token_usage"]["cache_creation"],
                ),
                dispatched_session_id=e.get("dispatched_session_id")
                or e.get("l3_session_id")
                or e.get("l2_session_id", ""),
            )
            for e in data["per_dispatch"]
        ]
        error_records = [
            SummaryErrorRecord(
                dispatch_name=r["dispatch_name"],
                code=FleetErrorCode(r["code"]),
                message=r["message"],
                dispatched_session_id=r.get("dispatched_session_id")
                or r.get("l3_session_id")
                or r.get("l2_session_id", ""),
            )
            for r in data["error_records"]
        ]
        return CampaignSummary(
            schema_version=data["schema_version"],
            campaign_id=data["campaign_id"],
            campaign_name=data["campaign_name"],
            dispatch_count=data["dispatch_count"],
            completed_count=data["completed_count"],
            failure_count=data["failure_count"],
            skipped_count=data["skipped_count"],
            per_dispatch=per_dispatch,
            error_records=error_records,
        )
    except (KeyError, TypeError, ValueError) as exc:
        return ParseFailure(ParseFailureKind.FIELD_ERROR, f"Field extraction failed: {exc}")


def serialize_campaign_summary(summary: CampaignSummary) -> str:
    """Serialize a CampaignSummary to sentinel-wrapped JSON text."""
    data = {
        "schema_version": summary.schema_version,
        "campaign_id": summary.campaign_id,
        "campaign_name": summary.campaign_name,
        "dispatch_count": summary.dispatch_count,
        "completed_count": summary.completed_count,
        "failure_count": summary.failure_count,
        "skipped_count": summary.skipped_count,
        "per_dispatch": [
            {
                "name": e.name,
                "status": e.status.value,
                "elapsed_seconds": e.elapsed_seconds,
                "token_usage": {
                    "input": e.token_usage.input,
                    "output": e.token_usage.output,
                    "cache_read": e.token_usage.cache_read,
                    "cache_creation": e.token_usage.cache_creation,
                },
                "dispatched_session_id": e.dispatched_session_id,
            }
            for e in summary.per_dispatch
        ],
        "error_records": [
            {
                "dispatch_name": r.dispatch_name,
                "code": r.code,
                "message": r.message,
                "dispatched_session_id": r.dispatched_session_id,
            }
            for r in summary.error_records
        ],
    }
    body = json.dumps(data, indent=2)
    cid = summary.campaign_id
    return f"---campaign-summary::{cid}---\n{body}\n---end-campaign-summary::{cid}---"
