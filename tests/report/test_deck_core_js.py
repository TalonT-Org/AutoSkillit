"""Browser-free contract tests for the observability deck's client logic."""

import json
from typing import Any

import pytest

from autoskillit.report.deck._registry import ChipState

pytestmark = [pytest.mark.small]

COHORT_KEYS = ["harness", "provider", "level", "window"]


def _route(**params: list[str]) -> dict[str, object]:
    return {"view": "cohort", "entity": None, "params": params}


def test_routes_encode_decode_and_reject_bad_query_values(deck_js: Any) -> None:
    route = {
        "view": "skill",
        "entity": "a/b c",
        "params": {"harness": ["x,y", "%"], "sort": ["runs:desc"]},
    }
    encoded = deck_js.call("DeckCore.encodeRoute", route, COHORT_KEYS)
    assert encoded == "#/skill/a%2Fb%20c?harness=x%2Cy,%25&sort=runs%3Adesc"
    assert deck_js.call("DeckCore.decodeRoute", encoded, "cohort") == {
        "view": "skill",
        "entity": "a/b c",
        "params": {"harness": ["x,y", "%"], "sort": ["runs:desc"]},
    }

    for empty_hash in ("", "#", "#/"):
        assert deck_js.call("DeckCore.decodeRoute", empty_hash, "cohort") == {
            "view": "cohort",
            "entity": None,
            "params": {},
        }
    assert deck_js.call("DeckCore.decodeRoute", "#/cohort?harness=%E0&provider=a", "cohort") == {
        "view": "cohort",
        "entity": None,
        "params": {"provider": ["a"]},
    }
    assert (
        deck_js.call(
            "DeckCore.encodeRoute",
            {"view": "cohort", "entity": None, "params": {"harness": [], "provider": None}},
            COHORT_KEYS,
        )
        == "#/cohort"
    )


def test_href_for_keeps_cohort_params_and_applies_target_changes(deck_js: Any) -> None:
    route = {
        "view": "cohort",
        "entity": None,
        "params": {
            "harness": ["codex"],
            "window": ["28d"],
            "sort": ["runs:desc"],
        },
    }
    assert (
        deck_js.call("DeckCore.hrefFor", route, {"view": "spend"}, COHORT_KEYS)
        == "#/spend?harness=codex&window=28d"
    )
    assert (
        deck_js.call(
            "DeckCore.hrefFor",
            route,
            {"view": "spend", "params": {"window": None}},
            COHORT_KEYS,
        )
        == "#/spend?harness=codex"
    )


def test_facet_selection_drops_struck_keys_and_preserves_one_live_key(
    deck_js: Any, deck_model: dict[str, Any]
) -> None:
    chips = deck_model["chips"]["cohort"]
    harness = chips["harness"]
    levels = chips["level"]

    assert deck_js.call("DeckCore.effectiveSelection", harness, None) == {
        "keys": ["claude-code", "codex"],
        "dropped": [],
        "widened": False,
    }
    mixed = deck_js.call("DeckCore.effectiveSelection", levels, ["unrecorded", "L0"])
    assert mixed["keys"] == ["unrecorded"]
    assert mixed["dropped"] == ["L0"]
    assert mixed["widened"] is False

    widened = deck_js.call("DeckCore.effectiveSelection", levels, ["L0"])
    assert widened["keys"] == ["unrecorded"]
    assert widened["widened"] is True

    selected = deck_js.call("DeckCore.toggleSelection", harness, None, "codex")
    assert selected == ["claude-code"]
    assert deck_js.call("DeckCore.toggleSelection", harness, selected, "claude-code") == [
        "claude-code"
    ]
    assert deck_js.call("DeckCore.toggleSelection", harness, selected, "codex") is None


def test_window_selection_falls_back_to_all_for_absent_window(
    deck_js: Any, deck_model: dict[str, Any]
) -> None:
    windows = deck_model["chips"]["cohort"]["window"]
    assert deck_js.call("DeckCore.windowSelection", windows, None)["chip"]["key"] == "all"
    absent = deck_js.call("DeckCore.windowSelection", windows, ["28d"])
    assert absent["chip"]["key"] == "all"
    assert absent["dropped"] == ["28d"]
    assert deck_js.call("DeckCore.windowSelection", windows, ["7d"])["chip"]["key"] == "7d"


