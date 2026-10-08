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


def test_href_for_preserves_exact_session_selection(deck_js: Any) -> None:
    route = {
        "view": "context",
        "entity": None,
        "params": {
            "harness": ["codex"],
            "provider": ["openai"],
            "level": ["L2"],
            "window": ["28d"],
            "session": ["owner-key-1"],
        },
    }

    href = deck_js.call("DeckCore.hrefFor", route, {"view": "gaps"}, COHORT_KEYS)

    assert href == ("#/gaps?harness=codex&provider=openai&level=L2&window=28d&session=owner-key-1")
    assert deck_js.call("DeckCore.hrefFor", route, {"view": "errors"}, COHORT_KEYS) == (
        "#/errors?harness=codex&provider=openai&level=L2&window=28d"
    )


def test_href_for_keeps_explicit_identity_filters_across_evidence_views(deck_js: Any) -> None:
    route = {
        "view": "context",
        "entity": None,
        "params": {
            "harness": ["codex"],
            "window": ["7d"],
            "skill": ["demo"],
            "recipe": ["recipe-demo"],
            "step": ["run"],
            "model": ["gpt-test"],
            "session": ["owner-key-1"],
        },
    }

    href = deck_js.call("DeckCore.hrefFor", route, {"view": "parity"}, COHORT_KEYS)
    decoded = deck_js.call("DeckCore.decodeRoute", href, "cohort")

    assert decoded["params"] == {
        "harness": ["codex"],
        "window": ["7d"],
        "skill": ["demo"],
        "recipe": ["recipe-demo"],
        "step": ["run"],
        "model": ["gpt-test"],
        "session": ["owner-key-1"],
    }
    assert (
        deck_js.call(
            "DeckCore.hrefFor",
            route,
            {"view": "spend", "params": {"window": None}},
            COHORT_KEYS,
        )
        == "#/spend?harness=codex"
    )


