"""Shared architectural budgets for recipe-delivery contract tests."""

from autoskillit.core import RECIPE_SECTION_RESPONSE_FLOOR_BYTES

MAX_ENVELOPE_MANIFEST_BYTES = 16_384
MAX_OPEN_KITCHEN_CALLS = {
    "claude_code_inline": 1,
    "claude_code_bounded": 4,
    "codex_bounded": 4,
}
MAX_PAGES_PER_SECTION = 1
MAX_TOKENS_PER_PAGE = 90_000
MIN_CALIBRATED_PER_PAGE_BYTES = 90_000

assert MAX_TOKENS_PER_PAGE >= RECIPE_SECTION_RESPONSE_FLOOR_BYTES
