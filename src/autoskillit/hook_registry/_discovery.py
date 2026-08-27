"""Claude settings path resolution across all scope tiers."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path


def _claude_settings_path(scope: str, *, cwd: Path) -> Path:
    """Return the Claude settings path for a scope and explicit project cwd.

    Raises:
        ValueError: If ``scope`` is not ``user``, ``project``, or ``local``.
    """
    if scope == "user":
        return Path.home() / ".claude" / "settings.json"
    project_dir = Path(cwd)
    if scope == "project":
        return project_dir / ".claude" / "settings.json"
    if scope == "local":
        return project_dir / ".claude" / "settings.local.json"
    raise ValueError(f"invalid Claude settings scope: {scope!r}")


def iter_all_scope_paths(
    project_root: Path | None = None,
) -> Iterator[tuple[str, Path]]:
    """Yield (scope_label, settings_path) for all Claude Code settings scopes.

    Always yields the user scope. Project and local scopes are yielded only
    when project_root is provided AND the corresponding .claude/ directory exists.
    """
    scope_cwd = Path.cwd() if project_root is None else Path(project_root)
    yield ("user", _claude_settings_path("user", cwd=scope_cwd))
    if project_root is not None:
        claude_dir = scope_cwd / ".claude"
        if claude_dir.is_dir():
            yield ("project", _claude_settings_path("project", cwd=scope_cwd))
            local_path = _claude_settings_path("local", cwd=scope_cwd)
            if local_path.exists():
                yield ("local", local_path)
