"""Browser-free renderer contracts for the observability deck's prepared views."""

from typing import Any

import pytest
from py_mini_racer import MiniRacer

pytestmark = [pytest.mark.small]

VIEW_IDS = ("spend", "efficiency", "skill", "role")

_RENDERER_HARNESS = r"""
globalThis.__deckViews = {};
globalThis.DeckShell = {
  registerView(id, render) { __deckViews[id] = render; }
};
globalThis.location = {hash: "#/cohort"};
globalThis.window = {location, addEventListener() {}};

function deckNode(tag, attrs = {}, children = null) {
  const node = {tag, attrs: {...attrs}, children: [], events: {}};
  node.appendChild = child => { node.children.push(child); return child; };
  node.addEventListener = (name, callback) => { node.events[name] = callback; };
  if (Array.isArray(children)) node.children.push(...children);
  else if (children !== null && children !== undefined) node.children.push(children);
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

function deckSnapshot(node) {
  if (node === null || node === undefined || typeof node !== "object") return node;
  return {
    tag: node.tag,
    attrs: node.attrs,
    text: deckText(node),
    links: deckLinks(node),
    review: (() => {
      const control = deckFind(node, child =>
        child.attrs && child.attrs["data-review-toggle"] !== undefined);
      return control ? {attrs: control.attrs, text: deckText(control)} : null;
    })()
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
      availabilityCell: measure => deckNode("span", {"data-state": measure.state},
        measure.state === "measured" ? String(measure.value) : measure.state),
      barChart: (items, options) => deckNode("svg", {"aria-label": options.label},
        JSON.stringify(items))
    };
    location.hash = DeckCore.encodeRoute(route, cohortKeys);
    globalThis.__deckLastTree = __deckViews[id](context);
    return deckSnapshot(__deckLastTree);
  },
  clickReview() {
    const control = deckFind(__deckLastTree,
      child => child.attrs && child.attrs["data-review-toggle"] !== undefined);
    if (!control || !control.events.click) throw new Error("review toggle has no click handler");
    control.events.click();
    return location.hash;
  }
};
"""


def _load_renderers(deck_asset: Any) -> MiniRacer:
    context = MiniRacer()
    context.eval(deck_asset("core.js"))
    context.eval(_RENDERER_HARNESS)
    for view_id in VIEW_IDS:
        context.eval(deck_asset(f"views/{view_id}.js"))
    return context


def _view_context(view_id: str, *, definitions_available: bool = True) -> dict[str, Any]:
    definition_state = "available" if definitions_available else "unavailable"
    roles = ("reader", "reviewer")
    definitions = {
        role: {
            "state": definition_state,
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
        role_view = context.call("DeckTest.render", "role", _view_context("role"))
        assert "L0" in role_view["text"]
        assert "spawning skill level" in role_view["text"].lower()
        assert "observed-model" in role_view["text"]
        assert "Claude Code declared tools" in role_view["text"]
        assert "Codex read-only tools" in role_view["text"]
    finally:
        context.close()


def test_review_toggle_requires_every_contributor_definition_and_resets_route(
    deck_asset: Any,
) -> None:
    context = _load_renderers(deck_asset)
    try:
        available = context.call("DeckTest.render", "efficiency", _view_context("efficiency"))
        assert available["review"] is not None
        assert "disabled" not in available["review"]["attrs"]
        assert available["text"].index("definition-first marker") < available["text"].index(
            "Show reviewable signals"
        )

        unavailable = context.call(
            "DeckTest.render",
            "efficiency",
            _view_context("efficiency", definitions_available=False),
        )
        assert unavailable["review"] is not None
        assert "disabled" in unavailable["review"]["attrs"]
        assert "definition" in unavailable["text"].lower()

        context.call("DeckTest.render", "efficiency", _view_context("efficiency"))
        enabled_hash = context.call("DeckTest.clickReview")
        enabled_route = context.call("DeckCore.decodeRoute", enabled_hash, "cohort")
        assert enabled_route["params"].get("review")
        assert enabled_route["params"].get("harness") == ["codex"]
        assert enabled_route["params"].get("provider") == ["openai"]

        context.call(
            "DeckTest.render",
            "efficiency",
            {**_view_context("efficiency"), "route": enabled_route},
        )
        reset_hash = context.call("DeckTest.clickReview")
        reset_route = context.call("DeckCore.decodeRoute", reset_hash, "cohort")
        assert "review" not in reset_route["params"]
        assert reset_route["params"].get("harness") == ["codex"]
        assert reset_route["params"].get("provider") == ["openai"]
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
