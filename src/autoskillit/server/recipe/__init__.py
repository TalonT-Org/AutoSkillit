"""Recipe artifact, delivery, execution, generation, and initialization.

Owns compiled-recipe generation and persistence (``_recipe_artifact``,
``_recipe_generation``), transactional finalization and its decision-support
helpers (``_recipe_delivery``, ``_recipe_delivery_helpers``,
``_recipe_segment_delivery``), server-owned execution and initialization
commits (``_recipe_execution``, ``_recipe_initialization``), and section
pagination (``_recipe_section_pagination``, ``_recipe_section_planning``,
verified against ``section``'s final invariant proof).
"""
