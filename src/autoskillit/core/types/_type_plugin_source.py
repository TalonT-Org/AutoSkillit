"""PluginSource — the always-projected plugin root handed to a spawned session.

Two distinct types, deliberately not interchangeable:

``DirectInstall``
    A raw plugin root on disk — the *input* to projection. Only
    ``autoskillit.workspace.skill_projection`` consumes one.

``ProjectedPluginRoot``
    A sanitized projection produced by that module — the *only* root a spawned
    agent session is ever pointed at, and the sole ``PluginSource`` variant.

Keeping them apart makes "this path has been projected" a type-level fact
rather than a runtime assertion at command-construction time.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..paths import pkg_root

__all__ = ["DirectInstall", "PluginSource", "ProjectedPluginRoot"]


@dataclass(frozen=True, slots=True)
class DirectInstall:
    """A raw plugin root — the projection input, never handed to a session."""

    plugin_dir: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "plugin_dir", Path(self.plugin_dir))


@dataclass(frozen=True, slots=True)
class ProjectedPluginRoot:
    """A sanitized plugin projection — the only root a session may load.

    Constructed exclusively by ``autoskillit.workspace.skill_projection``;
    an architectural ratchet test forbids construction anywhere else in
    ``src/``. The invariants below are what the backend command builders used
    to re-check at runtime.
    """

    plugin_dir: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "plugin_dir", Path(self.plugin_dir))
        if not self.plugin_dir.is_absolute():
            raise ValueError(f"projected plugin root must be an absolute path: {self.plugin_dir}")
        if self.plugin_dir.resolve() == pkg_root().resolve():
            raise ValueError(
                f"projected plugin root must not be the canonical package root: {self.plugin_dir}"
            )


PluginSource = ProjectedPluginRoot
