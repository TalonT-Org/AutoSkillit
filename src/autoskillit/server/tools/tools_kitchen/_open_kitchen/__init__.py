"""open_kitchen tool package facade.

The MCP tool and its orchestrator body live in ``_orchestrator.py``;
``_gate.py`` owns the gate-enablement handler, ``_visibility.py`` owns
subset/feature tool-visibility reconciliation, and ``_recipe_serve.py`` owns
named-recipe serving. This ``__init__.py`` is a pure re-export facade
(P14-2) — no logic lives here.
"""

from __future__ import annotations

from autoskillit.server.tools.tools_kitchen._open_kitchen._gate import (
    _open_kitchen_handler,
)
from autoskillit.server.tools.tools_kitchen._open_kitchen._orchestrator import open_kitchen
from autoskillit.server.tools.tools_kitchen._open_kitchen._recipe_serve import (
    _cache_finalized_recipe_projection,
    _clear_active_recipe_projection,
)
from autoskillit.server.tools.tools_kitchen._open_kitchen._visibility import (
    _redisable_subsets,
)

__all__ = [
    "open_kitchen",
    "_open_kitchen_handler",
    "_redisable_subsets",
    "_cache_finalized_recipe_projection",
    "_clear_active_recipe_projection",
]
