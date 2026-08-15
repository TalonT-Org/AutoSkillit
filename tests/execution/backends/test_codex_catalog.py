"""Regression tests for shared Codex model-catalog projection."""

from __future__ import annotations

import json

import pytest

from autoskillit.execution.backends._codex_catalog import project_codex_catalog

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]

_READER_MODEL = "gpt-5.6-luna"
_READER_REASONING_EFFORT = "xhigh"


def _installed_catalog() -> dict[str, object]:
    return {
        "models": [
            {
                "slug": "gpt-5.6-sol",
                "tool_mode": "code_mode",
                "apply_patch_tool_type": "freeform",
                "sentinel": {"preserved": True},
            },
            {
                "slug": _READER_MODEL,
                "tool_mode": "code_mode_only",
                "apply_patch_tool_type": "freeform",
                "supported_reasoning_levels": [
                    {"effort": "high", "description": "High"},
                    {"effort": _READER_REASONING_EFFORT, "description": "Extra high"},
                ],
                "reader_metadata": {"preserved": True},
            },
        ],
        "metadata": {"catalog": "installed", "schema_version": 7},
    }


def _catalog_bytes(catalog: object) -> bytes:
    return json.dumps(catalog).encode()


def test_reader_projection_preserves_the_complete_installed_catalog() -> None:
    installed = _installed_catalog()

    projection = project_codex_catalog(
        _catalog_bytes(installed),
        expected_model=_READER_MODEL,
        expected_reasoning_effort=_READER_REASONING_EFFORT,
    )
    projected = json.loads(projection.canonical_projected_bytes)

    expected = json.loads(_catalog_bytes(installed))
    expected_reader = expected["models"][1]
    expected_reader["tool_mode"] = "direct"
    expected_reader["apply_patch_tool_type"] = None
    assert projected == expected
    assert projected["models"][0] == installed["models"][0]
    assert projected["models"][1]["tool_mode"] == "direct"
    assert projected["models"][1]["apply_patch_tool_type"] is None
    assert projection.bundled_sha256.startswith("sha256:")
    assert projection.projected_sha256.startswith("sha256:")
    assert projection.bundled_sha256 != projection.projected_sha256


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (b'{"models":', "malformed"),
        (b'{"models":[],"models":[]}', "malformed"),
        (b'{"models":[],"revision":NaN}', "malformed"),
        (_catalog_bytes({"models": None}), "no model list"),
        (_catalog_bytes({"models": [{"slug": 1}]}), "malformed model entry"),
    ],
)
def test_catalog_schema_fails_closed(raw: bytes, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        project_codex_catalog(
            raw,
            expected_model=_READER_MODEL,
            expected_reasoning_effort=_READER_REASONING_EFFORT,
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("missing_model", "exactly one"),
        ("duplicate_model", "exactly one"),
        ("malformed_reasoning", "malformed supported reasoning"),
        ("missing_effort", "does not advertise"),
        ("direct_tool_mode", "bundled tool_mode"),
        ("null_apply_patch", "bundled apply_patch_tool_type"),
    ],
)
def test_reader_projection_rejects_incomplete_or_preprojected_surfaces(
    mutation: str,
    message: str,
) -> None:
    catalog = _installed_catalog()
    models = catalog["models"]
    assert isinstance(models, list)
    reader = models[1]
    assert isinstance(reader, dict)
    if mutation == "missing_model":
        models.pop()
    elif mutation == "duplicate_model":
        models.append(dict(reader))
    elif mutation == "malformed_reasoning":
        reader["supported_reasoning_levels"] = [{"effort": 1}]
    elif mutation == "missing_effort":
        reader["supported_reasoning_levels"] = [{"effort": "high"}]
    elif mutation == "direct_tool_mode":
        reader["tool_mode"] = "direct"
    else:
        reader["apply_patch_tool_type"] = None

    with pytest.raises(ValueError, match=message):
        project_codex_catalog(
            _catalog_bytes(catalog),
            expected_model=_READER_MODEL,
            expected_reasoning_effort=_READER_REASONING_EFFORT,
        )
