"""Backward-compat shim for _delivery_bounds — see core.io.delivery_bounds."""

from autoskillit.core.io.delivery_bounds import (
    recipe_delivery_request_digest,
    resolve_general_output_token_limit,
    resolve_recipe_delivery_decision,
    resolve_recipe_envelope_byte_limit,
    resolve_recipe_section_response_bound,
)

__all__ = [
    "recipe_delivery_request_digest",
    "resolve_general_output_token_limit",
    "resolve_recipe_delivery_decision",
    "resolve_recipe_envelope_byte_limit",
    "resolve_recipe_section_response_bound",
]
