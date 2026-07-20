"""Per-backend effective delivery-bound resolution.

The configured ``tool_output_token_limit`` written to Codex ``config.toml``
is a config-file ceiling. Code-mode models may bypass that ceiling when
they emit ``max_output_tokens`` without an upper bound, so the operative
bound for those paths is the harness default (~10K tokens). The
``BackendCapabilities.effective_delivery_token_limit`` field encodes this
worst-case operative bound; ``resolve_effective_delivery_bound`` is the
canonical accessor.
"""

from __future__ import annotations

from autoskillit.core import BackendCapabilities


def resolve_effective_delivery_bound(caps: BackendCapabilities) -> int:
    """Return the worst-case operative token bound for ``caps``'s backend transport."""
    return caps.effective_delivery_token_limit
