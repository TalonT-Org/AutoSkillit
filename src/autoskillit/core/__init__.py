"""IL-0 foundation sub-package: types, logging, and I/O primitives.

Re-exports the full public surface so callers can do
``from autoskillit.core import get_logger`` etc.  Submodules are loaded
lazily on first attribute access (PEP 562 via lazy-loader).
"""

import lazy_loader as lazy

__getattr__, _dir, __all__ = lazy.attach_stub(__name__, __file__)
del _dir  # replaced below so dir() reflects the filtered __all__

_PRIVATE_REEXPORTS: dict[str, str] = {
    "_collect_disabled_feature_tags": ".claude_env.feature_flags",
    "_parse_issue_ref": ".git.github_url",
    "_is_release_tag": ".install.install_detect",
    "_is_stable_track": ".install.install_detect",
    "_AUTOSKILLIT_GITIGNORE_ENTRIES": ".io",
    "_COMMITTED_BY_DESIGN": ".io",
    "_render_gfm_table": ".io",
    "_render_terminal_table": ".io",
    "_AUTOSKILLIT_INSTALL_ROOT_KEY": ".plugins._plugin_ids",
    "_AUTOSKILLIT_PLUGIN_KEY": ".plugins._plugin_ids",
    "_installed_plugins_path": ".plugins._plugin_ids",
    "_InstallLock": ".plugins._retiring_cache",
    "_MAX_ASSOCIATION_FILES": ".types",
    "_MAX_REFERENCED_ARTIFACTS_PER_CALL": ".types",
    "_PLAN_ASSOCIATION_DOMAIN": ".types",
    "_PLAN_ASSOCIATION_KEYS": ".types",
}
__all__ = [n for n in __all__ if n not in _PRIVATE_REEXPORTS]


def __dir__() -> list[str]:
    return __all__
