"""Contract tests for the observability deck's self-contained HTML document."""

import base64
import hashlib
import json
import re
import subprocess
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zipfile import ZipFile

import pytest

from autoskillit.core import pkg_root
from autoskillit.report import render_deck
from autoskillit.report._analysis import _analysis_payload
from autoskillit.report.deck import _html as deck_html
from autoskillit.report.deck import _payload as deck_payload_module
from autoskillit.report.deck import build_deck_payload
from autoskillit.report.deck import render_deck as render_deck_from_deck
from autoskillit.report.deck._html import render_deck_html
from autoskillit.report.deck._registry import DECK_VIEWS
from tests.report._fixtures import DECK_GENERATED_AT, session_row, subagent_row, token_measure

pytestmark = [pytest.mark.medium]


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


def _saved_analysis_payload(
    payload: dict[str, Any],
    *,
    findings: list[dict[str, Any]],
    evidence: dict[str, Any],
) -> dict[str, Any]:
    packet = {
        "payload": payload,
        "input_fingerprint": "frozen-input-fingerprint",
        "coverage": {
            "limitation": (
                "Coverage includes retained report-index sources, "
                "not complete historical coverage."
            ),
            "populations": {},
        },
        "cohort": {"change_id": None, "reason": "No rollout cohort applied.", "comparisons": []},
        "evidence": evidence,
    }
    interpretation = {
        "interpreter": {"role": "research-synthesizer", "model": "test-model", "prompt": None},
        "findings": findings,
    }
    return _analysis_payload(packet, interpretation, 1_791_504_000_123)


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
    expected = [
        "'sha256-"
        + base64.b64encode(hashlib.sha256(source.encode("utf-8")).digest()).decode("ascii")
        + "'"
        for attrs, source in collector.scripts
        if "type" not in attrs
    ]
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
    payload = build_deck_payload(rows, generated_at=DECK_GENERATED_AT, index_schema_version=1)
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


def test_saved_analysis_findings_are_inert_and_offline(
    deck_asset: Any, deck_payload: dict[str, Any]
) -> None:
    hostile = "</script><img src=x onerror=alert(1)>"
    evidence = {
        "evidence-1": {
            "view_id": "cohort",
            "source": "session",
            "measurement": {"note": hostile, "value": 10.5, "unit": "tokens"},
        }
    }
    payload = _saved_analysis_payload(
        deck_payload,
        findings=[{"view_id": "cohort", "text": hostile, "evidence_ids": ["evidence-1"]}],
        evidence=evidence,
    )
    html = render_deck_html(payload)
    collector = _collect(html)
    data_script = next(
        source for attrs, source in collector.scripts if attrs.get("id") == "deck-data"
    )

    assert json.loads(data_script) == payload
    assert payload["analysis"]["findings"][0]["text"] == hostile
    assert hostile not in html
    assert not any(tag == "img" for tag, _ in collector.elements)
    assert all("src" not in attrs for _, attrs in collector.elements)
    assert all(
        attrs.get("href") is None or attrs["href"].startswith("#")
        for tag, attrs in collector.elements
        if tag == "a"
    )
    assert "report-index/rows.jsonl" not in html
    assert all(
        name not in html for name in ("analysis.json", "findings.json", "measurements.json")
    )
    actual_scripts = [source for attrs, source in collector.scripts if "type" not in attrs]
    view_assets = [
        deck_asset(view.script)
        for view in DECK_VIEWS
        if view.planned_issue is None and view.script is not None
    ]
    assert actual_scripts == [deck_asset("core.js"), deck_asset("shell.js"), *view_assets]


def test_empty_saved_analysis_keeps_explicit_no_observations_finding() -> None:
    payload = build_deck_payload([], generated_at=DECK_GENERATED_AT, index_schema_version=1)
    payload = _saved_analysis_payload(
        payload,
        findings=[
            {
                "view_id": "cohort",
                "text": "No observations in the frozen corpus.",
                "evidence_ids": [],
            }
        ],
        evidence={},
    )

    collector = _collect(render_deck_html(payload))
    data_script = next(
        source for attrs, source in collector.scripts if attrs.get("id") == "deck-data"
    )

    assert json.loads(data_script)["analysis"]["findings"] == payload["analysis"]["findings"]


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


