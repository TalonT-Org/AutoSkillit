from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from autoskillit.core import ChildTaskTranscript, SessionLocator, SessionSummary, get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class CompositeSessionLocator:
    _locators: tuple[SessionLocator, ...] = field(default=(), repr=False)

    def locate_session(self, session_id: str) -> Path | None:
        if not session_id or session_id.startswith(("no_session_", "crashed_")):
            return None
        if self._locators:
            locators: tuple[SessionLocator, ...] = self._locators
            for locator in locators:
                try:
                    result = locator.locate_session(session_id)
                except Exception:
                    logger.debug("session_locate_failed", exc_info=True)
                    continue
                if result is not None:
                    return result
            return None
        from autoskillit.execution.backends import BACKEND_REGISTRY

        for backend_name, cls in BACKEND_REGISTRY.items():
            try:
                result = cls().session_locator().locate_session(session_id)
            except Exception:
                logger.debug("session_locate_failed", backend=backend_name, exc_info=True)
                continue
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

    def read_child_task(self, child_id: str) -> ChildTaskTranscript | None:
        first_error: Exception | None = None
        sources: Iterable[tuple[str | None, Callable[[], SessionLocator]]]
        if self._locators:
            sources = ((None, lambda: locator) for locator in self._locators)
        else:
            from autoskillit.execution.backends import BACKEND_REGISTRY

            sources = (
                (backend_name, lambda: cls().session_locator())
                for backend_name, cls in BACKEND_REGISTRY.items()
            )

        for backend_name, locator_factory in sources:
            try:
                result = locator_factory().read_child_task(child_id)
            except Exception as exc:
                if backend_name is None:
                    logger.debug("child_task_read_failed", exc_info=True)
                else:
                    logger.debug("child_task_read_failed", backend=backend_name, exc_info=True)
                if first_error is None:
                    first_error = exc
                continue
            if result is not None:
                return result
        if first_error is not None:
            raise first_error
        return None

    def list_sessions(self, cwd: str) -> tuple[SessionSummary, ...]:
        summaries: list[SessionSummary] = []
        if self._locators:
            for locator in self._locators:
                try:
                    summaries.extend(locator.list_sessions(cwd))
                except Exception:
                    logger.debug("session_list_failed", exc_info=True)
            return tuple(summaries)

        from autoskillit.execution.backends import BACKEND_REGISTRY

        for backend_name, cls in BACKEND_REGISTRY.items():
            try:
                summaries.extend(cls().session_locator().list_sessions(cwd))
            except Exception:
                logger.debug("session_list_failed", backend=backend_name, exc_info=True)
        return tuple(summaries)

    def locator_for(self, backend_name: str) -> SessionLocator:
        from autoskillit.execution.backends import BACKEND_REGISTRY

        cls = BACKEND_REGISTRY.get(backend_name)
        if cls is None:
            valid = ", ".join(sorted(BACKEND_REGISTRY))
            msg = f"Unknown backend {backend_name!r}. Valid names: {valid}"
            raise ValueError(msg)
        return cls().session_locator()
