"""Shared architectural budgets for recipe-delivery contract tests."""

MAX_ENVELOPE_MANIFEST_BYTES = 16_384
MAX_OPEN_KITCHEN_CALLS = {
    "claude_code_inline": 1,
    "claude_code_bounded": 4,
    "codex_bounded": 4,
}
MAX_PAGES_PER_SECTION = 1
