"""Unified recipe finalization: decision, shaping, and transactional commit.

``_response.py`` owns the ``FinalizedRecipeResponse`` carrier, ``_finalize.py``
owns ``finalize_recipe_delivery``, and ``_completion.py`` owns the independent
receipt-commit / lifecycle-close-out / response-backstop call chain
(``enforce_recipe_resource_response`` -> ``complete_finalized_recipe_response``
-> ``_complete_kitchen_serving_transition``) — split out to stay under the
REQ-CNST-010 750-line cap after this package's path lengthened (issue #4673
side-fix). This ``__init__.py`` is a pure re-export facade (P14-2) — no
logic lives here.
"""

from __future__ import annotations

from autoskillit._recipe_delivery_framing import (
    RECIPE_BODY_END,
    RECIPE_BODY_START,
    RECIPE_COMPLETION_SENTINEL,
)
from autoskillit.core import (
    RECIPE_ARTIFACT_DESCRIPTOR_VERSION,
    RECIPE_ARTIFACT_SCHEMA_VERSION,
    RecipeArtifactGeneration,
)
from autoskillit.server.recipe._recipe_artifact import (
    RecipeArtifactError,
    RecipeArtifactSchemaError,
    build_canonical_recipe_artifact_payload,
    build_recipe_flow_generation,
    load_recipe_artifact,
    persist_recipe_artifact,
    prepare_recipe_delivery_generation,
    recipe_pull_producers,
    recipe_recreation_producers,
    retire_recipe_artifacts,
)
from autoskillit.server.recipe._recipe_delivery._completion import (
    complete_finalized_recipe_response,
    enforce_recipe_resource_response,
)
from autoskillit.server.recipe._recipe_delivery._finalize import (
    document_recipe_delivery_contract,
    finalize_recipe_delivery,
)
from autoskillit.server.recipe._recipe_delivery._response import FinalizedRecipeResponse
from autoskillit.server.recipe._recipe_delivery_helpers import (
    _attested_render,  # noqa: F401  (not public API; reachable off the flat pre-#4673 module)
    _initialization_requirements,  # noqa: F401  (mock.patch reachability)
    initialize_host_client_attestation,
    validate_compiled_recipe_delivery_budget,
    validate_recipe_exemption_fitness,
)
from autoskillit.server.recipe._recipe_initialization import (
    build_recipe_envelope,  # noqa: F401  (not public API; reachable off the flat pre-#4673 module)
)

__all__ = [
    "FinalizedRecipeResponse",
    "RECIPE_ARTIFACT_DESCRIPTOR_VERSION",
    "RECIPE_ARTIFACT_SCHEMA_VERSION",
    "RECIPE_BODY_END",
    "RECIPE_BODY_START",
    "RECIPE_COMPLETION_SENTINEL",
    "RecipeArtifactError",
    "RecipeArtifactGeneration",
    "RecipeArtifactSchemaError",
    "build_canonical_recipe_artifact_payload",
    "build_recipe_flow_generation",
    "complete_finalized_recipe_response",
    "document_recipe_delivery_contract",
    "enforce_recipe_resource_response",
    "finalize_recipe_delivery",
    "initialize_host_client_attestation",
    "load_recipe_artifact",
    "persist_recipe_artifact",
    "prepare_recipe_delivery_generation",
    "recipe_pull_producers",
    "recipe_recreation_producers",
    "retire_recipe_artifacts",
    "validate_compiled_recipe_delivery_budget",
    "validate_recipe_exemption_fitness",
]
