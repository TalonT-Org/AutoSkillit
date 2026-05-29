"""Phase 2 tests: session_skills module — provider and core manager."""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

import pytest
import yaml

from autoskillit.core import BackendCapabilities
from autoskillit.workspace.session_skills import (
    _SKILLS_SUBDIR,
    CODEX_SKILLS_SUBDIR,
    DefaultSessionSkillManager,
    SkillsDirectoryProvider,
    resolve_ephemeral_root,
)

_CODEX_CAPABILITIES = BackendCapabilities(
    channel_b_capable=False,
    pty_required=False,
    session_resume_capable=True,
    skill_injection_capable=True,
    supports_thinking_blocks=False,
    supports_claude_format_stdout=False,
    exit_code_is_terminal=True,
    mcp_config_capable=True,
    food_truck_capable=True,
    completion_record_types=frozenset({"turn.completed", "turn.failed", "error"}),
    session_record_types=frozenset({"item.completed"}),
    required_session_files=frozenset({"config.toml"}),
    session_dir_symlinks=frozenset({"auth.json", "sessions"}),
    skills_subdir="skills",
)

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.small]


def test_resolve_ephemeral_root_returns_writable_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import autoskillit.workspace.session_skills as ss

    monkeypatch.setattr(ss, "_CANDIDATE_ROOTS", [tmp_path])
    root = resolve_ephemeral_root()
    assert root.exists()
    assert root.is_dir()
    test_file = root / "write_test.tmp"
    test_file.write_text("ok")
    test_file.unlink()


def test_resolve_ephemeral_root_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import autoskillit.workspace.session_skills as ss

    monkeypatch.setattr(ss, "_CANDIDATE_ROOTS", [Path("/nonexistent"), tmp_path])
    root = ss.resolve_ephemeral_root()
    assert root.exists()


def test_skills_directory_provider_lists_all_skills() -> None:
    provider = SkillsDirectoryProvider()
    skills = provider.list_skills()
    names = {s.name for s in skills}
    assert "open-kitchen" in names
    assert "close-kitchen" in names
    assert "implement-worktree" in names
    assert "sous-chef" not in names  # internal, excluded


def test_provider_injects_disable_model_invocation_for_tier2() -> None:
    provider = SkillsDirectoryProvider()
    content = provider.get_skill_content("open-kitchen", gated=True)
    fm_match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    assert fm_match, "Content must have YAML frontmatter"
    fm = yaml.safe_load(fm_match.group(1))
    assert fm.get("disable-model-invocation") is True


def test_provider_does_not_inject_for_cook_session() -> None:
    # Use mermaid (skills_extended/, no flag at rest) to verify that gated=False
    # returns unmodified content without injecting disable-model-invocation.
    # open-kitchen and close-kitchen carry disable-model-invocation: true in their source
    # (human-only skills), so they cannot be used to assert "flag not present".
    provider = SkillsDirectoryProvider()
    content = provider.get_skill_content("mermaid", gated=False)
    fm_match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    assert fm_match, "Content must have YAML frontmatter"
    fm = yaml.safe_load(fm_match.group(1))
    assert fm.get("disable-model-invocation") is not True


@pytest.mark.parametrize(
    ("backend_name", "skills_subdir"),
    [
        pytest.param(None, _SKILLS_SUBDIR, id="claude-code"),
        pytest.param("codex", CODEX_SKILLS_SUBDIR, id="codex"),
    ],
)
def test_session_skill_manager_creates_ephemeral_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    make_session_skill_manager,
    backend_name: str | None,
    skills_subdir: Path,
) -> None:
    backend = None
    if backend_name == "codex":
        fake_home = tmp_path / "fakehome"
        codex_dir = fake_home / ".codex"
        codex_dir.mkdir(parents=True)
        (codex_dir / "config.toml").write_text("[codex]\n")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
        from unittest.mock import MagicMock

        backend = MagicMock()
        backend.name = "codex"
        backend.capabilities = _CODEX_CAPABILITIES

    mgr = make_session_skill_manager()
    session_path = mgr.init_session("test-session-abc", cook_session=True, backend=backend)
    assert session_path.exists()
    assert session_path.is_dir()
    skill_files = list((session_path / skills_subdir).glob("*/SKILL.md"))
    assert len(skill_files) > 0
    if backend_name == "codex":
        assert not (session_path / _SKILLS_SUBDIR).exists()


