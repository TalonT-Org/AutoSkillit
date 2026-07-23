"""Stdlib-only recipe-delivery wire framing shared with hook subprocesses."""

from __future__ import annotations

RECIPE_BODY_START = "--- AUTOSKILLIT RECIPE BODY START ---"
RECIPE_BODY_END = "--- AUTOSKILLIT RECIPE BODY END ---"
RECIPE_COMPLETION_SENTINEL = "AUTOSKILLIT_RECIPE_DELIVERY_COMPLETE"


def is_attested_recipe_delivery(text: str) -> bool:
    """Return whether text contains the complete attested recipe wire frame."""
    return (
        text.startswith('{"recipe_delivery":')
        and RECIPE_BODY_START in text
        and RECIPE_BODY_END in text
        and RECIPE_COMPLETION_SENTINEL in text
    )
