"""Tests for sous-chef SKILL.md content invariants."""

from __future__ import annotations

import pytest

from autoskillit.cli._prompts import _read_full_sous_chef

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


def test_sous_chef_prohibits_raw_recipe_reading():
    content = _read_full_sous_chef()
    assert "NEVER read recipe YAML files from the filesystem" in content


def test_sous_chef_content_no_frontmatter():
    """_read_full_sous_chef must strip YAML frontmatter metadata."""
    content = _read_full_sous_chef()
    assert content, "_read_full_sous_chef returned empty string"
    assert not content.startswith("---"), "Frontmatter delimiter still present"
    assert "uses_capabilities:" not in content, "Frontmatter field leaked into content"
    assert "activate_deps:" not in content, "Dependency authority leaked into content"


def test_sous_chef_documents_exhaustive_recipe_section_reconstruction() -> None:
    content = _read_full_sous_chef()

    for required in (
        "recipe_pull",
        "recipe_flow",
        "initialization_id",
        "required_sections",
        "flow_records",
        "entrypoint",
        "complete_recipe_initialization",
        "completion receipt",
        "descriptor_version",
        "schema_version",
        "flow_schema_version",
        "pagination_version",
        "section_registry_sha256",
        "pagination_policy_sha256",
        "section_sha256",
        "page_plan_sha256",
        "continuation",
        "raw-text",
        "json-array-page",
        "json-scalar-page",
        "json-element-fragment",
        "json.loads",
        "next_part",
    ):
        assert required in content
    assert "unknown pagination_version" in content
    assert "unknown content_format" in content
    assert content.index("flow_records") < content.index("entrypoint named-step")
    assert content.index("entrypoint named-step") < content.index("complete_recipe_initialization")


def test_sous_chef_requires_real_result_receipt_acknowledgement() -> None:
    content = _read_full_sous_chef()

    assert "## RUN_SKILL COMPLETION HANDSHAKE — MANDATORY" in content
    assert "actual tool/task notification" in content
    assert "NEVER synthesize" in content
    assert "<bg_result>" in content
    assert "complete_run_skill_result" in content
    assert 'receipt_id="<exact delivered receipt_id>"' in content


def test_sous_chef_requires_progressive_segment_consumption() -> None:
    content = _read_full_sous_chef()

    for required in (
        "recipe_segment",
        "body_sha256",
        "segment-scoped",
        "pull_closure",
        "recipe_segment_post_effect_delivery_failure",
        "operation already ran",
        "do not repeat it",
        "full recipe horizon",
    ):
        assert required in content


def test_sous_chef_preserves_attested_skill_input_shape_and_falsey_defaults() -> None:
    content = _read_full_sous_chef()

    assert (
        "For structured child inputs, select "
        "`recipe_execution.skill_input_shapes[step_name]` and\n"
        "initialize `skill_inputs` with exactly its ordered keys; replace available "
        "values in place;\n"
        "for unavailable context, copy a value only from that key's advertised\n"
        "`absence_values` entry. Test key presence rather than truthiness so "
        '`""`, `0`, and\n'
        "`False` are forwarded verbatim; never delete or invent a key."
    ) in content
