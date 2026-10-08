"""Browser-free contract tests for the observability deck's shell."""

import json
from typing import Any

import pytest
from py_mini_racer import MiniRacer

from autoskillit.report.deck._html import deck_script_assets
from autoskillit.report.deck._registry import DECK_VIEWS

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
globalThis.window = {location, addEventListener() {}};
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
