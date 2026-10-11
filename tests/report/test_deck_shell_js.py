"""Browser-free contract tests for the observability deck's shell."""

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from py_mini_racer import MiniRacer

from autoskillit.execution import REPORT_INDEX_SCHEMA_VERSION
from autoskillit.report.deck import build_deck_payload
from autoskillit.report.deck._analysis import _analysis_payload
from autoskillit.report.deck._html import deck_script_assets
from autoskillit.report.deck._registry import DECK_VIEWS
from tests.report._fixtures import DECK_GENERATED_AT

pytestmark = [pytest.mark.small]

_SHELL_DOM_HARNESS = r"""
function shellNode(tag) {
  const node = {tag, attrs: {}, children: [], events: [], text: "", __shellNode: true};
  node.appendChild = child => {
    if (!child || typeof child !== "object" || child.__shellNode !== true) {
      throw new TypeError("appendChild requires a node");
    }
    node.children.push(child);
    return child;
  };
  node.replaceChildren = (...children) => { node.children = children; node.text = ""; };
  node.setAttribute = (key, value) => { node.attrs[key] = String(value); };
  node.addEventListener = (event, callback) => { node.events.push([event, callback]); };
  Object.defineProperty(node, "textContent", {
    get() { return node.text + node.children.map(child =>
      typeof child === "object" ? child.textContent : String(child)).join(""); },
    set(value) { node.text = String(value); node.children = []; }
  });
  return node;
}
const shellNodes = Object.fromEntries(["deck-nav", "deck-cohort", "deck-view", "deck-foot"]
  .map(id => [id, shellNode("div")]));
globalThis.document = {
  createElement: shellNode,
  createElementNS: (_namespace, tag) => shellNode(tag),
  createTextNode: text => {
    const node = shellNode("#text");
    node.textContent = String(text);
    return node;
  },
  getElementById: id => shellNodes[id] ?? null,
  addEventListener() {}
};
globalThis.location = {hash: ""};
const shellEvents = {};
globalThis.window = {location, addEventListener(name, callback) { shellEvents[name] = callback; }};
globalThis.registerDeckProbe = viewId => {
  DeckShell.registerView(viewId, ctx => {
    globalThis.__selected = {rows: ctx.rows, metrics: ctx.metrics, session: ctx.selection.session};
    return ctx.el("p", {}, ["Selected: ", ctx.el("span", {},
      ctx.rows.map(row => row.key).join(",")), " selected."]);
  });
  return true;
};
globalThis.deckProbeResult = () => JSON.stringify(__selected);
globalThis.deckCohortText = () => shellNodes["deck-cohort"].textContent;
globalThis.deckViewText = () => shellNodes["deck-view"].textContent;
globalThis.deckFootText = () => shellNodes["deck-foot"].textContent;
globalThis.deckSetHash = hash => {
  location.hash = hash;
  if (shellEvents.hashchange) shellEvents.hashchange();
};
"""


def test_shell_registers_built_views_without_document(deck_asset: Any) -> None:
    with MiniRacer() as ctx:
        assert ctx.eval("typeof document") == "undefined"
        for rel in deck_script_assets():
            ctx.eval(deck_asset(rel))
        registered = ctx.call("DeckShell.registeredViews")

    expected = [view.view_id for view in DECK_VIEWS if view.planned_issue is None]
    assert sorted(registered) == sorted(expected)


