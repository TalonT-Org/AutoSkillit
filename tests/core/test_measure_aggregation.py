from __future__ import annotations

import ast
import dataclasses
import subprocess
import sys
from collections.abc import Mapping
from typing import cast

import pytest

import autoskillit.core as aggregation

pytestmark = [pytest.mark.layer("core"), pytest.mark.medium]

_PKG_ROOT = aggregation.pkg_root()
_State = aggregation.TokenMeasureState


def _obs(value: int) -> aggregation.TokenMeasure:
    return aggregation.TokenMeasure.observed(value)


def _unav() -> aggregation.TokenMeasure:
    return aggregation.TokenMeasure.unavailable()


def _unk() -> aggregation.TokenMeasure:
    return aggregation.TokenMeasure.unknown()


def _na() -> aggregation.TokenMeasure:
    return aggregation.TokenMeasure.not_applicable()


def _rec(
    harness: str = "codex", provider: str = "codex", **measures: aggregation.TokenMeasure
) -> aggregation.MeasureRecord:
    return aggregation.MeasureRecord(aggregation.SourcePair(harness, provider), measures)


def _identity() -> aggregation.TokenNormalization:
    return aggregation.TokenNormalization(
        "test-identity", 1, lambda pair, measures: dict(measures)
    )


def _import_module_names(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom):
        assert node.level == 0
        return [node.module or ""]
    return []


def _assigned_names(statement: ast.stmt) -> set[str]:
    targets = getattr(statement, "targets", None)
    if targets is None:
        target = getattr(statement, "target", None)
        targets = [target] if target is not None else []
    return {target.id for target in targets if isinstance(target, ast.Name)}


