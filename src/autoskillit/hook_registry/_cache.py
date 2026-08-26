"""Plugin-cache hook validation.

Reads each hooks.json found under
``cache_dir/<version>/hooks/hooks.json`` — the real installed layout (write
site: ``cli/install/_marketplace.py``,
``public_plugin_root / "hooks" / "hooks.json"``) — and checks that every
autoskillit hook script path exists on disk. Token-bearing commands are
expanded against ``hooks_json_path.parent.parent``: the ``<version>``
incarnation directory, which is the plugin root Claude Code binds
``${CLAUDE_PLUGIN_ROOT}`` to (it directly contains ``hooks/``, ``agents/``,
``.claude-plugin/``, ``skills/``, ``recipes/``, ``assets/``).
"""

from __future__ import annotations

from pathlib import Path

from autoskillit.core import installed_plugin_cache_dir

from ._drift import find_broken_hook_scripts


def validate_plugin_cache_hooks(cache_dir: Path | None = None) -> list[str]:
    """Return list of broken hook commands from the plugin cache hooks.json."""
    _cache = cache_dir or installed_plugin_cache_dir(Path.home(), "autoskillit")
    broken: list[str] = []
    if not _cache.is_dir():
        return broken
    for hooks_json_path in _cache.glob("*/hooks/hooks.json"):
        broken.extend(
            find_broken_hook_scripts(
                hooks_json_path,
                expansion_root=hooks_json_path.parent.parent,
            )
        )
    return broken
