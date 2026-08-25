"""Tests for response-backstop exemption registry + install-site registry."""

from __future__ import annotations

import hashlib
import json
import operator

import pytest

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_response_backstop_exemption_def_namedtuple_fields() -> None:
    from autoskillit.core import ResponseBackstopExemptionDef

    definition = ResponseBackstopExemptionDef(
        max_chars=1,
        max_utf8_bytes=2,
        measurement_id="measurement-v1",
    )
    assert definition._fields == ("max_chars", "max_utf8_bytes", "measurement_id")
    assert definition.max_chars == 1
    assert definition.max_utf8_bytes == 2
    assert definition.measurement_id == "measurement-v1"


def test_response_backstop_exemption_registry_is_closed_and_pinned() -> None:
    from autoskillit.core import (
        RESPONSE_BACKSTOP_EXEMPTION_REGISTRY,
        ResponseBackstopExemptionDef,
    )

    assert RESPONSE_BACKSTOP_EXEMPTION_REGISTRY == {
        "get_recipe_section": ResponseBackstopExemptionDef(
            max_chars=195_000,
            max_utf8_bytes=195_000,
            measurement_id="bundled-recipes-all-modes-2026-08-09/get-recipe-section",
        ),
        "load_recipe": ResponseBackstopExemptionDef(
            max_chars=195_000,
            max_utf8_bytes=195_000,
            measurement_id="bundled-recipes-all-modes-2026-07-22/load-recipe",
        ),
        "open_kitchen": ResponseBackstopExemptionDef(
            max_chars=195_000,
            max_utf8_bytes=195_000,
            measurement_id="bundled-recipes-all-modes-2026-07-22/open-kitchen",
        ),
    }
    with pytest.raises(TypeError):
        operator.setitem(
            RESPONSE_BACKSTOP_EXEMPTION_REGISTRY,
            "mutated",
            RESPONSE_BACKSTOP_EXEMPTION_REGISTRY["load_recipe"],
        )


def test_response_backstop_exemption_registry_digest_is_canonical() -> None:
    from autoskillit.core import (
        RESPONSE_BACKSTOP_EXEMPTION_REGISTRY,
        RESPONSE_BACKSTOP_EXEMPTION_REGISTRY_DIGEST,
    )

    canonical = {
        tool_name: definition._asdict()
        for tool_name, definition in sorted(RESPONSE_BACKSTOP_EXEMPTION_REGISTRY.items())
    }
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    assert hashlib.sha256(payload.encode("ascii")).hexdigest() == (
        RESPONSE_BACKSTOP_EXEMPTION_REGISTRY_DIGEST
    )
    assert (
        RESPONSE_BACKSTOP_EXEMPTION_REGISTRY_DIGEST
        == "669328c03372e174282f498e17c682c6e8d74bd68e9b8086e400848777061f66"
    )


def test_response_backstop_exemption_registry_public_gateways() -> None:
    import autoskillit.core as core
    import autoskillit.core.types as core_types

    for module in (core, core_types):
        assert module.RESPONSE_BACKSTOP_EXEMPTION_REGISTRY
        assert module.RESPONSE_BACKSTOP_EXEMPTION_REGISTRY_DIGEST
        assert module.ResponseBackstopExemptionDef
        assert not hasattr(module, "OPEN_KITCHEN_OUTPUT_BUDGET_BYTES")


def test_recipe_execution_install_site_registry_digest_is_canonical() -> None:
    from autoskillit.core import (
        RECIPE_EXECUTION_INSTALL_SITE_REGISTRY,
        RECIPE_EXECUTION_INSTALL_SITE_REGISTRY_DIGEST,
    )

    canonical = {
        site: definition._asdict()
        for site, definition in sorted(RECIPE_EXECUTION_INSTALL_SITE_REGISTRY.items())
    }
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    assert hashlib.sha256(payload.encode("ascii")).hexdigest() == (
        RECIPE_EXECUTION_INSTALL_SITE_REGISTRY_DIGEST
    )
    assert (
        RECIPE_EXECUTION_INSTALL_SITE_REGISTRY_DIGEST
        == "7aec26971d5946cf50a32c9da3b5c1db10046ec14045881b0339f91993888fe4"
    )