@pytest.mark.parametrize(
    ("backend_name", "skills_subdir"),
    [
        pytest.param(None, _SKILLS_SUBDIR, id="claude-code"),
        pytest.param("codex", CODEX_SKILLS_SUBDIR, id="codex"),
    ],
)
def test_session_manager_injects_disable_for_tier2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    make_session_skill_manager,
    backend_name: str | None,
    skills_subdir: Path,
) -> None:
    """Structural gating: tier2 skill directory is absent after init_session for all backends."""
    from tests._helpers import make_skills_config, make_test_config

    backend = None
    if backend_name == "codex":
        fake_home = tmp_path / "fakehome"
        codex_dir = fake_home / ".codex"
        codex_dir.mkdir(parents=True)
        (codex_dir / "config.toml").write_text("[codex]\n")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
        from unittest.mock import MagicMock

        backend = MagicMock()
        backend.name = "codex"
        backend.capabilities = _CODEX_CAPABILITIES

    mgr = make_session_skill_manager()
    config = make_test_config(
        skills=make_skills_config(
            tier1=["open-kitchen", "close-kitchen"],
            tier2=["mermaid"],
            tier3=[],
        )
    )
    session_path = mgr.init_session(
        "test-session-xyz", cook_session=False, config=config, backend=backend
    )
    mermaid_dir = session_path / skills_subdir / "mermaid"
    assert not mermaid_dir.exists()
    if backend_name == "codex":
        assert not (session_path / _SKILLS_SUBDIR).exists()


def test_session_manager_no_flag_for_cook_session(tmp_path: Path) -> None:
    """Cook session writes all skills including tier2 (no gating)."""
    from tests._helpers import make_skills_config, make_test_config

    provider = SkillsDirectoryProvider()
    mgr = DefaultSessionSkillManager(provider, ephemeral_root=tmp_path)
    config = make_test_config(
        skills=make_skills_config(
            tier1=["open-kitchen", "close-kitchen"],
            tier2=["mermaid"],
            tier3=[],
        )
    )
    session_path = mgr.init_session("cook-session-123", cook_session=True, config=config)
    mermaid_md = session_path / _SKILLS_SUBDIR / "mermaid" / "SKILL.md"
    assert mermaid_md.exists()


def test_activate_skill_deps_removes_flag(tmp_path: Path) -> None:
    from tests._helpers import make_skills_config, make_test_config

    provider = SkillsDirectoryProvider()
    mgr = DefaultSessionSkillManager(provider, ephemeral_root=tmp_path)
    config = make_test_config(
        skills=make_skills_config(
            tier1=["open-kitchen", "close-kitchen"],
            tier2=["mermaid"],
            tier3=[],
        )
    )
    mgr.init_session(
        "session-toggle",
        cook_session=False,
        config=config,
        allow_only=frozenset({"mermaid"}),
    )
    result = mgr.activate_skill_deps("session-toggle", "mermaid")
    assert result is True
    mermaid_md = tmp_path / "session-toggle" / _SKILLS_SUBDIR / "mermaid" / "SKILL.md"
    content = mermaid_md.read_text()
    fm_match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    assert fm_match
    fm = yaml.safe_load(fm_match.group(1))
    assert "disable-model-invocation" not in fm or fm.get("disable-model-invocation") is not True


def test_activate_with_deps_materialises_absent_skill(
    tmp_path: Path, make_session_skill_manager
) -> None:
    """When SKILL.md is absent (structural gating), _activate_with_deps copies it from provider."""
    from tests._helpers import make_skills_config, make_test_config

    mgr = make_session_skill_manager()
    config = make_test_config(
        skills=make_skills_config(
            tier1=["open-kitchen", "close-kitchen"],
            tier2=["mermaid"],
            tier3=[],
        )
    )
    mgr.init_session("session-materialise", cook_session=False, config=config)
    mermaid_md = tmp_path / "session-materialise" / _SKILLS_SUBDIR / "mermaid" / "SKILL.md"
    assert not mermaid_md.exists()

    result = mgr.activate_skill_deps("session-materialise", "mermaid")
    assert result is True
    assert mermaid_md.exists()

    content = mermaid_md.read_text()
    fm_match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    assert fm_match
    fm = yaml.safe_load(fm_match.group(1))
    assert "disable-model-invocation" not in fm or fm.get("disable-model-invocation") is not True


def test_activate_with_deps_already_present_removes_flag(
    tmp_path: Path, make_session_skill_manager
) -> None:
    """When SKILL.md already exists with disable-model-invocation, activate removes the key."""
    from tests._helpers import make_skills_config, make_test_config

    mgr = make_session_skill_manager()
    config = make_test_config(
        skills=make_skills_config(
            tier1=["open-kitchen", "close-kitchen"],
            tier2=["mermaid"],
            tier3=[],
        )
    )
    mgr.init_session(
        "session-flag-remove",
        cook_session=False,
        config=config,
        allow_only=frozenset({"mermaid"}),
    )
    mermaid_md = tmp_path / "session-flag-remove" / _SKILLS_SUBDIR / "mermaid" / "SKILL.md"
    assert mermaid_md.exists()

    content = mermaid_md.read_text()
    injected = content.replace("---\n", "---\ndisable-model-invocation: true\n", 1)
    mermaid_md.write_text(injected)

    result = mgr.activate_skill_deps("session-flag-remove", "mermaid")
    assert result is True
    updated = mermaid_md.read_text()
    fm_match = re.match(r"^---\n(.*?)\n---", updated, re.DOTALL)
    assert fm_match
    fm = yaml.safe_load(fm_match.group(1))
    assert "disable-model-invocation" not in fm or fm.get("disable-model-invocation") is not True


