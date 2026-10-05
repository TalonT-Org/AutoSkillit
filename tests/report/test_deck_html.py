"""Contract tests for the observability deck's self-contained HTML document."""

import json
import re
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any

import pytest

from autoskillit.core import pkg_root
from autoskillit.report import render_deck
from autoskillit.report.deck import _html as deck_html
from autoskillit.report.deck import build_deck_payload
from autoskillit.report.deck import render_deck as render_deck_from_deck
from autoskillit.report.deck._html import render_deck_html, script_hash
from autoskillit.report.deck._registry import DECK_VIEWS

pytestmark = [pytest.mark.small]

GEN = datetime(2026, 10, 4, tzinfo=UTC)


class _HTMLCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.elements: list[tuple[str, dict[str, str | None]]] = []
        self.scripts: list[tuple[dict[str, str | None], str]] = []
        self._script_attrs: dict[str, str | None] | None = None
        self._script_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        self.elements.append((tag, attributes))
        if tag == "script":
            self._script_attrs = attributes
            self._script_parts = []

    def handle_data(self, data: str) -> None:
        if self._script_attrs is not None:
            self._script_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._script_attrs is not None:
            self.scripts.append((self._script_attrs, "".join(self._script_parts)))
            self._script_attrs = None
            self._script_parts = []


def _collect(html: str) -> _HTMLCollector:
    collector = _HTMLCollector()
    collector.feed(html)
    collector.close()
    return collector


def test_rendered_deck_has_no_network_dependency(deck_payload: dict[str, Any]) -> None:
    html = render_deck_html(deck_payload)
    collector = _collect(html)

    assert all("src" not in attrs for _, attrs in collector.elements)
    assert not {
        "link",
        "img",
        "iframe",
        "object",
        "embed",
        "audio",
        "video",
        "source",
    }.intersection(tag for tag, _ in collector.elements)
    assert all(
        attrs.get("href") is not None and attrs["href"].startswith("#")
        for tag, attrs in collector.elements
        if tag == "a" and "href" in attrs
    )

    for forbidden in (
        "@import",
        "fetch(",
        "XMLHttpRequest",
        "WebSocket",
        "EventSource",
        "sendBeacon",
        "import(",
        "new Worker",
    ):
        assert forbidden not in html
    assert all(
        target.strip().startswith("#") for target in re.findall(r"url\(([^)]*)\)", html, re.I)
    )
    assert re.findall(r"https?://[^\s\"'<>)]*", html) == ["http://www.w3.org/2000/svg"]


def test_csp_hashes_each_executable_script(deck_payload: dict[str, Any]) -> None:
    collector = _collect(render_deck_html(deck_payload))
    policies = [
        attrs["content"]
        for tag, attrs in collector.elements
        if tag == "meta" and attrs.get("http-equiv", "").lower() == "content-security-policy"
    ]

    assert len(policies) == 1
    policy = policies[0]
    assert policy is not None and policy.startswith("default-src 'none';")
    directives = {
        directive.strip().split()[0]: directive.strip().split()[1:]
        for directive in policy.split(";")
        if directive.strip()
    }
    script_tokens = directives["script-src"]
    expected = [script_hash(source) for attrs, source in collector.scripts if "type" not in attrs]
    assert sorted(script_tokens) == sorted(expected)
    assert "'unsafe-inline'" not in script_tokens


def test_embedded_json_escapes_markup_and_round_trips(deck_rows: list[dict[str, Any]]) -> None:
    rows = [
        *deck_rows,
        {
            **deck_rows[0],
            "key": "s5",
            "skill": "</script><img src=x onerror=alert(1)><!--deck:css-->",
        },
    ]
    payload = build_deck_payload(rows, generated_at=GEN, index_schema_version=1)
    html = render_deck_html(payload)
    collector = _collect(html)
    data = next(source for attrs, source in collector.scripts if attrs.get("id") == "deck-data")

    assert json.loads(data) == payload
    assert not any(tag == "img" for tag, _ in collector.elements)
    assert all(
        marker not in html
        for marker in (
            "<!--deck:csp-->",
            "<!--deck:css-->",
            "<!--deck:data-->",
            "<!--deck:scripts-->",
        )
    )


def test_rendered_scripts_follow_core_shell_and_registry_order(
    deck_asset: Any, deck_payload: dict[str, Any]
) -> None:
    collector = _collect(render_deck_html(deck_payload))
    actual = [source for attrs, source in collector.scripts if "type" not in attrs]
    view_assets = [
        deck_asset(view.script)
        for view in DECK_VIEWS
        if view.planned_issue is None and view.script is not None
    ]

    assert actual == [deck_asset("core.js"), deck_asset("shell.js"), *view_assets]


@pytest.mark.parametrize("invalid_asset", ["script", "css", "missing_marker", "duplicate_marker"])
def test_render_deck_html_rejects_unsafe_assets_and_template_markers(
    invalid_asset: str,
    monkeypatch: pytest.MonkeyPatch,
    deck_asset: Any,
    deck_payload: dict[str, Any],
) -> None:
    if invalid_asset == "script":
        target, replacement = "core.js", "const bad = '</SCRIPT>'"
    elif invalid_asset == "css":
        target, replacement = "deck.css", "x { color: red; </style> }"
    elif invalid_asset in {"missing_marker", "duplicate_marker"}:
        target = "deck.html"
        template = deck_asset(target)
        if invalid_asset == "missing_marker":
            replacement = template.replace("<!--deck:css-->", "", 1)
        else:
            replacement = template.replace(
                "<!--deck:data-->", "<!--deck:data--><!--deck:data-->", 1
            )
    else:
        raise AssertionError(f"unhandled test case: {invalid_asset}")

    def asset_text(rel: str) -> str:
        return replacement if rel == target else deck_asset(rel)

    monkeypatch.setattr(deck_html, "_asset_text", asset_text)
    with pytest.raises(ValueError):
        render_deck_html(deck_payload)


def test_deck_asset_directory_contains_only_registered_shell_assets() -> None:
    assets = pkg_root() / "assets" / "deck"
    actual = {path.relative_to(assets).as_posix() for path in assets.rglob("*") if path.is_file()}
    view_assets = {
        view.script
        for view in DECK_VIEWS
        if view.planned_issue is None and view.script is not None
    }

    assert actual == {"deck.html", "deck.css", "core.js", "shell.js"} | view_assets


def test_embedded_cohort_chips_cover_all_states(deck_payload: dict[str, Any]) -> None:
    collector = _collect(render_deck_html(deck_payload))
    data = next(source for attrs, source in collector.scripts if attrs.get("id") == "deck-data")
    chips = json.loads(data)["chips"]["cohort"]

    assert {chip["state"] for facet in chips.values() for chip in facet} >= {
        "live",
        "struck",
        "absent",
    }


def test_render_deck_facades_match_html_wrapper(
    deck_rows: list[dict[str, Any]], deck_payload: dict[str, Any]
) -> None:
    expected = render_deck_html(deck_payload)

    assert render_deck(deck_rows, generated_at=GEN, index_schema_version=1) == expected
    assert render_deck_from_deck(deck_rows, generated_at=GEN, index_schema_version=1) == expected