def test_filter_rows_applies_facets_windows_and_untimed_count(
    deck_js: Any, deck_model: dict[str, Any]
) -> None:
    model = deck_model
    rows = model["tables"]["sessions"]
    chips = model["chips"]["cohort"]

    unrecorded = deck_js.call(
        "DeckCore.filterRows", model, rows, chips, _route(level=["unrecorded"])
    )
    assert sorted(row["key"] for row in unrecorded["rows"]) == ["s1", "s2", "s3", "s4"]
    assert unrecorded["untimed"] == 0

    codex = deck_js.call("DeckCore.filterRows", model, rows, chips, _route(harness=["codex"]))
    assert [row["key"] for row in codex["rows"]] == ["s4"]
    assert codex["untimed"] == 0

    last_week = deck_js.call("DeckCore.filterRows", model, rows, chips, _route(window=["7d"]))
    assert sorted(row["key"] for row in last_week["rows"]) == ["s1", "s2"]
    assert last_week["untimed"] == 1

    no_timed_codex = deck_js.call(
        "DeckCore.filterRows", model, rows, chips, _route(harness=["codex"], window=["7d"])
    )
    assert no_timed_codex["rows"] == []
    assert no_timed_codex["untimed"] == 1

    absent_window = deck_js.call("DeckCore.filterRows", model, rows, chips, _route(window=["28d"]))
    assert sorted(row["key"] for row in absent_window["rows"]) == [
        "s1",
        "s2",
        "s3",
        "s4",
    ]


def test_population_sentence_explains_the_effective_population(
    deck_js: Any, deck_model: dict[str, Any]
) -> None:
    model = deck_model
    chips = model["chips"]["cohort"]
    rows = model["tables"]["sessions"]

    all_route = _route()
    all_result = deck_js.call("DeckCore.filterRows", model, rows, chips, all_route)
    sentence = deck_js.call("DeckCore.populationSentence", model, chips, all_route, all_result)
    assert sentence == {
        "headline": "4 runs",
        "detail": (
            "harness claude-code + codex (all) · provider anthropic + codex (all) · "
            "level unrecorded (all) · window all history "
            "(2026-09-25 → 2026-10-04)"
        ),
        "notes": [],
    }

    codex_route = _route(harness=["codex"])
    codex_result = deck_js.call("DeckCore.filterRows", model, rows, chips, codex_route)
    codex_sentence = deck_js.call(
        "DeckCore.populationSentence", model, chips, codex_route, codex_result
    )
    assert codex_sentence["headline"] == "1 run"
    assert codex_sentence["detail"].startswith("harness codex · ")

    week_route = _route(window=["7d"])
    week_result = deck_js.call("DeckCore.filterRows", model, rows, chips, week_route)
    week_sentence = deck_js.call(
        "DeckCore.populationSentence", model, chips, week_route, week_result
    )
    assert week_sentence["detail"].endswith("window last 7 days (2026-09-27 → 2026-10-04)")
    assert "1 run without a timestamp falls outside every window" in week_sentence["notes"]

    struck_route = _route(level=["L0"])
    struck_result = deck_js.call("DeckCore.filterRows", model, rows, chips, struck_route)
    struck_sentence = deck_js.call(
        "DeckCore.populationSentence", model, chips, struck_route, struck_result
    )
    assert (
        "L0 is not selectable on this view — L0 leaf agents write no session row; "
        "they exist only as subagent transcripts"
    ) in struck_sentence["notes"]
    assert (
        "no selected level is selectable here — showing every selectable level"
        in struck_sentence["notes"]
    )

    unknown_route = _route(harness=["nope"])
    unknown_result = deck_js.call("DeckCore.filterRows", model, rows, chips, unknown_route)
    unknown_sentence = deck_js.call(
        "DeckCore.populationSentence", model, chips, unknown_route, unknown_result
    )
    assert "nope does not appear in this index" in unknown_sentence["notes"]
    assert (
        "no selected harness is selectable here — showing every selectable harness"
        in unknown_sentence["notes"]
    )

    old_window_route = _route(window=["28d"])
    old_window_result = deck_js.call("DeckCore.filterRows", model, rows, chips, old_window_route)
    old_window_sentence = deck_js.call(
        "DeckCore.populationSentence", model, chips, old_window_route, old_window_result
    )
    assert (
        "28 days is not selectable on this view — index history begins 2026-09-25 — "
        "9 days retained (#4621)"
    ) in old_window_sentence["notes"]
    assert old_window_sentence["detail"].endswith("window all history (2026-09-25 → 2026-10-04)")


