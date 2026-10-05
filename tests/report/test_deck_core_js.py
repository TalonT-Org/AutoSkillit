"""Browser-free contract tests for the observability deck's client logic."""

import json
from typing import Any

import pytest

from autoskillit.report.deck._registry import ChipState

pytestmark = [pytest.mark.small]

COHORT_KEYS = ["harness", "provider", "level", "window"]
ALL_POPULATION_DETAIL = (
    "harness claude-code + codex (all) · provider anthropic + codex (all) · "
    "level unrecorded (all) · window all history (2026-09-25 → 2026-10-04)"
)


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


@pytest.mark.parametrize(
    ("params", "expected_keys", "untimed"),
    [
        pytest.param({"level": ["unrecorded"]}, ["s1", "s2", "s3", "s4"], 0, id="unrecorded"),
        pytest.param({"harness": ["codex"]}, ["s4"], 0, id="codex"),
        pytest.param({"window": ["7d"]}, ["s1", "s2"], 1, id="last-week"),
        pytest.param({"harness": ["codex"], "window": ["7d"]}, [], 1, id="untimed-codex"),
        pytest.param({"window": ["28d"]}, ["s1", "s2", "s3", "s4"], 0, id="absent-window"),
    ],
)
def test_filter_rows_applies_facets_windows_and_untimed_count(
    deck_js: Any,
    deck_model: dict[str, Any],
    params: dict[str, list[str]],
    expected_keys: list[str],
    untimed: int,
) -> None:
    result = deck_js.call(
        "DeckCore.filterRows",
        deck_model,
        deck_model["tables"]["sessions"],
        deck_model["chips"]["cohort"],
        _route(**params),
    )
    assert sorted(row["key"] for row in result["rows"]) == expected_keys
    assert result["untimed"] == untimed


@pytest.mark.parametrize(
    ("route", "headline", "detail", "notes"),
    [
        pytest.param(_route(), "4 runs", ALL_POPULATION_DETAIL, [], id="all"),
        pytest.param(
            _route(harness=["codex"]),
            "1 run",
            "harness codex · provider anthropic + codex (all) · "
            "level unrecorded (all) · window all history (2026-09-25 → 2026-10-04)",
            [],
            id="codex",
        ),
        pytest.param(
            _route(window=["7d"]),
            "2 runs",
            "harness claude-code + codex (all) · provider anthropic + codex (all) · "
            "level unrecorded (all) · window last 7 days (2026-09-27 → 2026-10-04)",
            ["1 run without a timestamp falls outside every window"],
            id="last-week",
        ),
        pytest.param(
            _route(level=["L0"]),
            "4 runs",
            ALL_POPULATION_DETAIL,
            [
                "L0 is not selectable on this view — L0 leaf agents write no session row; "
                "they exist only as subagent transcripts",
                "no selected level is selectable here — showing every selectable level",
            ],
            id="struck-level",
        ),
        pytest.param(
            _route(harness=["nope"]),
            "4 runs",
            ALL_POPULATION_DETAIL,
            [
                "nope does not appear in this index",
                "no selected harness is selectable here — showing every selectable harness",
            ],
            id="unknown-harness",
        ),
        pytest.param(
            _route(window=["28d"]),
            "4 runs",
            ALL_POPULATION_DETAIL,
            [
                "28 days is not selectable on this view — index history begins 2026-09-25 — "
                "9 days retained (#4621)"
            ],
            id="absent-window",
        ),
    ],
)
def test_population_sentence_explains_the_effective_population(
    deck_js: Any,
    deck_model: dict[str, Any],
    route: dict[str, object],
    headline: str,
    detail: str,
    notes: list[str],
) -> None:
    chips = deck_model["chips"]["cohort"]
    result = deck_js.call(
        "DeckCore.filterRows", deck_model, deck_model["tables"]["sessions"], chips, route
    )
    assert deck_js.call("DeckCore.populationSentence", deck_model, chips, route, result) == {
        "headline": headline,
        "detail": detail,
        "notes": notes,
    }


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


@pytest.mark.parametrize(
    ("state", "value", "text"),
    [
        pytest.param("measured", 1234567, "1,234,567", id="measured"),
        pytest.param("measured_zero", 0, "0", id="measured-zero"),
        pytest.param("unavailable", None, None, id="unavailable"),
        pytest.param("unknown", None, None, id="unknown"),
        pytest.param("not_applicable", None, None, id="not-applicable"),
    ],
)
def test_availability_presentation_uses_the_payload_vocabulary(
    deck_js: Any, deck_model: dict[str, Any], state: str, value: int | None, text: str | None
) -> None:
    vocabulary = deck_model["availability"]
    entry = next(item for item in vocabulary if item["state"] == state)
    assert deck_js.call(
        "DeckCore.availabilityPresentation", {"state": state, "value": value}, vocabulary
    ) == {
        "text": entry["label"] if text is None else text,
        "className": f"av av--{state}",
        "title": entry["description"],
    }


def test_availability_presentation_rejects_unknown_states(
    deck_js: Any, deck_model: dict[str, Any]
) -> None:
    with pytest.raises(Exception, match="unknown availability state: invalid"):
        deck_js.call(
            "DeckCore.availabilityPresentation",
            {"state": "invalid", "value": None},
            deck_model["availability"],
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


@pytest.mark.parametrize(
    ("token", "direction"),
    [
        pytest.param('"runs:desc"', "desc", id="valid"),
        pytest.param('"bogus"', "asc", id="invalid"),
        pytest.param("undefined", "asc", id="missing"),
    ],
)
def test_parse_sort_uses_valid_tokens_or_falls_back(
    deck_js: Any, token: str, direction: str
) -> None:
    assert json.loads(
        deck_js.eval(
            f'JSON.stringify(DeckCore.parseSort({token}, ["runs"], {{key:"runs", dir:"asc"}}))'
        )
    ) == {"key": "runs", "dir": direction}


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
    assert deck_js.eval("typeof window") == "undefined"
    assert deck_js.eval("typeof document") == "undefined"
    assert deck_js.eval("typeof location") == "undefined"