class TestStdlibOnlyPlacement:
    def test_module_imports_only_stdlib(self) -> None:
        tree = ast.parse((_PKG_ROOT / "_measure_aggregation.py").read_text())
        stdlib = sys.stdlib_module_names | {"__future__"}
        for node in ast.walk(tree):
            for name in _import_module_names(node):
                assert name.split(".", 1)[0] in stdlib

    def test_module_imports_standalone_without_package(self) -> None:
        code = f"""
import sys
sys.path.insert(0, {str(_PKG_ROOT)!r})
import _measure_aggregation as m
assert m.TokenMeasure.observed(0).state is m.TokenMeasureState.MEASURED_ZERO
assert m.TokenMeasure.measure_from_raw({{"state": "unavailable", "value": None}}).value is None
r = m.MeasureRecord(m.SourcePair("h", "p"), {{
    "input_tokens": m.TokenMeasure.observed(4),
    "output_tokens": m.TokenMeasure.observed(2),
}})
assert m.aggregate_measures([r], ["input_tokens"]).fields["input_tokens"].value.value == 4
assert m.measure_ratio([r], "input_tokens", "output_tokens").value == 2.0
assert "autoskillit" not in sys.modules
print("ok")
"""
        result = subprocess.run(
            [sys.executable, "-I", "-c", code],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "ok", result.stderr


class TestRelocatedPrimitivesAreSingleDefinitions:
    def test_relocated_types_name_the_root_authority(self) -> None:
        assert aggregation.TokenMeasure.__module__ == "autoskillit._measure_aggregation"
        assert _State.__module__ == "autoskillit._measure_aggregation"

    def test_core_types_do_not_define_relocated_primitives_again(self) -> None:
        names = {"TokenMeasure", "TokenMeasureState", "SerializedTokenMeasure"}
        found_classes: set[str] = set()
        assigned_constants: set[str] = set()
        for path in (_PKG_ROOT / "core" / "types").glob("*.py"):
            tree = ast.parse(path.read_text())
            found_classes.update(
                node.name
                for node in ast.walk(tree)
                if isinstance(node, ast.ClassDef) and node.name in names
            )
            assigned_constants.update(
                name
                for statement in tree.body
                for name in _assigned_names(statement)
                if name == "CANONICAL_ACCOUNTING_FIELDS"
            )
        assert not found_classes
        assert not assigned_constants


class TestNeverRenderZeroForMissing:
    def test_observed_values_render_as_digits(self) -> None:
        assert aggregation.render_measure(_obs(0)) == "0"
        assert aggregation.render_measure(_obs(1234)) == "1234"

    @pytest.mark.parametrize(
        "measure", [_unav(), _unk(), _na()], ids=["unavailable", "unknown", "not-applicable"]
    )
    def test_non_observed_states_render_their_state_without_formatting(
        self, measure: aggregation.TokenMeasure
    ) -> None:
        def fail_if_formatted(value: int) -> str:
            pytest.fail("a missing measure must not be formatted as a number")

        rendered = aggregation.render_measure(measure, fail_if_formatted)
        assert rendered == measure.state.value
        assert rendered != "0"

    def test_unavailable_aggregate_never_renders_as_zero(self) -> None:
        record = _rec("codex", "codex", cache_write_tokens=_unav())
        result = aggregation.aggregate_measures([record] * 3, ["cache_write_tokens"])
        measure = result.fields["cache_write_tokens"].value
        assert measure == aggregation.TokenMeasure.unavailable()
        assert aggregation.render_measure(measure) == "unavailable"

    def test_missing_field_aggregates_to_unknown(self) -> None:
        record = _rec("claude-code", "anthropic", input_tokens=_obs(5))
        field = aggregation.aggregate_measures([record], ["output_tokens"]).fields["output_tokens"]
        assert field.value.state is _State.UNKNOWN
        assert field.state_counts[_State.UNKNOWN] == 1

    @pytest.mark.parametrize(
        "overrides, match",
        [
            (
                {"state": _State.UNAVAILABLE, "value": 0.0},
                "value must be set exactly when state is measured or measured_zero",
            ),
            (
                {"state": _State.MEASURED_ZERO, "value": 1.0},
                "measured_zero ratio must have value 0.0",
            ),
            ({"value": 0.0}, "measured ratio must have value > 0"),
            (
                {"unknown_runs": 1},
                "observed ratio requires sample_size > 0, unknown_runs == 0 "
                "and denominator_total > 0",
            ),
            (
                {"sample_size": 0},
                "observed ratio requires sample_size > 0, unknown_runs == 0 "
                "and denominator_total > 0",
            ),
            (
                {"denominator_total": 0},
                "observed ratio requires sample_size > 0, unknown_runs == 0 "
                "and denominator_total > 0",
            ),
            ({"numerator_total": -1}, "numerator_total must be a non-negative int"),
            ({"sample_size": True}, "sample_size must be a non-negative int"),
            ({"state": _State.UNKNOWN, "value": None}, "unknown ratio requires unknown_runs > 0"),
            ({"numerator_field": ""}, "numerator_field must be a non-empty str"),
        ],
        ids=[
            "value-presence",
            "measured-zero-value",
            "measured-positive-value",
            "unknown-runs",
            "sample-size",
            "denominator-total",
            "nonnegative-total",
            "bool-is-not-int",
            "unknown-state-count",
            "field-name",
        ],
    )
    def test_ratio_rejects_each_invalid_contract(
        self, overrides: dict[str, object], match: str
    ) -> None:
        baseline = aggregation.MeasureRatio(
            scope=aggregation.MeasureScope(
                frozenset({aggregation.SourcePair("claude-code", "anthropic")})
            ),
            numerator_field="cache_read_tokens",
            denominator_field="output_tokens",
            numerator_total=10,
            denominator_total=2,
            sample_size=1,
            excluded_runs=0,
            unknown_runs=0,
            state=_State.MEASURED,
            value=5.0,
        )
        with pytest.raises(ValueError, match=match):
            dataclasses.replace(baseline, **overrides)


class TestStrictTotals:
    @pytest.mark.parametrize(
        "values, expected, reporting_runs, measured_count, zero_count",
        [
            ([_obs(5), _obs(0), _obs(7)], _obs(12), 3, 2, 1),
            ([_obs(0), _obs(0)], _obs(0), 2, 0, 2),
            ([_obs(5), _unav()], _unk(), 1, 1, 0),
            ([_na(), _na()], _na(), 0, 0, 0),
        ],
        ids=["mixed-observed", "all-zero", "unavailable-makes-unknown", "all-not-applicable"],
    )
    def test_nary_reductions_and_reporting_counts(
        self,
        values: list[aggregation.TokenMeasure],
        expected: aggregation.TokenMeasure,
        reporting_runs: int,
        measured_count: int,
        zero_count: int,
    ) -> None:
        records = [_rec(input_tokens=value) for value in values]
        field = aggregation.aggregate_measures(records, ["input_tokens"]).fields["input_tokens"]
        assert field.value == expected
        assert field.reporting_runs == reporting_runs
        assert field.state_counts[_State.MEASURED] == measured_count
        assert field.state_counts[_State.MEASURED_ZERO] == zero_count
        assert set(field.state_counts) == set(_State)

    def test_counts_continue_after_the_fold_becomes_unknown(self) -> None:
        records = [
            _rec(input_tokens=_obs(5)),
            _rec(input_tokens=_unav()),
            _rec(input_tokens=_obs(7)),
            _rec(input_tokens=_unav()),
            _rec(input_tokens=_na()),
        ]
        result = aggregation.aggregate_measures(records, ["input_tokens"])
        field = result.fields["input_tokens"]
        assert field.value == _unk()
        assert result.runs == 5
        assert field.state_counts == {
            _State.MEASURED: 2,
            _State.MEASURED_ZERO: 0,
            _State.UNKNOWN: 0,
            _State.UNAVAILABLE: 2,
            _State.NOT_APPLICABLE: 1,
        }

    def test_peak_context_uses_maximum(self) -> None:
        records = [_rec(peak_context=_obs(value)) for value in (100, 300, 200)]
        field = aggregation.aggregate_measures(records, ["peak_context"]).fields["peak_context"]
        assert field.value == _obs(300)


class TestAvailabilityKeyedOnPair:
    def test_grouping_preserves_each_harness_provider_pair(self) -> None:
        records = [
            _rec("codex", "minimax", cache_write_tokens=_unav()),
            _rec("claude-code", "minimax", cache_write_tokens=_unav()),
            _rec("codex", "codex", cache_write_tokens=_unav()),
            _rec("claude-code", "anthropic", cache_write_tokens=_obs(40)),
        ]
        groups = aggregation.group_by_pair(records)
        expected_pairs = (
            aggregation.SourcePair("claude-code", "anthropic"),
            aggregation.SourcePair("claude-code", "minimax"),
            aggregation.SourcePair("codex", "codex"),
            aggregation.SourcePair("codex", "minimax"),
        )
        assert tuple(groups) == expected_pairs
        assert aggregation.aggregate_measures(
            groups[expected_pairs[0]], ["cache_write_tokens"]
        ).fields["cache_write_tokens"].value == _obs(40)
        assert (
            aggregation.aggregate_measures(groups[expected_pairs[1]], ["cache_write_tokens"])
            .fields["cache_write_tokens"]
            .value
            == _unav()
        )
        assert expected_pairs[1] != expected_pairs[3]
        assert expected_pairs[1].provider == expected_pairs[3].provider
        assert expected_pairs[1].harness != expected_pairs[3].harness

    @pytest.mark.parametrize(
        "harness, provider",
        [("claude-code", ""), ("", "anthropic")],
        ids=["empty-provider", "empty-harness"],
    )
    def test_source_pair_requires_both_names(self, harness: str, provider: str) -> None:
        with pytest.raises(ValueError, match="must be a non-empty str"):
            aggregation.SourcePair(harness, provider)

    def test_source_pair_label(self) -> None:
        assert aggregation.SourcePair("claude-code", "anthropic").label == "claude-code/anthropic"


class TestNormalizationGate:
    def test_token_class_fields_cannot_pool_without_normalization(self) -> None:
        records = [
            _rec(
                "claude-code",
                "anthropic",
                input_tokens=_obs(4),
                cache_read_tokens=_obs(10),
                output_tokens=_obs(2),
            ),
            _rec(
                "claude-code",
                "minimax",
                input_tokens=_obs(6),
                cache_read_tokens=_obs(20),
                output_tokens=_obs(4),
            ),
        ]
        assert issubclass(aggregation.UnnormalizedPoolError, ValueError)
        with pytest.raises(
            aggregation.UnnormalizedPoolError, match="named, versioned normalization"
        ):
            aggregation.aggregate_measures(records, ["input_tokens"])
        with pytest.raises(
            aggregation.UnnormalizedPoolError, match="named, versioned normalization"
        ):
            aggregation.measure_ratio(records, "cache_read_tokens", "output_tokens")

    def test_non_token_fields_pool_without_normalization(self) -> None:
        records = [
            _rec("claude-code", "anthropic", turn_count=_obs(2)),
            _rec("claude-code", "minimax", turn_count=_obs(3)),
        ]
        result = aggregation.aggregate_measures(records, ["turn_count"])
        assert result.fields["turn_count"].value == _obs(5)
        assert result.scope.normalization is None
        assert result.scope.pairs == {record.pair for record in records}

    def test_normalization_receives_each_record_pair_and_is_recorded(self) -> None:
        records = [
            _rec("claude-code", "anthropic", input_tokens=_obs(4)),
            _rec("claude-code", "minimax", input_tokens=_obs(6)),
        ]
        seen: list[aggregation.SourcePair] = []

        def record_pair(
            pair: aggregation.SourcePair, measures: Mapping[str, aggregation.TokenMeasure]
        ) -> dict[str, aggregation.TokenMeasure]:
            seen.append(pair)
            return dict(measures)

        normalization = aggregation.TokenNormalization("test-identity", 1, record_pair)
        result = aggregation.aggregate_measures(
            records, ["input_tokens"], normalization=normalization
        )
        assert result.fields["input_tokens"].value == _obs(10)
        assert result.scope.normalization == "test-identity@v1"
        assert seen == [record.pair for record in records]

    def test_single_pair_still_records_requested_normalization(self) -> None:
        record = _rec("claude-code", "anthropic", input_tokens=_obs(4))
        result = aggregation.aggregate_measures(
            [record], ["input_tokens"], normalization=_identity()
        )
        assert result.scope.normalization == "test-identity@v1"

    @pytest.mark.parametrize(
        "name, version",
        [("", 1), ("test", 0), ("test", True), ("test", "1")],
        ids=["empty-name", "zero-version", "bool-version", "string-version"],
    )
    def test_normalization_requires_nonempty_name_and_positive_integer_version(
        self, name: str, version: object
    ) -> None:
        match = "normalization name" if not name else "normalization version"
        with pytest.raises(ValueError, match=match):
            aggregation.TokenNormalization(
                name, cast(int, version), lambda pair, measures: dict(measures)
            )

    def test_normalization_must_preserve_measure_keys(self) -> None:
        record = _rec("claude-code", "anthropic", input_tokens=_obs(4))
        normalization = aggregation.TokenNormalization("drop-all", 1, lambda pair, measures: {})
        with pytest.raises(ValueError, match="preserve measure keys"):
            aggregation.aggregate_measures([record], ["input_tokens"], normalization=normalization)

    def test_normalization_must_return_measures(self) -> None:
        record = _rec(input_tokens=_obs(4))
        normalization = aggregation.TokenNormalization(
            "bad-value",
            1,
            lambda pair, measures: {"input_tokens": cast(aggregation.TokenMeasure, 4)},
        )
        with pytest.raises(TypeError, match="normalization values must be TokenMeasure"):
            aggregation.aggregate_measures([record], ["input_tokens"], normalization=normalization)

    def test_token_class_fields_name_all_accounting_fields_and_peak_context(self) -> None:
        assert aggregation.TOKEN_CLASS_FIELDS == frozenset(
            aggregation.CANONICAL_ACCOUNTING_FIELDS
        ) | {"peak_context"}


class TestRatioCarriesSampleSize:
    def test_ratio_equality_and_rendering_include_sample_size(self) -> None:
        seven = [
            _rec("claude-code", "anthropic", cache_read_tokens=_obs(10), output_tokens=_obs(2))
            for _ in range(7)
        ]
        many = [
            _rec("claude-code", "anthropic", cache_read_tokens=_obs(10), output_tokens=_obs(2))
            for _ in range(278)
        ]
        seven_ratio = aggregation.measure_ratio(seven, "cache_read_tokens", "output_tokens")
        many_ratio = aggregation.measure_ratio(many, "cache_read_tokens", "output_tokens")
        assert seven_ratio.sample_size == 7
        assert seven_ratio.value == 5.0
        assert aggregation.render_ratio(seven_ratio) == "5.0 (n=7)"
        assert many_ratio.value == 5.0
        assert many_ratio != seven_ratio
        assert aggregation.render_ratio(many_ratio) == "5.0 (n=278)"

    def test_non_numeric_ratio_rendering_still_shows_sample_size(self) -> None:
        record = _rec("claude-code", "anthropic", cache_read_tokens=_unav(), output_tokens=_unav())
        ratio = aggregation.measure_ratio([record], "cache_read_tokens", "output_tokens")
        assert aggregation.render_ratio(ratio).startswith("unavailable (n=0")


class TestExcludeCannotProduceFromDenominator:
    @pytest.mark.parametrize("excluded", [_unav(), _na()], ids=["unavailable", "not-applicable"])
    def test_cannot_produce_numerator_runs_are_excluded(
        self, excluded: aggregation.TokenMeasure
    ) -> None:
        records = [
            _rec("claude-code", "anthropic", cache_write_tokens=_obs(30), input_tokens=_obs(10))
            for _ in range(3)
        ] + [
            _rec("claude-code", "anthropic", cache_write_tokens=excluded, input_tokens=_obs(10))
            for _ in range(2)
        ]
        ratio = aggregation.measure_ratio(records, "cache_write_tokens", "input_tokens")
        assert ratio.numerator_field == "cache_write_tokens"
        assert ratio.denominator_field == "input_tokens"
        assert ratio.sample_size == 3
        assert ratio.excluded_runs == 2
        assert ratio.value == 3.0

    def test_proportion_uses_only_producible_pairs(self) -> None:
        flags = [_obs(1), _obs(0), _obs(1), _unav()]
        records = [_rec(cache_write_tokens=flag, input_tokens=_obs(1)) for flag in flags]
        ratio = aggregation.measure_ratio(records, "cache_write_tokens", "input_tokens")
        assert ratio.value == 2 / 3
        assert ratio.excluded_runs == 1

    def test_unavailable_denominator_excludes_unknown_numerator(self) -> None:
        record = _rec(cache_write_tokens=_unk(), input_tokens=_unav())
        ratio = aggregation.measure_ratio([record], "cache_write_tokens", "input_tokens")
        assert ratio.excluded_runs == 1
        assert ratio.unknown_runs == 0

    def test_zero_denominators_are_not_applicable(self) -> None:
        records = [_rec(cache_write_tokens=_obs(30), input_tokens=_obs(0)) for _ in range(2)]
        ratio = aggregation.measure_ratio(records, "cache_write_tokens", "input_tokens")
        assert ratio.state is _State.NOT_APPLICABLE
        assert ratio.value is None
        assert ratio.sample_size == 2

    def test_unavailable_exclusion_outweighs_not_applicable_when_all_excluded(self) -> None:
        not_applicable = [_rec(cache_write_tokens=_na(), input_tokens=_obs(10)) for _ in range(2)]
        na_ratio = aggregation.measure_ratio(not_applicable, "cache_write_tokens", "input_tokens")
        assert na_ratio.state is _State.NOT_APPLICABLE
        assert na_ratio.sample_size == 0
        assert na_ratio.excluded_runs == 2
        mixed = [
            not_applicable[0],
            _rec(cache_write_tokens=_unav(), input_tokens=_obs(10)),
        ]
        ratio = aggregation.measure_ratio(mixed, "cache_write_tokens", "input_tokens")
        assert ratio.state is _State.UNAVAILABLE
        assert aggregation.render_ratio(ratio) == "unavailable (n=0, 2 excluded)"

    def test_one_shot_iterables_match_reusable_records(self) -> None:
        records = [
            _rec(cache_read_tokens=_obs(10), output_tokens=_obs(2)),
            _rec(cache_read_tokens=_obs(20), output_tokens=_obs(4)),
        ]
        assert aggregation.measure_ratio(
            (record for record in records), "cache_read_tokens", "output_tokens"
        ) == aggregation.measure_ratio(records, "cache_read_tokens", "output_tokens")
        assert aggregation.aggregate_measures(
            (record for record in records), ["cache_read_tokens"]
        ) == aggregation.aggregate_measures(records, ["cache_read_tokens"])


class TestUnknownIsNotSilentlyDropped:
    def test_unknown_run_blocks_ratio_and_is_rendered(self) -> None:
        records = [_rec(cache_read_tokens=_obs(10), output_tokens=_obs(2)) for _ in range(4)] + [
            _rec(cache_read_tokens=_obs(10), output_tokens=_unk())
        ]
        ratio = aggregation.measure_ratio(records, "cache_read_tokens", "output_tokens")
        assert ratio.unknown_runs == 1
        assert ratio.sample_size == 4
        assert ratio.state is _State.UNKNOWN
        assert ratio.value is None
        assert aggregation.render_ratio(ratio) == "unknown (n=4, 1 unknown)"

    def test_all_unknown_runs_have_zero_sample_size(self) -> None:
        records = [_rec(cache_read_tokens=_unk(), output_tokens=_obs(10)) for _ in range(2)]
        ratio = aggregation.measure_ratio(records, "cache_read_tokens", "output_tokens")
        assert ratio.state is _State.UNKNOWN
        assert ratio.sample_size == 0

    def test_rendered_ratio_keeps_excluded_run_count(self) -> None:
        records = [_rec(cache_read_tokens=_obs(30), output_tokens=_obs(10)) for _ in range(3)] + [
            _rec(cache_read_tokens=_unav(), output_tokens=_obs(10)) for _ in range(2)
        ]
        ratio = aggregation.measure_ratio(records, "cache_read_tokens", "output_tokens")
        assert aggregation.render_ratio(ratio) == "3.0 (n=3, 2 excluded)"


class TestNonReportingPairAcceptance:
    def test_non_reporting_pair_is_unavailable_and_excluded(self) -> None:
        anthropic = [
            _rec("claude-code", "anthropic", cache_write_tokens=_obs(20), input_tokens=_obs(10))
            for _ in range(4)
        ]
        codex = [
            _rec("codex", "codex", cache_write_tokens=_unav(), input_tokens=_obs(10))
            for _ in range(3)
        ]
        groups = aggregation.group_by_pair(anthropic + codex)
        unavailable = aggregation.aggregate_measures(
            groups[aggregation.SourcePair("codex", "codex")], ["cache_write_tokens"]
        )
        unavailable_measure = unavailable.fields["cache_write_tokens"].value
        assert unavailable_measure.state is _State.UNAVAILABLE
        assert aggregation.render_measure(unavailable_measure) == "unavailable"

        pooled = aggregation.measure_ratio(
            anthropic + codex,
            "cache_write_tokens",
            "input_tokens",
            normalization=_identity(),
        )
        anthropic_only = aggregation.measure_ratio(anthropic, "cache_write_tokens", "input_tokens")
        assert pooled.sample_size == 4
        assert pooled.excluded_runs == 3
        assert pooled.value == 2.0
        assert pooled.value == anthropic_only.value

        single_pair = aggregation.measure_ratio(codex, "cache_write_tokens", "input_tokens")
        assert single_pair.state is _State.UNAVAILABLE
        assert single_pair.value is None
        assert single_pair.sample_size == 0
        assert single_pair.excluded_runs == 3


class TestAggregateInputContract:
    def test_aggregators_reject_empty_inputs(self) -> None:
        with pytest.raises(ValueError, match="empty record set"):
            aggregation.aggregate_measures([], ["input_tokens"])
        with pytest.raises(ValueError, match="empty record set"):
            aggregation.measure_ratio([], "input_tokens", "output_tokens")

    def test_records_reject_non_measure_values(self) -> None:
        with pytest.raises(TypeError, match="measure values must be TokenMeasure"):
            aggregation.MeasureRecord(
                aggregation.SourcePair("a", "b"),
                {"input_tokens": cast(aggregation.TokenMeasure, 5)},
            )

    def test_grouping_empty_input_returns_empty_mapping(self) -> None:
        assert aggregation.group_by_pair([]) == {}

    def test_duplicate_fields_are_removed_without_reordering(self) -> None:
        record = _rec(x=_obs(2), y=_obs(3))
        result = aggregation.aggregate_measures([record], ["x", "x", "y"])
        assert tuple(result.fields) == ("x", "y")

    def test_record_copies_caller_measure_mapping(self) -> None:
        measures = {"input_tokens": _obs(4)}
        record = aggregation.MeasureRecord(aggregation.SourcePair("a", "b"), measures)
        measures["input_tokens"] = _obs(9)
        assert record.measures["input_tokens"] == _obs(4)

    def test_scope_rejects_empty_pairs(self) -> None:
        with pytest.raises(ValueError, match="scope pairs must not be empty"):
            aggregation.MeasureScope(frozenset())