def test_chip_presentation_covers_live_struck_absent_and_issue_reasons(
    deck_js: Any, deck_model: dict[str, Any]
) -> None:
    cohort = deck_model["chips"]["cohort"]
    live = next(chip for chip in cohort["harness"] if chip["key"] == "codex")
    assert deck_js.call("DeckCore.chipPresentation", live, True) == {
        "text": live["label"],
        "className": "chip",
        "disabled": False,
        "pressed": True,
        "reason": None,
    }

    struck = next(chip for chip in cohort["level"] if chip["key"] == "L0")
    struck_view = deck_js.call("DeckCore.chipPresentation", struck, True)
    assert struck_view == {
        "text": struck["label"],
        "className": "chip chip--struck",
        "disabled": True,
        "pressed": False,
        "reason": struck["reason"],
    }

    absent_level = next(chip for chip in cohort["level"] if chip["key"] == "L1")
    absent_view = deck_js.call("DeckCore.chipPresentation", absent_level, False)
    assert absent_view["text"] == absent_level["label"] + " ✕"
    assert absent_view["className"] == "chip chip--absent"
    assert absent_view["disabled"] is True
    assert absent_view["pressed"] is False
    assert absent_view["reason"].endswith("(#4622)")

    absent_window = next(chip for chip in cohort["window"] if chip["key"] == "28d")
    assert deck_js.call("DeckCore.chipPresentation", absent_window, False)["reason"].endswith(
        "(#4621)"
    )
    custom_strike = {
        "key": "future",
        "label": "future",
        "state": ChipState.STRUCK.value,
        "reason": "A custom strike",
        "issue": 9999,
    }
    assert deck_js.call("DeckCore.chipPresentation", custom_strike, False)["reason"] == (
        "A custom strike (#9999)"
    )


def test_availability_presentation_uses_the_payload_vocabulary(
    deck_js: Any, deck_model: dict[str, Any]
) -> None:
    vocabulary = deck_model["availability"]
    measured = deck_js.call(
        "DeckCore.availabilityPresentation",
        {"state": "measured", "value": 1234567},
        vocabulary,
    )
    assert measured == {
        "text": "1,234,567",
        "className": "av av--measured",
        "title": "Reported by the producer",
    }
    assert (
        deck_js.call(
            "DeckCore.availabilityPresentation",
            {"state": "measured_zero", "value": 0},
            vocabulary,
        )["text"]
        == "0"
    )

    for state in ("unavailable", "unknown", "not_applicable"):
        entry = next(item for item in vocabulary if item["state"] == state)
        assert deck_js.call(
            "DeckCore.availabilityPresentation", {"state": state, "value": None}, vocabulary
        ) == {
            "text": entry["label"],
            "className": f"av av--{state}",
            "title": entry["description"],
        }

    with pytest.raises(Exception, match="unknown availability state: invalid"):
        deck_js.call(
            "DeckCore.availabilityPresentation",
            {"state": "invalid", "value": None},
            vocabulary,
        )


