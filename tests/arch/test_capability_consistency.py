"""Behavioral arch tests: BackendCapabilities filesystem consistency."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.execution.backends import BACKEND_REGISTRY
from autoskillit.hook_registry import HOOKS_DIR

pytestmark = [pytest.mark.layer("arch"), pytest.mark.medium]


# ---------------------------------------------------------------------------
# Parametrize helpers — derived from BACKEND_REGISTRY at collection time
# ---------------------------------------------------------------------------

_GUARD_PARAMS: list[tuple[str, str]] = [
    (name, guard)
    for name, cls in sorted(BACKEND_REGISTRY.items())
    for guard in sorted(cls().capabilities.applicable_guards)
]

_REQUIRED_FILES_PARAMS: list[tuple[str, type]] = [
    (name, cls)
    for name, cls in sorted(BACKEND_REGISTRY.items())
    if cls().capabilities.required_session_files
]

_SYMLINK_PARAMS: list[tuple[str, type]] = [
    (name, cls)
    for name, cls in sorted(BACKEND_REGISTRY.items())
    if cls().capabilities.session_dir_symlinks
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    codex_dir = home / ".codex"
    codex_dir.mkdir()
    (codex_dir / "config.toml").write_text("")
    (codex_dir / "auth.json").write_text("{}")
    (codex_dir / ".env").write_text("")
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    return home


# ---------------------------------------------------------------------------
# Test classes
# ---------------------------------------------------------------------------


class TestApplicableGuardsExistOnDisk:
    @pytest.mark.parametrize(
        ("backend_name", "guard_name"),
        _GUARD_PARAMS,
        ids=[f"{name}-{guard}" for name, guard in _GUARD_PARAMS],
    )
    def test_guard_script_exists(self, backend_name: str, guard_name: str) -> None:
        script = HOOKS_DIR / "guards" / f"{guard_name}.py"
        assert script.is_file(), (
            f"Backend {backend_name!r} declares applicable_guard {guard_name!r} "
            f"but {script} does not exist"
        )


class TestRequiredSessionFilesCreated:
    @pytest.mark.parametrize(
        ("backend_name", "backend_cls"),
        _REQUIRED_FILES_PARAMS,
        ids=[name for name, _ in _REQUIRED_FILES_PARAMS],
    )
    def test_required_files_are_created(
        self,
        backend_name: str,
        backend_cls: type,
        tmp_path: Path,
        fake_home: Path,  # type: ignore[report-unused]
    ) -> None:
        backend = backend_cls()
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        backend.setup_session_dir(session_dir)
        for filename in sorted(backend.capabilities.required_session_files):
            assert (session_dir / filename).is_file(), (
                f"Backend {backend_name!r} declares required_session_file {filename!r} "
                f"but setup_session_dir did not create it"
            )


class TestSessionDirSymlinksAreSymlinks:
    @pytest.mark.parametrize(
        ("backend_name", "backend_cls"),
        _SYMLINK_PARAMS,
        ids=[name for name, _ in _SYMLINK_PARAMS],
    )
    def test_symlinks_are_symlinks(
        self,
        backend_name: str,
        backend_cls: type,
        tmp_path: Path,
        fake_home: Path,  # type: ignore[report-unused]
    ) -> None:
        backend = backend_cls()
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        backend.setup_session_dir(session_dir)
        for entry in sorted(backend.capabilities.session_dir_symlinks):
            path = session_dir / entry
            if path.exists() or path.is_symlink():
                assert path.is_symlink(), (
                    f"Backend {backend_name!r} declares session_dir_symlink {entry!r} "
                    f"but {path} is not a symlink (likely a copy)"
                )
