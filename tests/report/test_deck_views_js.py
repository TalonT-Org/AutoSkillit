"""Browser-free renderer contracts for the observability deck's prepared views."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from py_mini_racer import MiniRacer

from autoskillit.report.deck import build_deck_payload
from tests.report._fixtures import session_row, subagent_row, token_measure

pytestmark = [pytest.mark.small]

VIEW_IDS = ("spend", "efficiency", "skill", "role")
NEW_VIEW_IDS = ("context", "errors", "trend", "gaps", "parity")

_RENDERER_HARNESS = r"""
globalThis.__deckViews = {};
globalThis.DeckShell = {
  registerView(id, render) { __deckViews[id] = render; }
};
globalThis.location = {hash: "#/cohort"};
globalThis.window = {location, addEventListener() {}};

function deckNode(tag, attrs = {}, children = null) {
  const node = {tag, attrs: {...attrs}, children: [], events: {}, hidden: attrs.hidden === true,
    textContent: "", __deckNode: true};
  node.appendChild = child => {
    if (!child || typeof child !== "object" || child.__deckNode !== true) {
      throw new TypeError("appendChild requires a node");
    }
    node.children.push(child);
    return child;
  };
  node.addEventListener = (name, callback) => { node.events[name] = callback; };
  node.setAttribute = (name, value) => { node.attrs[name] = String(value); };
  if (Array.isArray(children)) {
    for (const child of children) {
      if (child === null || child === undefined) continue;
      if (["string", "number", "boolean"].includes(typeof child)) {
        node.appendChild(deckNode("#text", {}, String(child)));
      } else {
        node.appendChild(child);
      }
    }
  } else if (children !== null && children !== undefined) {
    if (typeof children === "object") node.appendChild(children);
    else node.textContent = String(children);
  }
  return node;
}

function deckText(node) {
  if (node === null || node === undefined) return "";
  if (Array.isArray(node)) return node.map(deckText).join(" ");
  if (typeof node !== "object") return String(node);
  return [node.textContent || "", ...node.children.map(deckText)].join(" ");
}

function deckLinks(node) {
  if (node === null || node === undefined) return [];
  if (Array.isArray(node)) return node.flatMap(deckLinks);
  if (typeof node !== "object") return [];
  return [...(node.tag === "a" ? [node.attrs.href] : []), ...node.children.flatMap(deckLinks)];
}

function deckFind(node, predicate) {
  if (node === null || node === undefined) return null;
  if (Array.isArray(node)) {
    for (const child of node) {
      const found = deckFind(child, predicate);
      if (found) return found;
    }
    return null;
  }
  if (typeof node !== "object") return null;
  if (predicate(node)) return node;
  for (const child of node.children) {
    const found = deckFind(child, predicate);
    if (found) return found;
  }
  return null;
}

function deckFindAll(node, predicate) {
  if (node === null || node === undefined) return [];
  if (Array.isArray(node)) return node.flatMap(child => deckFindAll(child, predicate));
  if (typeof node !== "object") return [];
  return [...(predicate(node) ? [node] : []), ...node.children.flatMap(child =>
    deckFindAll(child, predicate))];
}

function deckSnapshot(node) {
  if (node === null || node === undefined || typeof node !== "object") return node;
  return {
    tag: node.tag,
    attrs: node.attrs,
    text: deckText(node),
    links: deckLinks(node),
    reviews: deckFindAll(node, child =>
      child.attrs && child.attrs["data-review-signal"] !== undefined).map(signal => {
      const control = deckFind(signal, child =>
        child.attrs && child.attrs["data-review-flag"] !== undefined);
      const marker = deckFind(signal, child =>
        child.attrs && child.attrs.class === "view-review__marker");
      const definitions = deckFind(signal, child =>
        child.attrs && child.attrs.class === "view-review__definitions");
      return {
        attrs: control.attrs,
        markerHidden: marker.hidden,
        markerText: deckText(marker),
        linksBeforeFlag: definitions !== null && signal.children.indexOf(definitions) <
          signal.children.indexOf(control)
      };
    })
  };
}

function deckTable({columns, rows, defaultSort}) {
  return deckNode("table", {"data-default-sort": defaultSort && defaultSort.key}, [
    deckNode("thead", {}, columns.map(column => deckNode("th", {}, column.label))),
    deckNode("tbody", {}, rows.map(row => deckNode("tr", {}, columns.map(column =>
      deckNode("td", {}, column.cell ? column.cell(row) : row[column.key])))))
  ]);
}