def test_sort_rows_orders_numbers_strings_nulls_and_stable_ties(deck_js: Any) -> None:
    numeric = [
        {"value": 2, "id": "two-first"},
        {"value": None, "id": "null"},
        {"id": "undefined"},
        {"value": 1, "id": "one"},
        {"value": 2, "id": "two-second"},
    ]
    assert [row["id"] for row in deck_js.call("DeckCore.sortRows", numeric, "value", "asc")] == [
        "one",
        "two-first",
        "two-second",
        "null",
        "undefined",
    ]
    assert [row["id"] for row in deck_js.call("DeckCore.sortRows", numeric, "value", "desc")] == [
        "two-first",
        "two-second",
        "one",
        "null",
        "undefined",
    ]

    strings = [
        {"value": "b", "id": "b"},
        {"value": "a", "id": "a-first"},
        {"value": "A", "id": "upper"},
        {"value": "a", "id": "a-second"},
    ]
    assert [row["id"] for row in deck_js.call("DeckCore.sortRows", strings, "value", "asc")] == [
        "upper",
        "a-first",
        "a-second",
        "b",
    ]
    assert [row["id"] for row in deck_js.call("DeckCore.sortRows", strings, "value", "desc")] == [
        "b",
        "a-first",
        "a-second",
        "upper",
    ]

    fallback = {"key": "runs", "dir": "asc"}
    assert deck_js.call("DeckCore.parseSort", "runs:desc", ["runs"], fallback) == {
        "key": "runs",
        "dir": "desc",
    }
    assert deck_js.call("DeckCore.parseSort", "bogus", ["runs"], fallback) == fallback
    assert (
        json.loads(
            deck_js.eval(
                'JSON.stringify(DeckCore.parseSort(undefined, ["runs"], {key:"runs", dir:"asc"}))'
            )
        )
        == fallback
    )


def test_bar_layout_scales_values_and_handles_zero_and_null(
    deck_js: Any,
) -> None:
    options = {"width": 640, "labelWidth": 200, "valueWidth": 80, "rowHeight": 18, "gap": 6}
    items = [
        {"label": "six", "value": 6, "href": "#/six"},
        {"label": "three", "value": 3, "href": "#/three"},
        {"label": "unknown", "value": None, "href": "#/unknown"},
    ]
    assert deck_js.call("DeckCore.barLayout", items, options) == [
        {"label": "six", "value": 6, "href": "#/six", "x": 200, "y": 0, "w": 360, "h": 18},
        {"label": "three", "value": 3, "href": "#/three", "x": 200, "y": 24, "w": 180, "h": 18},
        {
            "label": "unknown",
            "value": None,
            "href": "#/unknown",
            "x": 200,
            "y": 48,
            "w": None,
            "h": 18,
        },
    ]
    zeros = deck_js.call(
        "DeckCore.barLayout",
        [
            {"label": "zero-a", "value": 0, "href": "#/zero-a"},
            {"label": "zero-b", "value": 0, "href": "#/zero-b"},
        ],
        options,
    )
    assert [item["w"] for item in zeros] == [0, 0]


def test_summarize_pairs_groups_skills_and_time_bounds(
    deck_js: Any, deck_model: dict[str, Any]
) -> None:
    rows = deck_model["tables"]["sessions"]
    generated_at_ms = deck_model["generated_at_ms"]
    day_ms = 86_400_000
    assert deck_js.call("DeckCore.summarizePairs", rows) == [
        {
            "harness": "claude-code",
            "provider": "anthropic",
            "runs": 3,
            "skills": 2,
            "first_ms": generated_at_ms - 9 * day_ms,
            "last_ms": generated_at_ms - day_ms,
        },
        {
            "harness": "codex",
            "provider": "codex",
            "runs": 1,
            "skills": 0,
            "first_ms": None,
            "last_ms": None,
        },
    ]


def test_formatters_and_chip_state_parity(deck_js: Any, deck_model: dict[str, Any]) -> None:
    assert deck_js.call("DeckCore.formatCount", 1234567) == "1,234,567"
    assert deck_js.call("DeckCore.formatDate", deck_model["generated_at_ms"]) == "2026-10-04"
    states = json.loads(deck_js.eval("JSON.stringify(Object.values(DeckCore.CHIP_STATES))"))
    assert sorted(states) == sorted(state.value for state in ChipState)


def test_core_js_has_no_browser_global_dependency(deck_js: Any) -> None:
    assert deck_js.eval("typeof window + typeof document + typeof location") == (
        "undefinedundefinedundefined"
    )
