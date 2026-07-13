"""Closure-mode report schema for audit-impl (IL-0, stdlib-only).

Typed dataclasses describing a closure report's structure, plus structural
validation that ties every row and the report-level verdict to hashes the
verifier can independently reconstruct.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..closure_hashing import HASH_RE as _HASH_RE
from ..closure_hashing import compute_report_hash, compute_row_hash

__all__ = [
    "CLOSURE_REPORT_SCHEMA_VERSION",
    "CLOSURE_ROW_ALLOWED_ASSESSMENTS",
    "ClosureRow",
    "ClosureReport",
]

CLOSURE_REPORT_SCHEMA_VERSION: int = 1
CLOSURE_ROW_ALLOWED_ASSESSMENTS: frozenset[str] = frozenset(
    {"COVERED", "MISSING", "ODD", "CONFLICT", "NAMED_DEVIATION"}
)

_ALLOWED_ASSESSMENTS = CLOSURE_ROW_ALLOWED_ASSESSMENTS


@dataclass(frozen=True, slots=True)
class ClosureRow:
    requirement_id: str
    requirement_text: str
    source_file: str
    source_line: int
    source_section: str
    assessment: str
    evidence_summary: str
    row_hash: str


@dataclass(frozen=True, slots=True)
class ClosureReport:
    schema_version: int
    request_hash: str
    authority_hash: str
    plan_hashes: tuple[str, ...]
    base_sha: str
    diff_sha: str
    target_sha: str
    requirement_ids: tuple[str, ...]
    rows: tuple[ClosureRow, ...]
    verdict: str
    report_hash: str
    remediation_path: str | None
    generated_at: str

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.schema_version != 1:
            errors.append(f"schema_version must be 1, got {self.schema_version}")
        if not self.rows:
            errors.append("rows must be non-empty")
        if not self.requirement_ids:
            errors.append("requirement_ids must be non-empty")
        if len(self.rows) != len(self.requirement_ids):
            errors.append(
                f"rows/requirement_ids length mismatch: {len(self.rows)} vs "
                f"{len(self.requirement_ids)}"
            )
        seen: set[str] = set()
        n = max(len(self.requirement_ids), len(self.rows))
        for idx in range(n):
            rid = self.requirement_ids[idx] if idx < len(self.requirement_ids) else None
            row = self.rows[idx] if idx < len(self.rows) else None
            if row is None:
                errors.append(f"missing row for requirement_ids[{idx}] {rid!r}")
                assert rid is not None
                seen.add(rid)
                continue
            if rid is None:
                errors.append(
                    f"extra row[{idx}] requirement_id={row.requirement_id!r} "
                    f"with no matching requirement_ids entry"
                )
                continue
            if rid in seen:
                errors.append(f"duplicate requirement_id at index {idx}: {rid!r}")
            seen.add(rid)
            if row.requirement_id != rid:
                errors.append(
                    f"row[{idx}].requirement_id {row.requirement_id!r} "
                    f"!= requirement_ids[{idx}] {rid!r}"
                )
            if row.assessment not in _ALLOWED_ASSESSMENTS:
                errors.append(
                    f"row[{idx}].assessment {row.assessment!r} not in {_ALLOWED_ASSESSMENTS}"
                )
            expected_row_hash = compute_row_hash(
                row.requirement_id,
                row.requirement_text,
                row.assessment,
                row.evidence_summary,
                row.source_file,
                row.source_line,
                row.source_section,
            )
            if row.row_hash != expected_row_hash:
                errors.append(f"row[{idx}].row_hash mismatch (content tampered)")
        expected_report_hash = compute_report_hash(
            self.request_hash, [r.row_hash for r in self.rows], self.verdict
        )
        if self.report_hash != expected_report_hash:
            errors.append("report_hash mismatch (recomputed hash differs)")
        for hash_field in (self.request_hash, self.authority_hash, self.report_hash):
            if not _HASH_RE.match(hash_field):
                errors.append(f"hash field has malformed format: {hash_field!r}")
        for idx, ph in enumerate(self.plan_hashes):
            if not _HASH_RE.match(ph):
                errors.append(f"plan_hashes[{idx}] has malformed format: {ph!r}")
        if self.verdict == "GO":
            blocking = [r for r in self.rows if r.assessment in {"MISSING", "CONFLICT"}]
            if blocking:
                errors.append(
                    f"verdict=GO but {len(blocking)} rows have MISSING/CONFLICT assessments"
                )
        if self.verdict == "NO GO" and (
            self.remediation_path is None or not self.remediation_path
        ):
            errors.append("verdict=NO GO requires non-empty remediation_path")
        return errors

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "request_hash": self.request_hash,
            "authority_hash": self.authority_hash,
            "plan_hashes": list(self.plan_hashes),
            "base_sha": self.base_sha,
            "diff_sha": self.diff_sha,
            "target_sha": self.target_sha,
            "requirement_ids": list(self.requirement_ids),
            "rows": [
                {
                    "requirement_id": r.requirement_id,
                    "requirement_text": r.requirement_text,
                    "source_file": r.source_file,
                    "source_line": r.source_line,
                    "source_section": r.source_section,
                    "assessment": r.assessment,
                    "evidence_summary": r.evidence_summary,
                    "row_hash": r.row_hash,
                }
                for r in self.rows
            ],
            "verdict": self.verdict,
            "report_hash": self.report_hash,
            "remediation_path": self.remediation_path,
            "generated_at": self.generated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ClosureReport:
        try:
            rows_raw = data["rows"]
            rows = tuple(
                ClosureRow(
                    requirement_id=r["requirement_id"],
                    requirement_text=r["requirement_text"],
                    source_file=r["source_file"],
                    source_line=r["source_line"],
                    source_section=r["source_section"],
                    assessment=r["assessment"],
                    evidence_summary=r["evidence_summary"],
                    row_hash=r["row_hash"],
                )
                for r in rows_raw
            )
            return cls(
                schema_version=data["schema_version"],
                request_hash=data["request_hash"],
                authority_hash=data["authority_hash"],
                plan_hashes=tuple(data["plan_hashes"]),
                base_sha=data["base_sha"],
                diff_sha=data["diff_sha"],
                target_sha=data["target_sha"],
                requirement_ids=tuple(data["requirement_ids"]),
                rows=rows,
                verdict=data["verdict"],
                report_hash=data["report_hash"],
                remediation_path=data.get("remediation_path"),
                generated_at=data["generated_at"],
            )
        except KeyError as exc:
            raise ValueError(f"ClosureReport.from_dict missing key: {exc.args[0]}") from exc
        except (TypeError, ValueError) as exc:
            raise ValueError(f"ClosureReport.from_dict invalid payload: {exc}") from exc