def test_facet_selection_drops_stale_keys_without_widening_scope(
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

    stale = deck_js.call("DeckCore.effectiveSelection", levels, ["L0"])
    assert stale["keys"] == []
    assert stale["dropped"] == ["L0"]

    selected = deck_js.call("DeckCore.toggleSelection", harness, None, "codex")
    assert selected == ["claude-code"]
    assert deck_js.call("DeckCore.toggleSelection", harness, selected, "claude-code") == [
        "claude-code"
    ]
    assert deck_js.call("DeckCore.toggleSelection", harness, selected, "codex") is None


def test_window_selection_does_not_fall_back_for_absent_window(
    deck_js: Any, deck_model: dict[str, Any]
) -> None:
    windows = deck_model["chips"]["cohort"]["window"]
    assert deck_js.call("DeckCore.windowSelection", windows, None)["chip"]["key"] == "all"
    absent = deck_js.call("DeckCore.windowSelection", windows, ["28d"])
    assert absent["chip"] is None
    assert absent["dropped"] == ["28d"]
    assert deck_js.call("DeckCore.windowSelection", windows, ["7d"])["chip"]["key"] == "7d"


def test_prepared_selection_uses_exact_scope_and_keeps_metric_cells_distinct(
    deck_js: Any,
) -> None:
    skill = {
        "skill": "review",
        "harness": "codex",
        "provider": "openai",
        "measures": {"input_tokens": {"state": "measured", "value": 17}},
    }
    other_skill = {**skill, "harness": "claude-code", "provider": "anthropic"}
    role = {
        "role": "reviewer",
        "provider": "openai",
        "harnesses": [
            {"harness": "codex", "measures": {"input_tokens": {"value": 5}}},
            {"harness": "claude-code", "measures": {"input_tokens": {"value": 9}}},
        ],
    }
    chips = {
        "window": [
            {"key": "7d", "match": "7d", "label": "7d", "state": "live", "days": 7},
            {"key": "all", "match": "all", "label": "all", "state": "live", "days": None},
        ],
        "level": [
            {"key": "L1", "match": "skill", "label": "L1", "state": "live"},
            {"key": "L2", "match": "orchestrator", "label": "L2", "state": "live"},
        ],
        "harness": [
            {"key": "codex", "match": "codex", "label": "codex", "state": "live"},
            {
                "key": "claude-code",
                "match": "claude-code",
                "label": "claude-code",
                "state": "live",
            },
        ],
        "provider": [
            {"key": "openai", "match": "openai", "label": "openai", "state": "live"},
            {
                "key": "anthropic",
                "match": "anthropic",
                "label": "anthropic",
                "state": "live",
            },
        ],
    }
    relationship = {
        "skill": "review",
        "role": "reviewer",
        "harness": "codex",
        "provider": "openai",
    }
    definition = {"state": "available", "description": "Reads code"}
    history = {"first_ms": 1, "last_ms": 2, "untimed": 0}
    prepared = {
        "skills": [
            {"window": "7d", "levels": ["skill"], "rows": [{**skill, "skill": "wrong-level"}]},
            {
                "window": "all",
                "levels": ["orchestrator", "skill"],
                "rows": [{**skill, "skill": "wrong-window"}],
            },
            {
                "window": "7d",
                "levels": ["orchestrator", "skill"],
                "rows": [skill, other_skill],
            },
        ],
        "roles": [
            {
                "window": "7d",
                "levels": ["orchestrator", "skill"],
                "rows": [role],
            }
        ],
        "relationships": [relationship],
        "definitions": {"reviewer": definition},
        "view_chips": {"efficiency": chips},
        "view_histories": {"efficiency": history},
    }
    route = {
        "params": {
            "window": ["7d"],
            "level": ["L1", "L2"],
            "harness": ["codex"],
            "provider": ["openai"],
        }
    }

    selected = deck_js.call("DeckCore.selectPrepared", prepared, "efficiency", chips, route)

    assert selected["skillRows"] == [skill]
    assert selected["roleRows"] == [
        {
            **role,
            "harnesses": [role["harnesses"][0]],
        }
    ]
    assert selected["relationships"] == [relationship]
    assert selected["definitions"] == {"reviewer": definition}
    assert selected["viewHistory"] == history

    route["params"]["window"] = ["missing-window"]
    empty = deck_js.call("DeckCore.selectPrepared", prepared, "efficiency", chips, route)
    assert empty["skillRows"] == empty["roleRows"] == []
    assert empty["relationships"] == [relationship]
    assert empty["definitions"] == {"reviewer": definition}

    route["params"]["window"] = ["7d"]
    prepared["skills"] = [{"window": "7d", "levels": ["skill"], "rows": [skill]}]
    selected = deck_js.call("DeckCore.selectPrepared", prepared, "skill", chips, route)
    assert selected["skillRows"] == [skill]
    route["params"]["level"] = ["L2"]
    selected = deck_js.call("DeckCore.selectPrepared", prepared, "skill", chips, route)
    assert selected["skillRows"] == []
    route["params"]["level"] = []
    selected = deck_js.call("DeckCore.selectPrepared", prepared, "skill", chips, route)
    assert selected["skillRows"] == [skill]


def test_prepared_selection_returns_no_metrics_for_stale_scope(deck_js: Any) -> None:
    chips = {
        "window": [{"key": "all", "match": "all", "label": "all", "state": "live", "days": None}],
        "level": [{"key": "L1", "match": "skill", "label": "L1", "state": "live"}],
        "harness": [{"key": "codex", "match": "codex", "label": "codex", "state": "live"}],
        "provider": [{"key": "openai", "match": "openai", "label": "openai", "state": "live"}],
    }
    prepared = {
        "skills": [{"window": "all", "levels": ["skill"], "rows": [{"skill": "review"}]}],
        "roles": [{"window": "all", "levels": ["skill"], "rows": []}],
        "relationships": [],
        "definitions": {},
        "view_chips": {"skill": chips},
        "view_histories": {"skill": {"first_ms": 1, "last_ms": 1, "untimed": 0}},
    }

    selected = deck_js.call(
        "DeckCore.selectPrepared",
        prepared,
        "skill",
        chips,
        {"params": {"window": ["deleted-window"], "level": ["L2"]}},
    )

    assert selected["skillRows"] == []
    assert selected["roleRows"] == []


def test_new_prepared_view_selects_actual_rows_and_exact_session_block(deck_js: Any) -> None:
    chips = {
        "window": [{"key": "7d", "match": "7d", "label": "7 days", "state": "live", "days": 7}],
        "level": [{"key": "L2", "match": "orchestrator", "label": "L2", "state": "live"}],
        "harness": [{"key": "codex", "match": "codex", "label": "Codex", "state": "live"}],
        "provider": [{"key": "openai", "match": "openai", "label": "OpenAI", "state": "live"}],
    }
    selected_row = {
        "key": "prepared-row",
        "harness": "codex",
        "provider": "openai",
        "skill": "demo",
        "recipe": "recipe-demo",
        "step": "run",
        "model": "gpt-test",
    }
    other_cohort_row = {
        "key": "other-cohort",
        "harness": "claude-code",
        "provider": "anthropic",
    }
    session_row = {**selected_row, "key": "session-row"}
    prepared = {
        "skills": [
            {
                "window": "7d",
                "levels": ["orchestrator"],
                "rows": [{"key": "fallback", "harness": "codex", "provider": "openai"}],
            }
        ],
        "context": {
            "blocks": [
                {
                    "window": "7d",
                    "levels": ["orchestrator"],
                    "rows": [
                        selected_row,
                        other_cohort_row,
                        {**selected_row, "key": "other-step", "step": "review"},
                    ],
                    "metrics": {
                        "parent_context": [
                            selected_row,
                            other_cohort_row,
                            {**selected_row, "key": "other-step", "step": "review"},
                        ]
                    },
                    "sessions": {
                        "owner-key-1": {
                            "rows": [session_row],
                            "metrics": {"parent_context": [session_row]},
                        }
                    },
                }
            ]
        },
    }
    route = {
        "view": "context",
        "entity": None,
        "params": {
            "window": ["7d"],
            "level": ["L2"],
            "harness": ["codex"],
            "provider": ["openai"],
            "skill": ["demo"],
            "recipe": ["recipe-demo"],
            "step": ["run"],
            "model": ["gpt-test"],
        },
    }

    selected = deck_js.call("DeckCore.selectPrepared", prepared, "context", chips, route)

    assert selected["rows"] == [selected_row]
    assert selected["metrics"]["parent_context"] == [selected_row]
    assert selected["skillRows"] == [{"key": "fallback", "harness": "codex", "provider": "openai"}]
    route["params"]["session"] = ["owner-key-1"]
    session_selected = deck_js.call("DeckCore.selectPrepared", prepared, "context", chips, route)
    assert session_selected["rows"] == [session_row]
    assert session_selected["metrics"]["parent_context"] == [session_row]
    assert session_selected["selection"]["session"] == "owner-key-1"
    route["params"]["session"] = ["unknown-owner"]
    missing_session = deck_js.call("DeckCore.selectPrepared", prepared, "context", chips, route)
    assert missing_session["rows"] == []
    assert missing_session["metrics"] == []

    route["params"]["session"] = ["owner-key-1"]
    route["params"]["step"] = ["missing-step"]
    missing_step = deck_js.call("DeckCore.selectPrepared", prepared, "context", chips, route)
    assert missing_step["rows"] == []
    assert missing_step["metrics"]["parent_context"] == []

    prepared["trend"] = {
        "blocks": [
            {
                "window": "7d",
                "levels": ["orchestrator"],
                "rows": [selected_row],
                "metrics": {"comparisons": [selected_row]},
            }
        ]
    }
    route["view"] = "trend"
    route["params"]["session"] = ["owner-key-1"]
    route["params"]["step"] = ["run"]
    trend_selected = deck_js.call("DeckCore.selectPrepared", prepared, "trend", chips, route)
    assert trend_selected["rows"] == [selected_row]
    assert trend_selected["metrics"]["comparisons"] == [selected_row]
    assert trend_selected["selection"]["session"] is None

    unattributed = {
        "key": "unattributed-event",
        "harness": None,
        "provider": None,
        "population": "unattributed_tool",
        "attribution_state": "unattributed",
    }
    prepared["errors"] = {
        "blocks": [
            {"window": "7d", "levels": ["orchestrator"], "rows": [unattributed], "metrics": {}}
        ]
    }
    route["view"] = "errors"
    for key in ("session", "skill", "recipe", "step", "model"):
        route["params"].pop(key, None)
    selected_errors = deck_js.call("DeckCore.selectPrepared", prepared, "errors", chips, route)
    assert selected_errors["rows"] == [unattributed]


@pytest.mark.parametrize(
    ("levels", "expected"),
    [
        pytest.param(None, ["short-window-skill"], id="window-local-default"),
        pytest.param(["L1", "L2"], ["short-window-skill"], id="mixed-levels-intersect"),
        pytest.param(["L2"], [], id="unobserved-window-level-is-empty"),
    ],
)
def test_prepared_selection_uses_the_selected_windows_level_domain(
    deck_js: Any,
    levels: list[str] | None,
    expected: list[str],
) -> None:
    chips = {
        "window": [
            {"key": "7d", "match": "7d", "label": "7d", "state": "live", "days": 7},
            {"key": "all", "match": "all", "label": "all", "state": "live", "days": None},
        ],
        "level": [
            {"key": "L1", "match": "skill", "label": "L1", "state": "live"},
            {"key": "L2", "match": "orchestrator", "label": "L2", "state": "live"},
        ],
        "harness": [{"key": "codex", "match": "codex", "label": "codex", "state": "live"}],
        "provider": [{"key": "openai", "match": "openai", "label": "openai", "state": "live"}],
    }
    short_row = {"skill": "short-window-skill", "harness": "codex", "provider": "openai"}
    all_row = {"skill": "older-orchestrator", "harness": "codex", "provider": "openai"}
    prepared = {
        "skills": [
            {"window": "7d", "levels": ["skill"], "rows": [short_row]},
            {"window": "all", "levels": ["orchestrator", "skill"], "rows": [all_row]},
        ],
        "roles": [],
        "relationships": [],
        "definitions": {},
    }
    params = {"window": ["7d"], "harness": ["codex"], "provider": ["openai"]}
    if levels is not None:
        params["level"] = levels

    selected = deck_js.call(
        "DeckCore.selectPrepared", prepared, "skill", chips, {"params": params}
    )

    assert [row["skill"] for row in selected["skillRows"]] == expected


def test_prepared_selection_keeps_skill_edges_with_unknown_child_provider(
    deck_js: Any,
) -> None:
    chips = {
        "window": [{"key": "all", "match": "all", "label": "all", "state": "live", "days": None}],
        "level": [{"key": "L1", "match": "skill", "label": "L1", "state": "live"}],
        "harness": [{"key": "codex", "match": "codex", "label": "codex", "state": "live"}],
        "provider": [{"key": "openai", "match": "openai", "label": "openai", "state": "live"}],
    }
    skill = {
        "skill": "parent-skill",
        "harness": "codex",
        "provider": "openai",
        "measures": {"input_tokens": {"state": "measured", "value": 42}},
    }
    edge = {
        "skill": "parent-skill",
        "role": "native-reader",
        "harness": "codex",
        "provider": "unknown",
    }
    prepared = {
        "skills": [{"window": "all", "levels": ["skill"], "rows": [skill]}],
        "roles": [],
        "relationships": [edge, {**edge, "skill": "another-skill"}],
        "definitions": {"native-reader": {"state": "available"}},
        "view_histories": {"skill": {"first_ms": 1, "last_ms": 1, "untimed": 0}},
    }
    route = {
        "view": "skill",
        "entity": "parent-skill",
        "params": {
            "window": ["all"],
            "level": ["L1"],
            "harness": ["codex"],
            "provider": ["openai"],
        },
    }

    selected = deck_js.call("DeckCore.selectPrepared", prepared, "skill", chips, route)

    assert selected["skillRows"] == [skill]
    assert selected["relationships"] == [edge]


@pytest.mark.parametrize(
    ("params", "expected_keys", "untimed"),
    [
        pytest.param({"level": ["unrecorded"]}, ["s1", "s2", "s3", "s4"], 0, id="unrecorded"),
        pytest.param({"harness": ["codex"]}, ["s4"], 0, id="codex"),
        pytest.param({"window": ["7d"]}, ["s1", "s2"], 1, id="last-week"),
        pytest.param({"harness": ["codex"], "window": ["7d"]}, [], 1, id="untimed-codex"),
        pytest.param({"window": ["28d"]}, [], 0, id="absent-window"),
        pytest.param({"harness": ["retired"]}, [], 0, id="stale-harness"),
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
            "0 runs",
            ALL_POPULATION_DETAIL.replace("level unrecorded (all)", "level none selected"),
            [
                "L0 is not selectable on this view — L0 leaf agents write no session row; "
                "they exist only as subagent transcripts",
            ],
            id="struck-level",
        ),
        pytest.param(
            _route(harness=["nope"]),
            "0 runs",
            ALL_POPULATION_DETAIL.replace(
                "harness claude-code + codex (all)", "harness none selected"
            ),
            [
                "nope does not appear in this index",
            ],
            id="unknown-harness",
        ),
        pytest.param(
            _route(window=["28d"]),
            "0 runs",
            ALL_POPULATION_DETAIL.replace(
                "window all history (2026-09-25 → 2026-10-04)", "window unavailable"
            ),
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


@pytest.mark.parametrize(
    ("facet", "key", "selected", "expected"),
    [
        pytest.param(
            "harness",
            "codex",
            True,
            {
                "text": "codex",
                "className": "chip",
                "disabled": False,
                "pressed": True,
                "reason": None,
            },
            id="live",
        ),
        pytest.param(
            "level",
            "L0",
            True,
            {
                "text": "L0",
                "className": "chip chip--struck",
                "disabled": True,
                "pressed": False,
                "reason": (
                    "L0 leaf agents write no session row; they exist only as subagent transcripts"
                ),
            },
            id="struck",
        ),
        pytest.param(
            "level",
            "L1",
            False,
            {
                "text": "L1 ✕",
                "className": "chip chip--absent",
                "disabled": True,
                "pressed": False,
                "reason": "no session row in this index records its orchestration level (#4622)",
            },
            id="absent-level",
        ),
        pytest.param(
            "window",
            "28d",
            False,
            {
                "text": "28 days ✕",
                "className": "chip chip--absent",
                "disabled": True,
                "pressed": False,
                "reason": "index history begins 2026-09-25 — 9 days retained (#4621)",
            },
            id="absent-window",
        ),
        pytest.param(
            None,
            "future",
            False,
            {
                "text": "future",
                "className": "chip chip--struck",
                "disabled": True,
                "pressed": False,
                "reason": "A custom strike (#9999)",
            },
            id="custom-issue",
        ),
    ],
)
def test_chip_presentation_covers_live_struck_absent_and_issue_reasons(
    deck_js: Any,
    deck_model: dict[str, Any],
    facet: str | None,
    key: str,
    selected: bool,
    expected: dict[str, Any],
) -> None:
    chip = (
        next(chip for chip in deck_model["chips"]["cohort"][facet] if chip["key"] == key)
        if facet is not None
        else {
            "key": key,
            "label": key,
            "state": ChipState.STRUCK.value,
            "reason": "A custom strike",
            "issue": 9999,
        }
    )
    assert deck_js.call("DeckCore.chipPresentation", chip, selected) == expected


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
