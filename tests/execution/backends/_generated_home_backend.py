"""Explicit Codex test adapter for direct command-builder calls."""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

import pytest

from autoskillit.execution.backends.codex import CodexBackend


class GeneratedHomeCodexBackend(CodexBackend):
    """Codex backend whose direct builders default to a fixture-bound home."""

    generated_home: ClassVar[Path | None] = None

    @classmethod
    def _fixture_home(cls) -> Path:
        assert cls.generated_home is not None
        return cls.generated_home

    def build_headless_cmd(self, *args: Any, **kwargs: Any):
        if kwargs.get("generated_home") is None:
            kwargs["generated_home"] = self._fixture_home()
        return super().build_headless_cmd(*args, **kwargs)

    def build_interactive_cmd(self, *args: Any, **kwargs: Any):
        if kwargs.get("generated_home") is None:
            kwargs["generated_home"] = self._fixture_home()
        return super().build_interactive_cmd(*args, **kwargs)

    def build_resume_cmd(self, *args: Any, **kwargs: Any):
        if kwargs.get("session_home") is None and kwargs.get("managed_skill_catalog") is None:
            kwargs["session_home"] = str(self._fixture_home())
        return super().build_resume_cmd(*args, **kwargs)


def bind_generated_home_backend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Bind the shared adapter to one test's isolated generated home."""
    monkeypatch.setattr(
        GeneratedHomeCodexBackend,
        "generated_home",
        tmp_path / "generated-home",
    )