@pytest.mark.parametrize(
    ("view_id", "headline"),
    [
        ("context", "context record"),
        ("errors", "symptom group"),
        ("trend", "daily observation"),
        ("gaps", "gap"),
        ("parity", "coverage cell"),
    ],
)
def test_shell_passes_selected_prepared_rows_to_renderer_and_headline(
    deck_asset: Any, view_id: str, headline: str
) -> None:
    selected_row = {"key": "prepared-population-row", "harness": "codex", "provider": "openai"}
    chips = {
        "window": [
            {"key": "all", "match": "all", "label": "all history", "state": "live", "days": None}
        ],
        "level": [{"key": "L2", "match": "orchestrator", "label": "L2", "state": "live"}],
        "harness": [{"key": "codex", "match": "codex", "label": "Codex", "state": "live"}],
        "provider": [{"key": "openai", "match": "openai", "label": "OpenAI", "state": "live"}],
    }
    session_rows = {"owner-key-1": {"rows": [selected_row], "metrics": {"session": True}}}
    block = {
        "window": "all",
        "levels": ["orchestrator"],
        "rows": [selected_row],
        "metrics": {"selected": view_id},
        "sessions": session_rows,
    }
    payload = {
        "tables": {"sessions": {"columns": ["session_key"], "rows": []}},
        "facets": [
            {"id": "window", "kind": "window", "label": "Window"},
            {"id": "level", "kind": "values", "label": "Level"},
            {"id": "harness", "kind": "values", "label": "Harness"},
            {"id": "provider", "kind": "values", "label": "Provider"},
        ],
        "landing": "cohort",
        "views": [
            {
                "id": view_id,
                "status": "built",
                "group": "Evidence",
                "question": view_id,
                "decision": "inspect evidence",
            }
        ],
        "chips": {view_id: chips},
        "prepared": {view_id: {"blocks": [block]}},
        "history": {"first_ms": None, "last_ms": None, "untimed": 0},
        "generated_at_ms": 0,
        "index_schema_version": 3,
    }
    with MiniRacer() as ctx:
        ctx.eval(deck_asset("core.js"))
        ctx.eval(_SHELL_DOM_HARNESS)
        ctx.eval(deck_asset("shell.js"))
        ctx.call("registerDeckProbe", view_id)
        ctx.eval(
            'location.hash = "#/" + "'
            + view_id
            + '?window=all&level=L2&harness=codex&provider=openai";'
        )
        ctx.eval(f"DeckShell.boot({json.dumps(payload)});")
        result = json.loads(ctx.call("deckProbeResult"))
        cohort_text = ctx.call("deckCohortText")
        view_text = ctx.call("deckViewText")

    assert result["rows"] == [selected_row]
    assert result["metrics"] == {"selected": view_id}
    assert f"1 {headline}" in cohort_text
    assert view_text == "Selected: prepared-population-row selected."


def _analysis_payload_for_shell(
    payload: dict[str, Any],
    *,
    generated_at_ms: int,
    cohort: dict[str, Any],
    coverage: dict[str, Any],
    evidence: dict[str, Any],
    findings: list[dict[str, Any]],
) -> dict[str, Any]:
    packet = {
        "payload": payload,
        "input_fingerprint": "frozen-input-fingerprint",
        "cohort": cohort,
        "coverage": coverage,
        "evidence": evidence,
    }
    interpretation = {
        "interpreter": {"role": "research-synthesizer", "model": "test-model", "prompt": ""},
        "findings": findings,
    }
    return _analysis_payload(packet, interpretation, generated_at_ms)


