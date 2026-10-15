from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from autoskillit.core import ChildTaskTranscript, SessionLocator, SessionSummary, get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class CompositeSessionLocator:
    _locators: tuple[SessionLocator, ...] = field(default=(), repr=False)

    def _locator_sources(
        self,
    ) -> Iterable[tuple[str | None, Callable[[], SessionLocator]]]:
        if self._locators:
            return tuple((None, lambda locator=locator: locator) for locator in self._locators)
        from autoskillit.execution.backends import BACKEND_REGISTRY

        return tuple(
            (backend_name, lambda cls=cls: cls().session_locator())
            for backend_name, cls in BACKEND_REGISTRY.items()
        )

    def _first_resolved_session(self, session_id: str) -> tuple[SessionLocator, Path] | None:
        if not session_id or session_id.startswith(("no_session_", "crashed_")):
            return None
        for backend_name, locator_factory in self._locator_sources():
            try:
                locator = locator_factory()
                result = locator.locate_session(session_id)
            except Exception:
                if backend_name is None:
                    logger.debug("session_locate_failed", exc_info=True)
                else:
                    logger.debug("session_locate_failed", backend=backend_name, exc_info=True)
                continue
            if result is not None:
                return locator, result
        return None

    def locate_session(self, session_id: str) -> Path | None:
        resolved = self._first_resolved_session(session_id)
        return resolved[1] if resolved is not None else None

    def session_context_was_compacted(self, cwd: str, session_id: str) -> bool:
        resolved = self._first_resolved_session(session_id)
        if resolved is None:
            return False
        locator, _ = resolved
        # Once the exact session is found, propagate transcript read errors;
        # another backend cannot provide authoritative evidence for that session.
        return locator.session_context_was_compacted(cwd, session_id)

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
