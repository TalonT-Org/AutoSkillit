"""Shared architectural budgets for recipe-delivery contract tests."""

from autoskillit.core import RECIPE_SECTION_RESPONSE_FLOOR_BYTES

MAX_ENVELOPE_MANIFEST_BYTES = 16_384
MAX_OPEN_KITCHEN_CALLS = {
    "claude_code_inline": 1,
    "claude_code_bounded": 4,
    "codex_bounded": 4,
}
MAX_BYTES_PER_PAGE = 90_000
MIN_CALIBRATED_PER_PAGE_BYTES = 90_000
# Today's bundled recipes are calibrated to fit each backend's default (not
# page_max_bytes=None) delivery bound within a single page. This is a
# calibration target for the current recipe set, not a hard runtime
# invariant -- the #4557 outcome permits multi-page plans.
CALIBRATED_PAGES_PER_SECTION = 1

assert MAX_BYTES_PER_PAGE >= RECIPE_SECTION_RESPONSE_FLOOR_BYTES