def test_init_session_gated_tier2_skill_dir_absent(tmp_path: Path) -> None:
    """Gated tier2 skill has no directory at all; non-gated skills have SKILL.md."""
    from tests._helpers import make_skills_config, make_test_config

    provider = SkillsDirectoryProvider()
    mgr = DefaultSessionSkillManager(provider, ephemeral_root=tmp_path)
    config = make_test_config(
        skills=make_skills_config(
            tier1=["open-kitchen", "close-kitchen"],
            tier2=["mermaid"],
            tier3=[],
        )
    )
    session_path = mgr.init_session("test-absent", cook_session=False, config=config)
    skills_base = session_path / _SKILLS_SUBDIR
    # Tier2 directory absent
    assert not (skills_base / "mermaid").exists()
    # Non-gated BUNDLED_EXTENDED skills are written (BUNDLED skills go via --plugin-dir, not here)
    assert (skills_base / "implement-worktree" / "SKILL.md").exists()


def test_init_session_tier2_skill_present_when_in_allow_only(tmp_path: Path) -> None:
    """Tier2 skill in allow_only is written; tier2 skill NOT in allow_only is absent."""
    from tests._helpers import make_skills_config, make_test_config

    provider = SkillsDirectoryProvider()
    mgr = DefaultSessionSkillManager(provider, ephemeral_root=tmp_path)
    config = make_test_config(
        skills=make_skills_config(
            tier1=["open-kitchen", "close-kitchen"],
            tier2=["mermaid", "make-plan"],
            tier3=[],
        )
    )
    session_path = mgr.init_session(
        "test-allow",
        cook_session=False,
        config=config,
        allow_only=frozenset({"mermaid"}),
    )
    skills_base = session_path / _SKILLS_SUBDIR
    assert (skills_base / "mermaid" / "SKILL.md").exists()
    assert not (skills_base / "make-plan").exists()


def test_cleanup_stale_removes_old_dirs(tmp_path: Path) -> None:
    provider = SkillsDirectoryProvider()
    mgr = DefaultSessionSkillManager(provider, ephemeral_root=tmp_path)
    stale_dir = tmp_path / "stale-session"
    stale_dir.mkdir()
    os.utime(stale_dir, (time.time() - 90000, time.time() - 90000))  # 25h old
    fresh_dir = tmp_path / "fresh-session"
    fresh_dir.mkdir()
    count = mgr.cleanup_stale(max_age_seconds=86400)
    assert count == 1
    assert not stale_dir.exists()
    assert fresh_dir.exists()


def test_init_session_backend_none_uses_claude_skills_subdir(tmp_path: Path) -> None:
    """When backend is None (default), skills_base resolves to .claude/skills/."""
    provider = SkillsDirectoryProvider()
    mgr = DefaultSessionSkillManager(provider, ephemeral_root=tmp_path)
    session_path = mgr.init_session("test-backend-none", cook_session=True)
    skills_dir = session_path / _SKILLS_SUBDIR
    assert skills_dir.is_dir()
    skill_files = list(skills_dir.glob("*/SKILL.md"))
    assert len(skill_files) > 0


def test_init_session_codex_backend_uses_codex_skills_subdir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When backend.name == 'codex', skills_base resolves to skills/ (not .claude/skills/)."""
    from unittest.mock import MagicMock

    from autoskillit.workspace.session_skills import CODEX_SKILLS_SUBDIR

    codex_backend = MagicMock()
    codex_backend.name = "codex"
    codex_backend.capabilities = _CODEX_CAPABILITIES

    fake_home = tmp_path / "fakehome"
    codex_dir = fake_home / ".codex"
    codex_dir.mkdir(parents=True)
    (codex_dir / "config.toml").write_text("[codex]\n")

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    provider = SkillsDirectoryProvider()
    mgr = DefaultSessionSkillManager(provider, ephemeral_root=tmp_path)
    session_path = mgr.init_session("test-codex-backend", cook_session=True, backend=codex_backend)

    codex_skills = session_path / CODEX_SKILLS_SUBDIR
    claude_skills = session_path / _SKILLS_SUBDIR
    assert codex_skills.is_dir()
    skill_files = list(codex_skills.glob("*/SKILL.md"))
    assert len(skill_files) > 0
    assert not claude_skills.exists()


def test_init_session_returns_validated_add_dir_for_default_backend(tmp_path: Path) -> None:
    """init_session() returns a ValidatedAddDir regardless of backend."""
    from autoskillit.core import ValidatedAddDir

    provider = SkillsDirectoryProvider()
    mgr = DefaultSessionSkillManager(provider, ephemeral_root=tmp_path)
    result = mgr.init_session("test-validated-return", cook_session=True)
    assert isinstance(result, ValidatedAddDir)


def test_default_session_skill_manager_satisfies_protocol() -> None:
    """DefaultSessionSkillManager satisfies the SessionSkillManager Protocol."""
    from autoskillit.core.types._type_protocols_workspace import SessionSkillManager

    provider = SkillsDirectoryProvider()
    mgr = DefaultSessionSkillManager(provider, ephemeral_root=Path("/tmp/dummy"))
    assert isinstance(mgr, SessionSkillManager)