def test_shell_keeps_saved_analysis_scoped_and_fixed_across_interactive_filters(
    deck_asset: Any, deck_payload: dict[str, Any]
) -> None:
    guardrail = "Observed differences prompt investigation; they are not proof of causation."
    comparison = {
        "group": ["make-plan", "recipe-a", "scope", "claude-code", "anthropic", "model-a"],
        "metric": "input_tokens",
        "before": {
            "state": "measured",
            "value": 10.5,
            "observed_sessions": 2,
            "eligible_sessions": 3,
            "state_counts": {"measured": 2, "unknown": 1},
        },
        "after": {
            "state": "measured",
            "value": 14.25,
            "observed_sessions": 3,
            "eligible_sessions": 4,
            "state_counts": {"measured": 3, "unknown": 1},
        },
        "delta": {
            "state": "measured",
            "value": 3.75,
            "unit": "tokens",
            "reason": None,
            "earlier_samples": 2,
            "later_samples": 3,
            "guardrail": guardrail,
        },
    }
    cohort = {
        "change_id": "change-42",
        "effective_at": "2026-10-11T00:00:00Z",
        "scope": {"skills": ["make-plan"], "backends": ["claude-code"], "roles": ["planner"]},
        "expected_effect": "Reduce input tokens while preserving requirement coverage.",
        "before_count": 2,
        "after_count": 3,
        "excluded_count": 1,
        "preregistration": "Marked pre-cutover observations provide a baseline.",
        "comparisons": [comparison],
    }
    coverage = {
        "limitation": (
            "Coverage includes retained report-index sources, not complete historical coverage."
        ),
        "populations": {
            "sessions": {
                "rows": 4,
                "untimed_rows": 1,
                "measure_states": {
                    "input_tokens": {"measured": 2, "measured_zero": 0, "unknown": 1}
                },
            }
        },
    }
    evidence = {
        "trend-evidence": {"view_id": "trend", "source": "rollout", "measurement": comparison},
        "cohort-evidence": {
            "view_id": "cohort",
            "source": "sessions",
            "measurement": {"key": "run-a", "time_ms": deck_payload["generated_at_ms"]},
        },
    }
    findings = [
        {
            "view_id": "trend",
            "text": "Trend-only interpretation.",
            "evidence_ids": ["trend-evidence"],
        },
        {
            "view_id": "cohort",
            "text": "Cohort-only interpretation.",
            "evidence_ids": ["cohort-evidence"],
        },
    ]
    created = datetime(2026, 10, 11, 12, 34, 56, 789_000, tzinfo=UTC)
    created_ms = int(created.timestamp() * 1000)
    payload = _analysis_payload_for_shell(
        deck_payload,
        generated_at_ms=created_ms,
        cohort=cohort,
        coverage=coverage,
        evidence=evidence,
        findings=findings,
    )

    with MiniRacer() as ctx:
        ctx.eval(deck_asset("core.js"))
        ctx.eval(_SHELL_DOM_HARNESS)
        for asset in deck_script_assets()[1:]:
            ctx.eval(deck_asset(asset))
        ctx.eval('location.hash = "#/trend?window=7d&harness=claude-code&provider=anthropic";')
        ctx.eval(f"DeckShell.boot({json.dumps(payload)});")
        trend_text = ctx.call("deckViewText")
        cohort_text = ctx.call("deckCohortText")
        footer = ctx.call("deckFootText")
        ctx.call("deckSetHash", "#/trend?window=all&harness=codex&provider=codex")
        filtered_trend_text = ctx.call("deckViewText")
        ctx.call("deckSetHash", "#/cohort?window=all")
        cohort_view_text = ctx.call("deckViewText")

    assert "Run analysis" in trend_text
    assert "Rollout marker" in trend_text
    assert "change-42" in trend_text
    assert "make-plan" in trend_text and "claude-code" in trend_text and "planner" in trend_text
    assert "Reduce input tokens while preserving requirement coverage." in trend_text
    assert "Marked pre-cutover observations provide a baseline." in trend_text
    assert "Frozen coverage" in trend_text
    assert "unknown" in trend_text
    assert "retained report-index sources" in trend_text
    assert "Interpretation" in trend_text
    assert "Trend-only interpretation." in trend_text
    assert "Cohort-only interpretation." not in trend_text
    saved_values = (
        "10.5",
        "14.25",
        "3.75",
        "tokens",
        "Observed samples 2",
        "eligible 3",
        "unknown 1",
        "Observed before/after samples 2/3",
        guardrail,
    )
    for saved_value in saved_values:
        assert saved_value in trend_text
        assert saved_value in filtered_trend_text
    assert "Trend-only interpretation." in filtered_trend_text
    assert "Cohort-only interpretation." in cohort_view_text
    assert "Trend-only interpretation." not in cohort_view_text

    anchor = datetime.fromtimestamp(deck_payload["generated_at_ms"] / 1000, UTC)
    anchor_date = anchor.strftime("%Y-%m-%d")
    created_iso = created.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    assert "analysis created" in footer.lower()
    assert "corpus/window anchor" in footer.lower()
    assert created_iso in footer
    assert anchor_date in footer
    assert f"→ {anchor_date}" in cohort_text
    assert "2026-10-11" not in cohort_text


def test_shell_shows_explicit_empty_corpus_interpretation(deck_asset: Any) -> None:
    payload = build_deck_payload(
        [],
        generated_at=DECK_GENERATED_AT,
        index_schema_version=REPORT_INDEX_SCHEMA_VERSION,
    )
    payload = _analysis_payload_for_shell(
        payload,
        generated_at_ms=int(datetime(2026, 10, 11, tzinfo=UTC).timestamp() * 1000),
        cohort={"change_id": None, "reason": "No rollout cohort applied.", "comparisons": []},
        coverage={"limitation": "Coverage is limited to the frozen corpus.", "populations": {}},
        evidence={},
        findings=[
            {
                "view_id": "cohort",
                "text": "No observations in the frozen corpus.",
                "evidence_ids": [],
            }
        ],
    )

    with MiniRacer() as ctx:
        ctx.eval(deck_asset("core.js"))
        ctx.eval(_SHELL_DOM_HARNESS)
        for asset in deck_script_assets()[1:]:
            ctx.eval(deck_asset(asset))
        ctx.eval(f"DeckShell.boot({json.dumps(payload)});")
        text = ctx.call("deckViewText")

    assert "No observations in the frozen corpus." in text