globalThis.DeckTest = {
  render(id, input) {
    const route = input.route;
    const cohortKeys = ["harness", "provider", "level", "window"];
    const context = {
      ...input,
      model: {prepared: input.prepared},
      el: deckNode,
      href: target => DeckCore.hrefFor(route, target, cohortKeys),
      entityLink: (text, target) => deckNode("a", {
        href: DeckCore.hrefFor(route, target, cohortKeys)
      }, text),
      sortableTable: deckTable,
      availabilityCell: measure => {
        if (!["measured", "measured_zero", "unknown", "unavailable", "not_applicable"]
          .includes(measure.state)) {
          throw new Error("unknown availability state: " + measure.state);
        }
        return deckNode("span", {"data-state": measure.state},
          measure.state === "measured" ? String(measure.value) :
            measure.state === "measured_zero" ? "0" : measure.state);
      },
      svgElement: (tag, attrs, children) => deckNode(tag, attrs, children),
      barChart: (items, options) => deckNode("svg", {"aria-label": options.label},
        JSON.stringify(items))
    };
    location.hash = DeckCore.encodeRoute(route, cohortKeys);
    globalThis.__deckLastTree = __deckViews[id](context);
    return deckSnapshot(__deckLastTree);
  },
  renderPrepared(id, payload, route) {
    const chips = payload.chips[id];
    const selected = DeckCore.selectPrepared(payload.prepared, id, chips, route);
    return globalThis.DeckTest.render(id, {prepared: payload.prepared, rows: selected.rows,
      metrics: selected.metrics, chips, selection: selected.selection, route});
  },
  clickReview(index = 0) {
    const signals = deckFindAll(__deckLastTree,
      child => child.attrs && child.attrs["data-review-signal"] !== undefined);
    const control = deckFind(signals[index],
      child => child.attrs && child.attrs["data-review-flag"] !== undefined);
    if (!control || !control.events.click) throw new Error("review flag has no click handler");
    control.events.click();
    return deckSnapshot(__deckLastTree).reviews[index];
  },
  clickContextInput() {
    const button = deckFind(__deckLastTree, node =>
      node.attrs && node.attrs.class === "context-input-toggle");
    const chart = deckFind(__deckLastTree, node => node.tag === "svg" &&
      node.attrs && node.attrs["aria-label"] === "Inclusive input tokens by turn");
    if (!button || !button.events.click || !chart) {
      throw new Error("context input toggle is missing");
    }
    const initial = {pressed: button.attrs["aria-pressed"], style: chart.attrs.style};
    button.events.click();
    return {initial, pressed: button.attrs["aria-pressed"], style: chart.attrs.style};
  },
  snapshot() { return deckSnapshot(__deckLastTree); },
  currentHash() { return location.hash; }
};
"""


def _load_renderers(deck_asset: Any) -> MiniRacer:
    context = MiniRacer()
    context.eval(deck_asset("core.js"))
    context.eval(_RENDERER_HARNESS)
    for view_id in (*VIEW_IDS, *NEW_VIEW_IDS):
        context.eval(deck_asset(f"views/{view_id}.js"))
    return context


def _view_context(view_id: str, *, definitions_available: bool = True) -> dict[str, Any]:
    roles = ("reader", "reviewer")
    definitions = {
        role: {
            "state": "available" if definitions_available or role == "reader" else "unavailable",
            "description": f"Definition for {role}: definition-first marker",
            "body": f"{role} role instructions",
            "tools": ["read_file"],
            "model": "small-model",
            "reader_tools": ["read_file"],
            "codex_model": "small-model",
        }
        for role in roles
    }

    ratio = {
        "state": "measured",
        "value": 2.5,
        "sample_unit": "child-invocation",
        "sample_size": 2,
        "review_eligible": True,
        "review_reason": "Definitions are available for all contributors.",
        "definition_roles": list(roles),
    }
    skill_ratio = {**ratio, "sample_unit": "skill-run"}
    skill_row = {
        "skill": "summarizer",
        "harness": "codex",
        "provider": "openai",
        "measures": {
            "input_tokens": {"state": "measured", "value": 101, "runs": 2},
            "output_tokens": {"state": "measured", "value": 40, "runs": 2},
            "cache_read_tokens": {"state": "measured", "value": 20, "runs": 2},
            "cache_write_tokens": {"state": "measured", "value": 3, "runs": 2},
        },
        "ratios": {"input_output": skill_ratio, "cache_share": skill_ratio},
        "recipe_steps": [],
    }
    role_row = {
        "role": "reviewer",
        "provider": "openai",
        "harnesses": [
            {
                "harness": "codex",
                "models": ["observed-model"],
                "measures": {"input_tokens": {"state": "measured", "value": 11, "runs": 2}},
                "ratios": {
                    "input_output": ratio,
                    "cache_share": ratio,
                    "tool_mix": {"read_file": ratio},
                },
            }
        ],
    }
    prepared = {
        "skills": [],
        "roles": [],
        "relationships": [
            {"skill": "summarizer", "role": role, "harness": "codex", "provider": "openai"}
            for role in roles
        ],
        "definitions": definitions,
        "view_chips": {},
        "view_histories": {view_id: {"first_ms": 1, "last_ms": 2, "untimed": 0}},
    }
    return {
        "prepared": prepared,
        "metrics": [skill_row] if view_id == "skill" else [role_row],
        "skillMetrics": [skill_row],
        "roleMetrics": [role_row],
        "relationships": prepared["relationships"],
        "definitions": definitions,
        "viewHistory": prepared["view_histories"][view_id],
        "chips": {"window": [], "level": [], "harness": [], "provider": []},
        "selection": {},
        "route": {
            "view": view_id,
            "entity": "summarizer"
            if view_id == "skill"
            else "reviewer"
            if view_id == "role"
            else None,
            "params": {
                "window": ["all"],
                "level": ["L1"],
                "harness": ["codex"],
                "provider": ["openai"],
            },
        },
    }


def _new_view_context(view_id: str) -> dict[str, Any]:
    def measured(value: int | float) -> dict[str, Any]:
        return {"state": "measured", "value": value}

    measured_zero = {"state": "measured_zero", "value": 0}
    unknown = {"state": "unknown"}
    unavailable = {"state": "unavailable"}
    common = {
        "harness": "codex",
        "provider": "openai",
        "skill": "summarizer",
        "recipe": "daily",
        "step": "draft",
        "level": "orchestrator",
        "session_key": "owner-key-1",
    }
    turn = {
        **common,
        "key": "turn-1",
        "kind": "turn",
        "ordinal": 1,
        "time_ms": 1791235200000,
        "model": "gpt-test",
        "input_tokens": measured(75),
        "output_tokens": measured_zero,
        "cache_read_tokens": measured(50),
        "cache_write_tokens": unavailable,
        "context_window_tokens": 100,
        "context_fraction_percent": measured(50),
        "turn_usage_state": "observed",
        "turn_usage_reason": None,
        "fraction_disagreement": False,
    }
    coverage = {
        **common,
        "key": "coverage-1",
        "kind": "coverage",
        "turn_usage_state": "unavailable",
        "turn_usage_reason": "turn ledger is missing",
        "request_model": "gpt-test",
    }
    error = {
        **common,
        "key": "error-1",
        "population": "session_failure",
        "symptom": "unknown symptom",
        "failures": {**measured(1), "state_counts": {"measured": 1}},
        "failure_rate": {**measured(0.25), "sample_size": 4},
        "eligible_count": 4,
        "observed_count": 3,
        "unknown_count": 1,
        "tool_result_event_count": 2,
        "tool_event_coverage": unknown,
        "attribution_state": "owner indexed",
    }
    trend = {
        **common,
        "key": "trend-1",
        "day": "2026-10-05",
        "time_ms": 1791158400000,
        "model": "gpt-test",
        "measures": {
            field: {
                **measured(value),
                "observed_sessions": 1,
                "eligible_sessions": 2,
                "state_counts": {"measured": 1, "unknown": 1},
            }
            for field, value in {
                "input_tokens": 75,
                "output_tokens": 0,
                "cache_read_tokens": 50,
                "cache_write_tokens": 0,
            }.items()
        },
        "failure_share": {**measured_zero, "sample_size": 1},
        "eligible_sessions": 2,
        "untimed_sessions": 1,
        "future_sessions": 0,
        "retained_from_ms": 1791158400000,
        "window_start_ms": 1790553600000,
        "window_end_ms": 1791763200000,
    }
    gap = {
        **common,
        "key": "gap-1",
        "population": "session_turn",
        "field": "context_window_tokens",
        "state": "unavailable",
        "measure": unavailable,
        "state_counts": {"unavailable": 1},
        "eligible_count": 1,
        "observation_count": 0,
        "reason": "resolved model window is absent",
        "question": "Which resolved window is needed?",
        "prerequisite": "Resolve the turn model window",
        "source": "turn usage ledger",
    }
    parity = [
        {
            **gap,
            "key": "parity-1",
            "field": "input_tokens",
            "state": "measured_zero",
            "measure": measured_zero,
            "state_counts": {"measured_zero": 1},
            "observation_count": 1,
        },
        {**gap, "key": "parity-2", "field": "context_window_tokens"},
    ]
    populations = {
        "context": (
            [turn, coverage],
            {
                "parent_context": [
                    {
                        **common,
                        "model": "gpt-test",
                        "prompt": {
                            **measured(12),
                            "state_counts": {"measured": 1},
                            "eligible_count": 1,
                            "observation_count": 1,
                        },
                        "returned": unavailable,
                        "comparison": {
                            "state": "no_observations",
                            "reason": "no matched invocation",
                        },
                        "eligible_invocations": 1,
                        "matched_invocations": 0,
                        "provenance": ["parent transcript"],
                        "source_basis": "recorded text at parent model",
                    }
                ]
            },
        ),
        "errors": ([error], {}),
        "trend": (
            [trend],
            {
                "comparisons": [
                    {
                        **common,
                        "model": "gpt-test",
                        "earlier": {
                            "start_ms": 1,
                            "end_ms": 2,
                            "covered_start_ms": 1,
                            "covered_end_ms": 2,
                            "measures": {"input_tokens": measured(50)},
                            "failure_share": measured_zero,
                        },
                        "later": {
                            "start_ms": 3,
                            "end_ms": 4,
                            "covered_start_ms": 3,
                            "covered_end_ms": 4,
                            "measures": {"input_tokens": measured(75)},
                            "failure_share": measured_zero,
                        },
                        "deltas": {
                            "input_tokens": {
                                **measured(25),
                                "unit": "tokens/session",
                                "earlier_samples": 1,
                                "later_samples": 1,
                            },
                            "failure_share": {
                                **measured_zero,
                                "unit": "percentage_points",
                                "earlier_samples": 1,
                                "later_samples": 1,
                            },
                        },
                    }
                ]
            },
        ),
        "gaps": ([gap], {}),
        "parity": (parity, {}),
    }
    rows, metrics = populations[view_id]
    params = {
        "window": ["all"],
        "level": ["L2"],
        "harness": ["codex"],
        "provider": ["openai"],
    }
    if view_id in {"context", "gaps", "parity"}:
        params["session"] = ["owner-key-1"]
    return {
        "prepared": {},
        "rows": rows,
        "metrics": metrics,
        "chips": {},
        "selection": {},
        "route": {"view": view_id, "entity": None, "params": params},
    }


@pytest.mark.parametrize(
    ("measure", "expected"),
    [
        ({"state": "measured_zero", "value": 0}, {"primitive": "measured_zero"}),
        (
            {"state": "mixed"},
            {"class": "coverage-state coverage-state--mixed", "text": "mixed coverage"},
        ),
        (
            {"state": "no_observations"},
            {"class": "coverage-state coverage-state--no_observations", "text": "no observations"},
        ),
        (None, {"class": "coverage-state coverage-state--unknown", "text": "unknown"}),
    ],
)
def test_shared_coverage_cell_keeps_primitive_and_summary_states_distinct(
    deck_asset: Any, measure: dict[str, Any] | None, expected: dict[str, str]
) -> None:
    context = _load_renderers(deck_asset)
    try:
        context.eval("""
          globalThis.coverageCell = (measure, className) => DeckCore.coverageStateCell({
            availabilityCell: item => ({primitive: item.state}),
            el: (tag, attrs, text) => ({class: attrs.class, text})
          }, measure, className);
        """)
        assert context.call("coverageCell", measure) == expected
        if "class" in expected:
            assert context.call("coverageCell", measure, "coverage-state") == {
                **expected,
                "class": "coverage-state",
            }
    finally:
        context.close()


def test_each_prepared_view_renders_its_population_and_cohort_links(deck_asset: Any) -> None:
    context = _load_renderers(deck_asset)
    try:
        markers = {"spend": "101", "efficiency": "2.5", "skill": "summarizer", "role": "reviewer"}
        for view_id, marker in markers.items():
            rendered = context.call("DeckTest.render", view_id, _view_context(view_id))
            assert marker in rendered["text"]
            assert rendered["links"]
            assert any(
                "harness=codex" in href and "provider=openai" in href for href in rendered["links"]
            )
            if view_id in {"efficiency", "skill", "role"}:
                assert rendered["reviews"]
                assert all(control["linksBeforeFlag"] for control in rendered["reviews"])
        role_view = context.call("DeckTest.render", "role", _view_context("role"))
        assert "L0" in role_view["text"]
        assert "spawning skill level" in role_view["text"].lower()
        assert "observed-model" in role_view["text"]
        assert "Claude Code declared tools" in role_view["text"]
        assert "Codex read-only tools" in role_view["text"]
        efficiency_view = context.call(
            "DeckTest.render", "efficiency", _view_context("efficiency")
        )
        assert "2 skill runs" in efficiency_view["text"]
        assert "2 child invocations" in efficiency_view["text"]
    finally:
        context.close()


@pytest.mark.parametrize(
    ("view_id", "marker"),
    [
        ("context", "Cache-read proxy"),
        ("errors", "unknown symptom"),
        ("trend", "failure share"),
        ("gaps", "Which resolved window is needed?"),
        ("parity", "coverage parity"),
    ],
)
def test_view_renderers_keep_prepared_populations_and_filter_links(
    deck_asset: Any, view_id: str, marker: str
) -> None:
    context = _load_renderers(deck_asset)
    try:
        rendered = context.call("DeckTest.render", view_id, _new_view_context(view_id))
        assert marker.lower() in rendered["text"].lower()
        assert rendered["links"]
        assert any(
            "harness=codex" in href and "provider=openai" in href for href in rendered["links"]
        )
        if view_id in {"context", "gaps", "parity"}:
            assert any("session=owner-key-1" in href for href in rendered["links"])
    finally:
        context.close()


def test_context_input_toggle_shows_inclusive_input_series(deck_asset: Any) -> None:
    context = _load_renderers(deck_asset)
    try:
        context.call("DeckTest.render", "context", _new_view_context("context"))
        input_toggle = context.call("DeckTest.clickContextInput")
        assert input_toggle["initial"] == {"pressed": "false", "style": "display:none"}
        assert input_toggle["pressed"] == "true"
        assert input_toggle["style"] == "display:block"
    finally:
        context.close()


def test_python_prepared_payload_renders_all_new_views(deck_asset: Any) -> None:
    generated_at = datetime(2026, 10, 7, tzinfo=UTC)
    event_at = generated_at - timedelta(seconds=5)
    event_ms = int(event_at.timestamp() * 1000)
    owner = session_row(
        "s1",
        session_id="native-s1",
        time_ms=event_ms,
        harness="codex",
        provider="openai",
        level="skill",
        skill="demo",
        recipe="recipe-demo",
        step="run",
        success=False,
        subtype="ToolError",
        input_tokens=token_measure(75),
        output_tokens=token_measure(0),
        cache_read_tokens=token_measure(50),
        cache_write_tokens=token_measure(None, state="unavailable"),
    )
    turn = {
        "key": "s1:turn:0",
        "kind": "turn",
        "session_key": "s1",
        "session_id": "native-s1",
        "source_id": "s1",
        "ordinal": 0,
        "time_ms": event_ms,
        "harness": "codex",
        "provider": "openai",
        "model": "gpt-known",
        "skill": "demo",
        "recipe": "recipe-demo",
        "step": "run",
        "level": "skill",
        "input_tokens": token_measure(75),
        "output_tokens": token_measure(0),
        "cache_read_tokens": token_measure(50),
        "cache_write_tokens": token_measure(None, state="unavailable"),
        "context_window_tokens": 100,
        "context_fraction": 0.5,
        "request_id": "req-1",
    }
    request = {
        "key": "s1:req-1",
        "kind": "request",
        "session_key": "s1",
        "request_id": "req-1",
        "time_ms": event_ms,
        "model": "gpt-known",
    }
    tool = {
        "key": "s1:tool-1",
        "kind": "tool",
        "session_key": "s1",
        "time_ms": event_ms,
        "success": False,
        "error_type": "ToolError",
    }
    child = subagent_row(
        "child-1",
        parent=owner,
        role="reviewer",
        skill="demo",
        provider="openai",
        input_tokens=11,
        output_tokens=7,
        cache_read_tokens=0,
        cache_write_tokens=None,
        tool_counts={"read_file": 1},
    )
    child["parent_context_spans"] = [
        {
            "field": "parent_prompt_tokens",
            "measure": {"state": "measured", "value": 13},
            "invocation_id": "invocation-1",
            "turn_id": "turn-1",
            "source_id": "parent-transcript-1",
            "timestamp": event_at.isoformat(),
            "harness": "codex",
            "provider": "openai",
            "model": "gpt-known",
            "tokenizer_version": "0.14.0",
            "encoding": "cl100k_base",
            "reason": None,
        },
        {
            "field": "subagent_return_tokens",
            "measure": {"state": "measured", "value": 9},
            "invocation_id": "invocation-1",
            "turn_id": "turn-1",
            "source_id": "parent-transcript-2",
            "timestamp": event_at.isoformat(),
            "harness": "codex",
            "provider": "openai",
            "model": "gpt-known",
            "tokenizer_version": "0.14.0",
            "encoding": "cl100k_base",
            "reason": None,
        },
    ]
    payload = build_deck_payload(
        [owner],
        request_rows=[request],
        tool_rows=[tool],
        subagent_rows=[child],
        turn_rows=[turn],
        generated_at=generated_at,
        index_schema_version=3,
    )
    context = _load_renderers(deck_asset)
    try:
        for view_id in NEW_VIEW_IDS:
            rendered = context.call(
                "DeckTest.renderPrepared",
                view_id,
                payload,
                {"view": view_id, "entity": None, "params": {}},
            )
            assert rendered["tag"] in {"div", "section"}
            assert rendered["text"]
    finally:
        context.close()


def test_manual_review_flags_require_definitions_and_reset_on_rerender(
    deck_asset: Any,
) -> None:
    context = _load_renderers(deck_asset)
    try:
        available = context.call("DeckTest.render", "efficiency", _view_context("efficiency"))
        assert len(available["reviews"]) >= 3
        assert all(
            control["attrs"].get("disabled") is not True for control in available["reviews"]
        )
        assert all(
            control["attrs"].get("aria-pressed") == "false" for control in available["reviews"]
        )
        assert all(control["markerHidden"] for control in available["reviews"])
        assert all(control["linksBeforeFlag"] for control in available["reviews"])
        assert available["text"].index("definition-first marker") < available["text"].index(
            "Flag for review"
        )
        initial_hash = context.call("DeckTest.currentHash")

        flagged = context.call("DeckTest.clickReview", 0)
        after_one_flag = context.call("DeckTest.snapshot")["reviews"]
        assert flagged["attrs"]["aria-pressed"] == "true"
        assert flagged["markerHidden"] is False
        assert flagged["markerText"].strip() == "Flagged for review"
        assert after_one_flag[1]["attrs"]["aria-pressed"] == "false"
        assert context.call("DeckTest.currentHash") == initial_hash

        unavailable = context.call(
            "DeckTest.render",
            "efficiency",
            _view_context("efficiency", definitions_available=False),
        )
        assert unavailable["reviews"]
        assert all("disabled" in control["attrs"] for control in unavailable["reviews"])
        assert all(control["linksBeforeFlag"] for control in unavailable["reviews"])
        assert "Contributor definitions unavailable" in unavailable["text"]

        reset = context.call("DeckTest.render", "efficiency", _view_context("efficiency"))
        assert all(control["attrs"]["aria-pressed"] == "false" for control in reset["reviews"])
        assert all(control["markerHidden"] for control in reset["reviews"])
    finally:
        context.close()


def test_role_definition_and_spawning_links_survive_empty_metric_scope(deck_asset: Any) -> None:
    context = _load_renderers(deck_asset)
    try:
        view = _view_context("role")
        view["route"]["entity"] = "reviewer"
        view["metrics"] = []
        view["roleMetrics"] = []

        rendered = context.call("DeckTest.render", "role", view)

        assert "reviewer" in rendered["text"]
        assert "reviewer role instructions" in rendered["text"]
        assert "No child-invocation metrics match these facets." in rendered["text"]
        assert any("#/skill/summarizer" in href for href in rendered["links"])
    finally:
        context.close()
