"""Direct unit tests for resolve_persistent_session_root / resolve_persistent_session_roots."""

from __future__ import annotations

from pathlib import Path

import pytest

import autoskillit.workspace.session_skills as session_skills
from tests.workspace._helpers import _stub_backend

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.small]


class TestResolvePersistentSessionRoot:
    """T1 — direct unit tests for resolve_persistent_session_root (#4391)."""

    def test_non_persistent_backend_returns_none(self, tmp_path: Path) -> None:
        from autoskillit.execution.backends import get_backend

        backend = get_backend("claude-code")
        assert session_skills.resolve_persistent_session_root(tmp_path, backend) is None

    def test_codex_backend_returns_subdir_of_base_root(self, tmp_path: Path) -> None:
        from autoskillit.core import CODEX_SESSIONS_SUBDIR
        from autoskillit.execution.backends import get_backend

        backend = get_backend("codex")
        assert session_skills.resolve_persistent_session_root(tmp_path, backend) == (
            tmp_path / CODEX_SESSIONS_SUBDIR
        )

    def test_missing_root_convention_raises(self, tmp_path: Path) -> None:
        backend = _stub_backend("synthetic", persistent_session_root_subdir=None)
        with pytest.raises(RuntimeError, match="no generated-home root convention"):
            session_skills.resolve_persistent_session_root(tmp_path, backend)

    def test_absolute_subdir_raises_unsafe(self, tmp_path: Path) -> None:
        backend = _stub_backend("synthetic", persistent_session_root_subdir=Path("/abs"))
        with pytest.raises(RuntimeError, match="Unsafe persistent generated-home root"):
            session_skills.resolve_persistent_session_root(tmp_path, backend)

    def test_parent_traversal_subdir_raises_unsafe(self, tmp_path: Path) -> None:
        backend = _stub_backend("synthetic", persistent_session_root_subdir=Path("../x"))
        with pytest.raises(RuntimeError, match="Unsafe persistent generated-home root"):
            session_skills.resolve_persistent_session_root(tmp_path, backend)


class TestResolvePersistentSessionRoots:
    """T2 — unit tests for resolve_persistent_session_roots (#4391)."""

    def test_only_persistent_backends_are_included(self, tmp_path: Path) -> None:
        from autoskillit.core import CODEX_SESSIONS_SUBDIR
        from autoskillit.execution.backends import get_backend

        backends = [get_backend("claude-code"), get_backend("codex")]
        roots = session_skills.resolve_persistent_session_roots(tmp_path, backends)
        assert roots == {"codex": tmp_path / CODEX_SESSIONS_SUBDIR}

    def test_malformed_backend_not_in_required_names_is_skipped(self, tmp_path: Path) -> None:
        backend = _stub_backend("synthetic", persistent_session_root_subdir=None)
        roots = session_skills.resolve_persistent_session_roots(tmp_path, [backend])
        assert roots == {}

    def test_malformed_backend_in_required_names_raises(self, tmp_path: Path) -> None:
        backend = _stub_backend("synthetic", persistent_session_root_subdir=None)
        with pytest.raises(RuntimeError, match="no generated-home root convention"):
            session_skills.resolve_persistent_session_roots(
                tmp_path,
                [backend],
                required_backend_names=frozenset({"synthetic"}),
            )
