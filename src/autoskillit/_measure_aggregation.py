"""Stdlib-only measure aggregation authority: availability states, the TokenMeasure
value type, and aggregation over (harness, provider) scopes.
Shared by package consumers (via autoskillit.core) and standalone hook projections
(bare-name import).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum, unique
from typing import TypedDict

logger = logging.getLogger(__name__)  # noqa: TID251 — stdlib-only hook-callable authority

__all__ = [
    "CANONICAL_ACCOUNTING_FIELDS",
    "FieldAggregate",
    "MAXIMUM_FIELDS",
    "MeasureAggregate",
    "MeasureRatio",
    "MeasureRecord",
    "MeasureScope",
    "SerializedTokenMeasure",
    "SourcePair",
    "TOKEN_CLASS_FIELDS",
    "TokenMeasure",
    "TokenMeasureState",
    "TokenNormalization",
    "UnnormalizedPoolError",
    "aggregate_measures",
    "group_by_pair",
    "measure_ratio",
    "render_measure",
    "render_ratio",
]


@unique
class TokenMeasureState(StrEnum):
    MEASURED = "measured"
    MEASURED_ZERO = "measured_zero"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


# Canonical accounting field set shared by TokenEntry, fleet pair totals,
# OTLP observation aggregation, and the sidecar accounting measures frozenset.
# Order is deliberate (cache_write before cache_read) so iteration across all
# consumers stays consistent. peak_context is intentionally NOT in this tuple —
# it is a separate measure aggregated only by aggregate_token_observations.
CANONICAL_ACCOUNTING_FIELDS: tuple[str, ...] = (
    "input_tokens",
    "output_tokens",
    "cache_write_tokens",
    "cache_read_tokens",
)


class SerializedTokenMeasure(TypedDict):
    """Durable representation of one accounting measure."""

    state: str
    value: int | None


@dataclass(frozen=True, slots=True)
class TokenMeasure:
    """One token accounting observation with an explicit availability state."""

    state: TokenMeasureState
    value: int | None = None

    def __post_init__(self) -> None:
        if self.value is not None and (
            isinstance(self.value, bool) or not isinstance(self.value, int) or self.value < 0
        ):
            raise ValueError("Token measure values must be non-negative integers")
        if self.state is TokenMeasureState.MEASURED:
            if self.value is None or self.value == 0:
                raise ValueError("measured token measures require a positive value")
        elif self.state is TokenMeasureState.MEASURED_ZERO:
            if self.value != 0:
                raise ValueError("measured_zero token measures require value 0")
        elif self.value is not None:
            raise ValueError(f"{self.state.value} token measures cannot carry a value")

    @classmethod
    def observed(cls, value: int) -> TokenMeasure:
        """Build an explicitly observed measurement, including an observed zero."""
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("Observed token values must be non-negative integers")
        return cls(
            TokenMeasureState.MEASURED_ZERO if value == 0 else TokenMeasureState.MEASURED,
            value,
        )

    @classmethod
    def unavailable(cls) -> TokenMeasure:
        return cls(TokenMeasureState.UNAVAILABLE)

    @classmethod
    def unknown(cls) -> TokenMeasure:
        return cls(TokenMeasureState.UNKNOWN)

    @classmethod
    def not_applicable(cls) -> TokenMeasure:
        return cls(TokenMeasureState.NOT_APPLICABLE)

    @classmethod
    def from_dict(cls, raw: object) -> TokenMeasure:
        """Decode a durable measure record without accepting ambiguous numerics."""
        if not isinstance(raw, dict):
            raise ValueError("Token measure must be a mapping")
        if set(raw) != {"state", "value"}:
            raise ValueError("Token measure must contain exactly state and value")
        state = raw["state"]
        value = raw["value"]
        if not isinstance(state, str):
            raise ValueError("Token measure state must be a string")
        try:
            parsed_state = TokenMeasureState(state)
        except ValueError as exc:
            raise ValueError(f"Unknown token measure state: {state!r}") from exc
        if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
            raise ValueError("Token measure value must be an integer or null")
        return cls(parsed_state, value)

    @classmethod
    def measure_from_raw(cls, raw: object, *, legacy: bool = False) -> TokenMeasure:
        """Canonical decoder: dict/int/None → TokenMeasure with legacy-zero overload.

        Downgrades to unknown on malformed input while logging the offending shape.
        """
        if isinstance(raw, dict):
            try:
                return cls.from_dict(raw)
            except ValueError as exc:
                logger.debug(
                    "token_measure_decode_downgrade_to_unknown",
                    extra={
                        "raw_kind": type(raw).__name__,
                        "raw_keys": sorted(raw.keys()) if isinstance(raw, Mapping) else None,
                        "error": str(exc),
                    },
                )
                return cls.unknown()
        if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0:
            if legacy and raw == 0:
                return cls.unknown()
            return cls.observed(raw)
        return cls.unknown()

    @classmethod
    def measure_from_raw_to_serialized(
        cls, raw: object, *, legacy: bool = False
    ) -> SerializedTokenMeasure:
        """Decode through the canonical helper and serialize for durable storage."""
        return cls.measure_from_raw(raw, legacy=legacy).to_dict()

    @staticmethod
    def combine_or_unknown(left: TokenMeasure, right: TokenMeasure) -> TokenMeasure:
        """Combine two measures or downgrade to unknown on incompatible states."""
        try:
            return left.combine(right)
        except ValueError as exc:
            logger.debug(
                "token_measure_combine_downgrade_to_unknown",
                extra={
                    "left_state": left.state.value,
                    "right_state": right.state.value,
                    "error": str(exc),
                },
            )
            return TokenMeasure.unknown()

    @staticmethod
    def maximum_or_unknown(left: TokenMeasure, right: TokenMeasure) -> TokenMeasure:
        """Take the maximum of two measures or downgrade to unknown on conflict."""
        try:
            return left.maximum(right)
        except ValueError as exc:
            logger.debug(
                "token_measure_maximum_downgrade_to_unknown",
                extra={
                    "left_state": left.state.value,
                    "right_state": right.state.value,
                    "error": str(exc),
                },
            )
            return TokenMeasure.unknown()

    def to_dict(self) -> SerializedTokenMeasure:
        return {"state": self.state.value, "value": self.value}

    def combine(self, other: TokenMeasure) -> TokenMeasure:
        """Combine compatible observations without converting absence into zero."""
        if self.value is not None and other.value is not None:
            return self.observed(self.value + other.value)
        if self.state is other.state:
            return self
        raise ValueError(
            "Cannot combine token measures with incompatible availability states: "
            f"{self.state.value!r} vs {other.state.value!r}"
        )

    def maximum(self, other: TokenMeasure) -> TokenMeasure:
        """Return the observed maximum or reject incompatible availability evidence."""
        if self.value is not None and other.value is not None:
            return self.observed(max(self.value, other.value))
        if self.state is other.state:
            return self
        raise ValueError(
            "Cannot compare token measures with incompatible availability states: "
            f"{self.state.value!r} vs {other.state.value!r}"
        )


TOKEN_CLASS_FIELDS: frozenset[str] = frozenset({*CANONICAL_ACCOUNTING_FIELDS, "peak_context"})
MAXIMUM_FIELDS: frozenset[str] = frozenset({"peak_context"})
_OBSERVED_STATES = frozenset({TokenMeasureState.MEASURED, TokenMeasureState.MEASURED_ZERO})
_CANNOT_PRODUCE_STATES = frozenset(
    {TokenMeasureState.UNAVAILABLE, TokenMeasureState.NOT_APPLICABLE}
)


@dataclass(frozen=True, slots=True, order=True)
class SourcePair:
    harness: str
    provider: str

    def __post_init__(self) -> None:
        if not isinstance(self.harness, str) or not self.harness:
            raise ValueError("harness must be a non-empty str")
        if not isinstance(self.provider, str) or not self.provider:
            raise ValueError("provider must be a non-empty str")

    @property
    def label(self) -> str:
        return f"{self.harness}/{self.provider}"


@dataclass(frozen=True, slots=True)
class MeasureRecord:
    pair: SourcePair
    measures: Mapping[str, TokenMeasure]

    def __post_init__(self) -> None:
        if not isinstance(self.pair, SourcePair):
            raise TypeError("pair must be a SourcePair")
        if any(not isinstance(key, str) for key in self.measures):
            raise TypeError("measure keys must be strings")
        if any(not isinstance(value, TokenMeasure) for value in self.measures.values()):
            raise TypeError("measure values must be TokenMeasure instances")
        object.__setattr__(self, "measures", dict(self.measures))


@dataclass(frozen=True, slots=True)
class MeasureScope:
    pairs: frozenset[SourcePair]
    normalization: str | None = None

    def __post_init__(self) -> None:
        if not self.pairs:
            raise ValueError("scope pairs must not be empty")


@dataclass(frozen=True, slots=True)
class TokenNormalization:
    name: str
    version: int
    transform: Callable[[SourcePair, Mapping[str, TokenMeasure]], Mapping[str, TokenMeasure]]

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("normalization name must be a non-empty str")
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 1:
            raise ValueError("normalization version must be a positive int")

    @property
    def identity(self) -> str:
        return f"{self.name}@v{self.version}"


class UnnormalizedPoolError(ValueError):
    """Token-class fields cannot pool across (harness, provider) pairs without a
    named, versioned TokenNormalization.
    """


@dataclass(frozen=True, slots=True)
class FieldAggregate:
    """A field total with one state_counts key per TokenMeasureState.

    Counts include every run in the scope, even after the total becomes unknown.
    """

    field: str
    value: TokenMeasure
    state_counts: Mapping[TokenMeasureState, int]

    @property
    def reporting_runs(self) -> int:
        return (
            self.state_counts[TokenMeasureState.MEASURED]
            + self.state_counts[TokenMeasureState.MEASURED_ZERO]
        )


@dataclass(frozen=True, slots=True)
class MeasureAggregate:
    scope: MeasureScope
    runs: int
    fields: Mapping[str, FieldAggregate]


@dataclass(frozen=True, slots=True)
class MeasureRatio:
    """A ratio of eligible sums with explicit availability and run counts.

    Field names are non-empty strings; totals and counts are non-negative integers.
    Only observed states carry values, zero for measured_zero and positive for
    measured. Observed ratios require a positive sample size and denominator,
    with no unknown runs. Unknown ratios require at least one unknown run.
    """

    scope: MeasureScope
    numerator_field: str
    denominator_field: str
    numerator_total: int
    denominator_total: int
    sample_size: int
    excluded_runs: int
    unknown_runs: int
    state: TokenMeasureState
    value: float | None

    def __post_init__(self) -> None:
        for name in ("numerator_field", "denominator_field"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be a non-empty str")
        for name in (
            "numerator_total",
            "denominator_total",
            "sample_size",
            "excluded_runs",
            "unknown_runs",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative int")
        observed = self.state in _OBSERVED_STATES
        if (self.value is not None) != observed:
            raise ValueError("value must be set exactly when state is measured or measured_zero")
        if self.state is TokenMeasureState.MEASURED_ZERO and self.value != 0.0:
            raise ValueError("measured_zero ratio must have value 0.0")
        if self.state is TokenMeasureState.MEASURED and not (
            self.value is not None and self.value > 0
        ):
            raise ValueError("measured ratio must have value > 0")
        if observed and (
            self.sample_size == 0 or self.unknown_runs != 0 or self.denominator_total == 0
        ):
            raise ValueError(
                "observed ratio requires sample_size > 0, unknown_runs == 0 "
                "and denominator_total > 0"
            )
        if self.state is TokenMeasureState.UNKNOWN and self.unknown_runs == 0:
            raise ValueError("unknown ratio requires unknown_runs > 0")


def _scope_for(
    records: Sequence[MeasureRecord],
    fields: Sequence[str],
    normalization: TokenNormalization | None,
) -> MeasureScope:
    if not records:
        raise ValueError("cannot aggregate an empty record set")
    pairs = frozenset(record.pair for record in records)
    token_fields = TOKEN_CLASS_FIELDS.intersection(fields)
    if len(pairs) > 1 and token_fields and normalization is None:
        raise UnnormalizedPoolError(
            f"Token-class fields {sorted(token_fields)} cannot pool across pairs "
            f"{[pair.label for pair in sorted(pairs)]} without a named, versioned normalization"
        )
    return MeasureScope(pairs, normalization.identity if normalization is not None else None)


def _measures_for(
    record: MeasureRecord, normalization: TokenNormalization | None
) -> Mapping[str, TokenMeasure]:
    if normalization is None:
        return record.measures
    out = normalization.transform(record.pair, record.measures)
    if set(out) != set(record.measures):
        raise ValueError("normalization must preserve measure keys")
    if any(not isinstance(value, TokenMeasure) for value in out.values()):
        raise TypeError("normalization values must be TokenMeasure instances")
    return out


def _lookup(measures: Mapping[str, TokenMeasure], field: str) -> TokenMeasure:
    return measures.get(field, TokenMeasure.unknown())


def _ratio_outcome(
    num_total: int,
    den_total: int,
    sample_size: int,
    unknown_runs: int,
    excluded_states: set[TokenMeasureState],
) -> tuple[TokenMeasureState, float | None]:
    if unknown_runs > 0:
        return TokenMeasureState.UNKNOWN, None
    if sample_size > 0 and den_total > 0:
        return (
            (TokenMeasureState.MEASURED, num_total / den_total)
            if num_total > 0
            else (TokenMeasureState.MEASURED_ZERO, 0.0)
        )
    if sample_size > 0:
        return TokenMeasureState.NOT_APPLICABLE, None
    if TokenMeasureState.UNAVAILABLE in excluded_states:
        return TokenMeasureState.UNAVAILABLE, None
    return TokenMeasureState.NOT_APPLICABLE, None


def group_by_pair(records: Iterable[MeasureRecord]) -> dict[SourcePair, list[MeasureRecord]]:
    """Group by the full source pair, preserving each pair's input order."""
    groups: dict[SourcePair, list[MeasureRecord]] = {}
    for record in records:
        groups.setdefault(record.pair, []).append(record)
    return {pair: groups[pair] for pair in sorted(groups)}


