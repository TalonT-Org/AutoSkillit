"""Assemble the deck's first-party assets and escaped index payload."""

import base64
import hashlib
import json
from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any

import regex as re

from autoskillit.core import pkg_root

from ._payload import build_deck_payload
from ._registry import DECK_VIEWS


def _asset_text(rel: str) -> str:
    return (pkg_root() / "assets" / "deck" / rel).read_text(encoding="utf-8")


def deck_script_assets() -> tuple[str, ...]:
    return (
        "core.js",
        "shell.js",
        *(v.script for v in DECK_VIEWS if v.planned_issue is None and v.script is not None),
    )


def embed_json(payload: Mapping[str, Any]) -> str:
    return (
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        .replace("&", r"\u0026")
        .replace("<", r"\u003c")
        .replace(">", r"\u003e")
    )


def script_hash(source: str) -> str:
    digest = hashlib.sha256(source.encode("utf-8")).digest()
    return "'sha256-" + base64.b64encode(digest).decode("ascii") + "'"


def render_deck_html(payload: Mapping[str, Any]) -> str:
    scripts = [_asset_text(rel) for rel in deck_script_assets()]
    css = _asset_text("deck.css")
    if any("</script" in script.lower() for script in scripts):
        raise ValueError("script asset contains a closing script tag")
    if "</style" in css.lower():
        raise ValueError("CSS asset contains a closing style tag")
    csp = (
        "default-src 'none'; script-src "
        + " ".join(script_hash(s) for s in scripts)
        + "; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"
    )
    values = {
        "csp": csp,
        "css": css,
        "data": embed_json(payload),
        "scripts": "\n".join("<script>" + s + "</script>" for s in scripts),
    }
    template = _asset_text("deck.html")
    pattern = r"<!--deck:(\w+)-->"
    if sorted(re.findall(pattern, template)) != sorted(values):
        raise ValueError("deck template must contain each insertion marker exactly once")
    return re.sub(pattern, lambda m: values[m.group(1)], template)


def render_deck(
    session_rows: Iterable[Mapping[str, Any]], *, generated_at: datetime, index_schema_version: int
) -> str:
    return render_deck_html(
        build_deck_payload(
            session_rows, generated_at=generated_at, index_schema_version=index_schema_version
        )
    )
