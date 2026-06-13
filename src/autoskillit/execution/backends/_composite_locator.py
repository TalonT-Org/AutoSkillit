from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from autoskillit.core import SessionLocator


@dataclass(frozen=True, slots=True)
class CompositeSessionLocator(SessionLocator):
    def locate_session(self, session_id: str) -> Path | None:
        if not session_id or session_id.startswith(("no_session_", "crashed_")):
            return None
        from autoskillit.execution.backends import BACKEND_REGISTRY

        for cls in BACKEND_REGISTRY.values():
            result = cls().session_locator().locate_session(session_id)
            if result is not None:
                return result
        return None

    def project_log_dir(self, cwd: str) -> Path:
        return self.project_log_dir_for(cwd, "claude-code")

    def project_log_dir_for(self, cwd: str, backend_name: str) -> Path:
        from autoskillit.execution.backends import BACKEND_REGISTRY

        cls = BACKEND_REGISTRY.get(backend_name)
        if cls is None:
            valid = ", ".join(sorted(BACKEND_REGISTRY))
            msg = f"Unknown backend {backend_name!r}. Valid names: {valid}"
            raise ValueError(msg)
        return cls().session_locator().project_log_dir(cwd)

    def session_log_path(self, cwd: str, session_id: str) -> Path | None:
        return self.locate_session(session_id)

    def locator_for(self, backend_name: str) -> SessionLocator:
        from autoskillit.execution.backends import BACKEND_REGISTRY

        cls = BACKEND_REGISTRY.get(backend_name)
        if cls is None:
            valid = ", ".join(sorted(BACKEND_REGISTRY))
            msg = f"Unknown backend {backend_name!r}. Valid names: {valid}"
            raise ValueError(msg)
        return cls().session_locator()
