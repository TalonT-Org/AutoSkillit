"""Explicit Codex test adapter for direct command-builder calls."""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from autoskillit.execution.backends.codex import CodexBackend


class GeneratedHomeCodexBackend(CodexBackend):
    """Codex backend whose direct builders default to a fixture-bound home."""

    generated_home: ClassVar[Path | None] = None

    @classmethod
    def _fixture_home(cls) -> Path:
        assert cls.generated_home is not None
        return cls.generated_home

    def build_headless_cmd(self, *args: Any, **kwargs: Any):
        kwargs.setdefault("generated_home", self._fixture_home())
        return super().build_headless_cmd(*args, **kwargs)

    def build_interactive_cmd(self, *args: Any, **kwargs: Any):
        kwargs.setdefault("generated_home", self._fixture_home())
        return super().build_interactive_cmd(*args, **kwargs)

    def build_resume_cmd(self, *args: Any, **kwargs: Any):
        kwargs.setdefault("session_home", str(self._fixture_home()))
        return super().build_resume_cmd(*args, **kwargs)
