"""Receipt-bearing terminal page contracts."""

import pytest

from autoskillit.core import (
    RECIPE_ARTIFACT_DESCRIPTOR_VERSION,
    RECIPE_ARTIFACT_SCHEMA_VERSION,
    RECIPE_FLOW_SCHEMA_VERSION,
    RecipeArtifactGeneration,
)
from autoskillit.server._recipe_initialization import recipe_initialization_receipt

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]


def _artifact() -> RecipeArtifactGeneration:
    digest = "sha256:" + ("a" * 64)
    return RecipeArtifactGeneration(
        producer_tool="open_kitchen",
        recipe_name="contract",
        descriptor_version=RECIPE_ARTIFACT_DESCRIPTOR_VERSION,
        schema_version=RECIPE_ARTIFACT_SCHEMA_VERSION,
        payload_sha256=digest,
        artifact_blob_sha256=digest,
        artifact_blob_size_bytes=1,
        body_sha256=digest,
        body_size_bytes=1,
        flow_schema_version=RECIPE_FLOW_SCHEMA_VERSION,
        flow_sha256=digest,
        flow_size_bytes=1,
        flow_record_count=1,
    )


def test_completion_receipt_is_bound_to_terminal_content() -> None:
    first = recipe_initialization_receipt(
        "initialization", _artifact(), content_sha256="sha256:" + ("b" * 64)
    )
    second = recipe_initialization_receipt(
        "initialization", _artifact(), content_sha256="sha256:" + ("c" * 64)
    )
    assert first != second