def test_frozen_render_bundle_avoids_live_assets_and_registry(
    monkeypatch: pytest.MonkeyPatch, deck_payload: dict[str, Any]
) -> None:
    bundle = deck_html.load_render_bundle()
    assert set(bundle) == {"template", "css", "scripts"}
    assert [asset["name"] for asset in bundle["scripts"]] == list(deck_html.deck_script_assets())
    assert all(set(asset) == {"name", "text"} for asset in bundle["scripts"])
    expected_html = render_deck_html(deck_payload)
    expected_scripts = [asset["text"] for asset in bundle["scripts"]]

    def unexpected_asset_read(rel: str) -> str:
        pytest.fail(f"render bundle unexpectedly read live asset {rel!r}")

    monkeypatch.setattr(deck_html, "_asset_text", unexpected_asset_read)
    monkeypatch.setattr(deck_html, "DECK_VIEWS", ())

    rendered = render_deck_html(deck_payload, render_bundle=bundle)
    assert rendered == expected_html

    collector = _collect(rendered)
    actual_scripts = [source for attrs, source in collector.scripts if "type" not in attrs]
    policy = next(
        attrs["content"]
        for tag, attrs in collector.elements
        if tag == "meta" and attrs.get("http-equiv", "").lower() == "content-security-policy"
    )
    expected_hashes = [
        "'sha256-"
        + base64.b64encode(hashlib.sha256(source.encode("utf-8")).digest()).decode("ascii")
        + "'"
        for source in expected_scripts
    ]
    script_tokens = next(
        directive.strip().split()[1:]
        for directive in policy.split(";")
        if directive.strip().startswith("script-src ")
    )

    assert actual_scripts == expected_scripts
    assert sorted(script_tokens) == sorted(expected_hashes)


def test_frozen_render_bundle_rejects_closing_script_tag(
    deck_payload: dict[str, Any],
) -> None:
    bundle = deck_html.load_render_bundle()
    bundle["scripts"][0]["text"] = "const bad = '</SCRIPT>'"

    with pytest.raises(ValueError, match="closing script tag"):
        render_deck_html(deck_payload, render_bundle=bundle)


def test_embedded_json_uses_canonical_mapping_order() -> None:
    first = {"z": {"b": 2, "a": 1}, "a": 0}
    second = {"a": 0, "z": {"a": 1, "b": 2}}

    assert deck_html.embed_json(first) == deck_html.embed_json(second)
    assert deck_html.embed_json(first) == '{"a":0,"z":{"a":1,"b":2}}'


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


def test_built_wheel_contains_registered_view_scripts(tmp_path: Path) -> None:
    # REQ-108/REQ-158: isolate the plan's wheel-check directory per test worker.
    wheel_out = tmp_path / "wheel-check"
    result = subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(wheel_out), "--offline"],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, (result.stdout + result.stderr)[-4000:]
    wheels = list(wheel_out.glob("*.whl"))
    assert len(wheels) == 1, f"Expected one built wheel, found {wheels}"
    with ZipFile(wheels[0]) as wheel:
        required = {
            f"autoskillit/assets/deck/{view.script}"
            for view in DECK_VIEWS
            if view.planned_issue is None and view.script is not None
        }
        missing = required - set(wheel.namelist())
        assert not missing, f"Built wheel is missing view scripts: {sorted(missing)}"


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

    assert (
        render_deck(
            deck_rows,
            request_rows=(),
            tool_rows=(),
            subagent_rows=(),
            generated_at=DECK_GENERATED_AT,
            index_schema_version=1,
        )
        == expected
    )
    assert (
        render_deck_from_deck(
            deck_rows,
            request_rows=(),
            tool_rows=(),
            subagent_rows=(),
            generated_at=DECK_GENERATED_AT,
            index_schema_version=1,
        )
        == expected
    )


def test_render_deck_forwards_child_facts_and_embeds_definitions_as_literal_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    role = "audit-impl-slice-auditor"
    parent = session_row(
        "parent",
        session_id="native-parent",
        time_ms=1_791_000_000_000,
        skill="planner",
        recipe="planner",
        step="inspect",
        level="skill",
        input_tokens=token_measure(12),
        output_tokens=token_measure(3),
    )
    child = subagent_row(
        "native-child",
        parent=parent,
        role=role,
        skill="planner",
        provider="anthropic",
        input_tokens=20,
        output_tokens=4,
        cache_read_tokens=2,
        cache_write_tokens=1,
        tool_counts={"Bash": 3944, "Read": 6},
    )
    hostile_body = "Run git show. </script><img src=x onerror=alert(1)>"
    definition = SimpleNamespace(
        name=role,
        description="Inspect a committed slice.",
        body=hostile_body,
        tools=("Bash", "Read"),
        model="test-claude-model",
        reader_tools=(),
        codex=SimpleNamespace(model="test-codex-model"),
    )
    monkeypatch.setattr(
        deck_payload_module,
        "load_bundled_agent_definitions",
        lambda: (definition,),
        raising=False,
    )
    html = deck_html.render_deck(
        [parent],
        request_rows=[{"request_id": "unlinked", "agent_name": role}],
        tool_rows=[{"agent_name": role, "tool_name": "Bash", "count": 999_999}],
        subagent_rows=[child],
        generated_at=DECK_GENERATED_AT,
        index_schema_version=1,
    )
    collector = _collect(html)
    data = next(source for attrs, source in collector.scripts if attrs.get("id") == "deck-data")
    payload = json.loads(data)

    assert payload["prepared"]["definitions"][role]["body"] == hostile_body
    assert any(row[0] == role for row in payload["tables"]["roles"]["rows"])
    assert "\\u003c/script\\u003e" in data
    assert not any(tag == "img" for tag, _ in collector.elements)