def aggregate_measures(
    records: Iterable[MeasureRecord],
    fields: Iterable[str],
    *,
    normalization: TokenNormalization | None = None,
) -> MeasureAggregate:
    """Reduce strict field totals and retain every run's availability state."""
    record_list = list(records)
    wanted = tuple(dict.fromkeys(fields))
    scope = _scope_for(record_list, wanted, normalization)
    normalized = [_measures_for(record, normalization) for record in record_list]
    aggregates: dict[str, FieldAggregate] = {}
    for field in wanted:
        reducer = (
            TokenMeasure.maximum_or_unknown
            if field in MAXIMUM_FIELDS
            else TokenMeasure.combine_or_unknown
        )
        counts = {state: 0 for state in TokenMeasureState}
        running: TokenMeasure | None = None
        for measures in normalized:
            measure = _lookup(measures, field)
            counts[measure.state] += 1
            running = (
                measure
                if running is None
                else running
                if running.state is TokenMeasureState.UNKNOWN
                else reducer(running, measure)
            )
        assert running is not None
        aggregates[field] = FieldAggregate(field, running, counts)
    return MeasureAggregate(scope, len(record_list), aggregates)


def measure_ratio(
    records: Iterable[MeasureRecord],
    numerator: str,
    denominator: str,
    *,
    normalization: TokenNormalization | None = None,
) -> MeasureRatio:
    """Pool eligible sums, excluding cannot-produce runs and retaining unknowns."""
    record_list = list(records)
    scope = _scope_for(record_list, (numerator, denominator), normalization)
    num_total = den_total = sample_size = excluded_runs = unknown_runs = 0
    excluded_states: set[TokenMeasureState] = set()
    for record in record_list:
        measures = _measures_for(record, normalization)
        num = _lookup(measures, numerator)
        den = _lookup(measures, denominator)
        blocked = {num.state, den.state} & _CANNOT_PRODUCE_STATES
        if blocked:
            excluded_runs += 1
            excluded_states |= blocked
            continue
        if num.value is None or den.value is None:
            unknown_runs += 1
            continue
        sample_size += 1
        num_total += num.value
        den_total += den.value
    state, value = _ratio_outcome(num_total, den_total, sample_size, unknown_runs, excluded_states)
    return MeasureRatio(
        scope=scope,
        numerator_field=numerator,
        denominator_field=denominator,
        numerator_total=num_total,
        denominator_total=den_total,
        sample_size=sample_size,
        excluded_runs=excluded_runs,
        unknown_runs=unknown_runs,
        state=state,
        value=value,
    )


def render_measure(measure: TokenMeasure, fmt: Callable[[int], str] = str) -> str:
    """Render digits only for observed values, including explicitly observed zero."""
    return measure.state.value if measure.value is None else fmt(measure.value)


def render_ratio(ratio: MeasureRatio, fmt: Callable[[float], str] = "{:.1f}".format) -> str:
    """Render availability or a numeric ratio together with its sample size."""
    head = fmt(ratio.value) if ratio.value is not None else ratio.state.value
    details = [f"n={ratio.sample_size}"]
    if ratio.unknown_runs:
        details.append(f"{ratio.unknown_runs} unknown")
    if ratio.excluded_runs:
        details.append(f"{ratio.excluded_runs} excluded")
    return f"{head} ({', '.join(details)})"
