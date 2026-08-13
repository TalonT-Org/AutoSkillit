"""Tests for --profile flag in cook command."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import autoskillit.cli.session._session_cook as cook_module
from autoskillit.config import AutomationConfig
from autoskillit.core import (
    BackendConventions,
    CmdSpec,
    CompiledSessionSkillCatalogAuthority,
    CookSessionHandle,
    HookTrustPolicy,
    ManagedSessionHome,
    RepositoryProfileId,
    SkillExecutionRole,
    SkillProjectionContextAuthority,
    SkillSource,
    ValidatedAddDir,
    pkg_root,
)
from autoskillit.execution.backends import ClaudeCodeBackend, CodexBackend
from autoskillit.workspace import (
    CompiledSessionSkillCatalog,
    EffectiveSkillCatalog,
    SkillCatalogEntry,
)
from autoskillit.workspace.skills import _skill_info_from_frontmatter
from tests.fakes import adapt_test_skill_semantics

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium]


def _make_mock_backend_class():
    captured = []

    class _MockBackend:
        name = "claude-code"
        conventions = BackendConventions()
        exploration_dispatch_renderer = ClaudeCodeBackend().exploration_dispatch_renderer
        capabilities = SimpleNamespace(
            hook_trust_policy=HookTrustPolicy.AUTOMATED,
            session_dir_persistent=False,
            session_scoped_explorer_capable=True,
            terminal_explorer_capable=False,
            supports_tool_list_changed=True,
            cook_exact_binding_probe_required=False,
            skill_injection_capable=False,
            plugin_install_capable=True,
        )
        adapt_skill_semantics = staticmethod(adapt_test_skill_semantics)

        def binary_name(self) -> str:
            return "claude"

        def recover_cook_history(self) -> None:
            return None

        def build_interactive_cmd(self, **kwargs):
            captured.append(kwargs.get("env_extras", {}))
            return CmdSpec(cmd=("claude",), env={})

        def validate_interactive_invocation(self, spec: CmdSpec) -> list[str]:
            return []

        @contextmanager
        def cook_session_context(self, **kwargs):
            yield CookSessionHandle(
                view_id="profile-view",
                pass_fds=(),
                _record_spawn=lambda _pid, _pgid: None,
                _record_reaped=lambda _pid, _pgid: None,
            )

    return _MockBackend, captured


@pytest.fixture()
def _mock_mgr():
    return MagicMock()


def _run_cook(profile, cfg, mock_mgr, generated_home: Path):
    mock_backend_cls, captured = _make_mock_backend_class()
    skills_dir = generated_home / "skills"
    skills_dir.mkdir(parents=True)

    @contextmanager
    def managed_session(
        launch_id: str,
        compilation: CompiledSessionSkillCatalogAuthority,
        projection_context: SkillProjectionContextAuthority,
    ):
        assert projection_context.catalog == compilation.catalog
        yield ManagedSessionHome(
            launch_id=launch_id,
            generated_home=generated_home,
            skills_dir=ValidatedAddDir(str(skills_dir)),
            pass_fds=(),
        )

    mock_mgr.managed_session.side_effect = managed_session
    with (
        patch("shutil.which", return_value="/usr/bin/claude"),
        patch("autoskillit.workspace.DefaultSessionSkillManager", return_value=mock_mgr),
        # cook() derives project_dir via the shared git-toplevel helper; pin it so
        # the test does not depend on the caller's checkout.
        patch("autoskillit.cli.session._session_cook.resolve_project_dir", Path.cwd),
        patch(
            "autoskillit.cli.session._session_process.run_cook_attempt",
            return_value=SimpleNamespace(pid=1, pgid=1, returncode=0),
        ),
        patch(
            "autoskillit.cli.session._session_reload.consume_reload_sentinel",
            return_value=None,
        ),
        patch("autoskillit.cli._onboarding.is_first_run", return_value=False),
        patch("autoskillit.core.write_registry_entry"),
        patch("autoskillit.config.load_config", return_value=cfg),
        patch(
            "autoskillit.cli.session._session_cook.is_feature_enabled",
            side_effect=lambda key, *a, **kw: key == "providers",
        ),
        patch("autoskillit.cli.ui._timed_input.timed_prompt", return_value=""),
    ):
        cook_module.cook(profile=profile, backend=mock_backend_cls())
    return captured


def test_cook_skips_repository_profile_resolution_for_ordinary_catalog(
    _mock_mgr: MagicMock,
    tmp_path: Path,
) -> None:
    cfg = MagicMock()
    cfg.experimental_enabled = True
    cfg.providers.profiles = {}
    ordinary_path = pkg_root() / "skills" / "open-kitchen" / "SKILL.md"
    ordinary_info = _skill_info_from_frontmatter(
        "open-kitchen", SkillSource.BUNDLED, ordinary_path
    )
    assert not ordinary_info.invalidities, ordinary_info.invalidities
    ordinary_compilation = CompiledSessionSkillCatalog(
        backend="claude-code",
        catalog=EffectiveSkillCatalog(
            skills=(SkillCatalogEntry.from_skill_info(ordinary_info),),
            execution_role=SkillExecutionRole.SESSION,
        ),
        unavailable=(),
    )

    with (
        patch(
            "autoskillit.workspace.compile_session_skill_catalog",
            return_value=ordinary_compilation,
        ),
        patch("autoskillit.exploration.resolve_repository_profile") as resolve_profile,
    ):
        _run_cook(None, cfg, _mock_mgr, tmp_path / "ordinary-home")

    resolve_profile.assert_not_called()


def test_cook_resolves_repository_profile_for_active_auto_vector(
    _mock_mgr: MagicMock,
    tmp_path: Path,
) -> None:
    cfg = MagicMock()
    cfg.experimental_enabled = True
    cfg.providers.profiles = {}

    with patch(
        "autoskillit.exploration.resolve_repository_profile",
        return_value=RepositoryProfileId.AUTOSKILLIT,
    ) as resolve_profile:
        _run_cook(None, cfg, _mock_mgr, tmp_path / "auto-home")

    resolve_profile.assert_called_once_with(Path.cwd())


def test_profile_valid_injects_provider_env_var(_mock_mgr, tmp_path: Path):
    """AUTOSKILLIT_PROVIDER_PROFILE must be in env_extras when --profile is given."""
    cfg = MagicMock()
    cfg.experimental_enabled = True
    cfg.providers.profiles = {"minimax": {"ANTHROPIC_BASE_URL": "https://minimax.example"}}
    captured = _run_cook("minimax", cfg, _mock_mgr, tmp_path / "generated-home")
    assert len(captured) >= 1, "build_interactive_cmd was not called"
    env = captured[0]
    assert env.get("AUTOSKILLIT_PROVIDER_PROFILE") == "minimax"


def test_profile_valid_injects_profile_env_vars(_mock_mgr, tmp_path: Path):
    """Profile's own env vars (API creds) must be injected into env_extras."""
    cfg = MagicMock()
    cfg.experimental_enabled = True
    cfg.providers.profiles = {
        "minimax": {"ANTHROPIC_BASE_URL": "https://mm.io", "ANTHROPIC_API_KEY": "sk-mm"}
    }
    captured = _run_cook("minimax", cfg, _mock_mgr, tmp_path / "generated-home")
    assert len(captured) >= 1, "build_interactive_cmd was not called"
    env = captured[0]
    assert env.get("ANTHROPIC_BASE_URL") == "https://mm.io"
    assert env.get("ANTHROPIC_API_KEY") == "sk-mm"


def test_profile_none_does_not_inject_provider_env(_mock_mgr, tmp_path: Path):
    """When profile=None, AUTOSKILLIT_PROVIDER_PROFILE must NOT appear in env_extras."""
    cfg = MagicMock()
    cfg.experimental_enabled = True
    cfg.providers.profiles = {}
    captured = _run_cook(None, cfg, _mock_mgr, tmp_path / "generated-home")
    assert len(captured) >= 1, "build_interactive_cmd was not called"
    env = captured[0]
    assert "AUTOSKILLIT_PROVIDER_PROFILE" not in env


def test_profile_feature_disabled_exits(capsys, _mock_mgr):
    """SystemExit(1) with informative message when providers feature is not enabled."""
    cfg = MagicMock()
    cfg.experimental_enabled = False
    cfg.providers.profiles = {"minimax": {}}
    mock_backend_cls, _ = _make_mock_backend_class()
    with (
        patch("shutil.which", return_value="/usr/bin/claude"),
        patch("autoskillit.workspace.DefaultSessionSkillManager", return_value=_mock_mgr),
        patch("autoskillit.config.load_config", return_value=cfg),
        patch("autoskillit.cli.session._session_cook.is_feature_enabled", return_value=False),
    ):
        with pytest.raises(SystemExit) as exc_info:
            cook_module.cook(profile="minimax", backend=mock_backend_cls())
    assert exc_info.value.code == 1
    assert "providers" in capsys.readouterr().err


def test_profile_unknown_exits(capsys, _mock_mgr):
    """SystemExit(1) with informative message listing known profiles for unknown name."""
    cfg = MagicMock()
    cfg.experimental_enabled = True
    cfg.providers.profiles = {"anthropic": {}, "openai": {}}
    mock_backend_cls, _ = _make_mock_backend_class()
    with (
        patch("shutil.which", return_value="/usr/bin/claude"),
        patch("autoskillit.workspace.DefaultSessionSkillManager", return_value=_mock_mgr),
        patch("autoskillit.config.load_config", return_value=cfg),
        patch("autoskillit.cli.session._session_cook.is_feature_enabled", return_value=True),
    ):
        with pytest.raises(SystemExit) as exc_info:
            cook_module.cook(profile="minimax", backend=mock_backend_cls())
    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "minimax" in err
    assert "anthropic" in err or "openai" in err


def test_finalized_profile_spec_is_shared_by_validator_context_and_child(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from autoskillit.core import (
        CookSessionHandle,
        HookTrustPolicy,
        ManagedSessionHome,
        ValidatedAddDir,
    )

    generated_home = tmp_path / "generated-home"
    skills_dir = generated_home / "skills"
    skills_dir.mkdir(parents=True)
    manager = MagicMock()
    captured: dict[str, object] = {}

    @contextmanager
    def managed_session(
        launch_id: str,
        compilation: CompiledSessionSkillCatalogAuthority,
        projection_context: SkillProjectionContextAuthority,
    ):
        assert projection_context.catalog == compilation.catalog
        yield ManagedSessionHome(
            launch_id=launch_id,
            generated_home=generated_home,
            skills_dir=ValidatedAddDir(str(skills_dir)),
            pass_fds=(3,),
        )

    manager.managed_session.side_effect = managed_session

    class _Backend:
        name = "codex"
        conventions = BackendConventions(
            project_local_skill_search_dirs=(".codex/skills", ".agents/skills"),
            persistent_session_root_subdir=Path("codex-sessions"),
            skill_sigil="$",
        )
        exploration_dispatch_renderer = CodexBackend().exploration_dispatch_renderer
        capabilities = SimpleNamespace(
            hook_trust_policy=HookTrustPolicy.REVIEW_EACH_SESSION,
            session_dir_persistent=True,
            session_scoped_explorer_capable=False,
            terminal_explorer_capable=True,
            supports_tool_list_changed=False,
            cook_startup_observer_capable=False,
            cook_exact_binding_probe_required=False,
            skill_injection_capable=True,
            plugin_install_capable=False,
        )
        adapt_skill_semantics = staticmethod(adapt_test_skill_semantics)

        def binary_name(self) -> str:
            return "codex"

        def recover_cook_history(self) -> None:
            return None

        def build_interactive_cmd(self, **kwargs: object) -> CmdSpec:
            captured["build_kwargs"] = kwargs
            env = dict(kwargs["env_extras"])  # type: ignore[arg-type]
            generated_home = kwargs["generated_home"]
            env["CODEX_HOME"] = str(generated_home)
            env["CODEX_SQLITE_HOME"] = str(generated_home)
            return CmdSpec(
                cmd=(
                    "codex",
                    "--profile",
                    "minimax",
                    "-c",
                    f"sqlite_home={generated_home!s}",
                ),
                env=env,
                cwd="/ambient-cwd-must-not-survive",
            )

        def validate_interactive_invocation(self, spec: CmdSpec) -> list[str]:
            captured["validated"] = spec
            return []

        @contextmanager
        def cook_session_context(self, **kwargs: object):
            captured["context"] = kwargs
            yield CookSessionHandle(
                view_id="view-1",
                pass_fds=(5,),
                _record_spawn=lambda _pid, _pgid: None,
                _record_reaped=lambda _pid, _pgid: None,
            )

    def run_attempt(spec: CmdSpec, **kwargs: object) -> object:
        captured["child"] = spec
        captured["pass_fds"] = kwargs["pass_fds"]
        kwargs["on_spawn"](1, 1)  # type: ignore[operator]
        trace = kwargs["trace"]
        trace.record_spawn()  # type: ignore[union-attr]
        trace.record_stage(  # type: ignore[union-attr]
            "hook_review",
            attempt=1,
            view_id="view-1",
        )
        kwargs["on_reaped"](1, 1)  # type: ignore[operator]
        return SimpleNamespace(pid=1, pgid=1, returncode=0)

    cfg = MagicMock()
    cfg.experimental_enabled = True
    cfg.providers.profiles = {
        "minimax": {
            "ANTHROPIC_BASE_URL": "https://minimax.example",
            "CODEX_HOME": "/caller-home",
            "CODEX_SQLITE_HOME": "/caller-sqlite-home",
        }
    }

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CODEX_HOME", "/ambient-home")
    monkeypatch.setenv("CODEX_SQLITE_HOME", "/ambient-sqlite-home")
    monkeypatch.setenv("AUTOSKILLIT_CODEX_STARTUP_TRACE", "1")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    with (
        patch("shutil.which", return_value="/usr/bin/codex"),
        patch("sys.stdin.isatty", return_value=True),
        patch("autoskillit.config.load_config", return_value=cfg),
        patch("autoskillit.cli.session._session_cook.is_feature_enabled", return_value=True),
        patch("autoskillit.workspace.DefaultSessionSkillManager", return_value=manager),
        patch("autoskillit.cli._onboarding.is_first_run", return_value=False),
        patch("autoskillit.cli.ui._timed_input.timed_prompt", return_value=""),
        patch("autoskillit.core.write_registry_entry"),
        patch("autoskillit.cli.session._session_launch.bind_session_owner"),
        patch(
            "autoskillit.cli.session._session_process.run_cook_attempt",
            side_effect=run_attempt,
        ),
        patch(
            "autoskillit.cli.session._session_reload.consume_reload_sentinel",
            return_value=None,
        ),
    ):
        cook_module.cook(profile="minimax", backend=_Backend())

    spec = captured["validated"]
    assert spec is captured["child"]
    assert isinstance(spec, CmdSpec)
    assert spec.cwd == str(tmp_path)
    assert spec.env["AUTOSKILLIT_PROVIDER_PROFILE"] == "minimax"
    assert spec.env["CODEX_HOME"] == str(generated_home)
    assert spec.env["CODEX_SQLITE_HOME"] == str(generated_home)
    assert "AUTOSKILLIT_CODEX_STARTUP_TRACE" not in spec.env
    assert any("sqlite_home=" in arg and str(generated_home) in arg for arg in spec.cmd)
    assert captured["pass_fds"] == (3, 5)


def test_cook_rejects_orchestrator_skill_in_l1_tier_before_launch(capsys) -> None:
    """Direct cook composition validates configured tiers before materialization.

    Composition-root rendering (2.3) catches SkillContractError around
    validate_skill_tier_roles and exits cleanly instead of letting a raw
    traceback propagate — the pin moves from `pytest.raises(SkillContractError)`
    to `pytest.raises(SystemExit)` plus an output assertion.
    """
    cfg = AutomationConfig()
    cfg.skills.tier2 = ["process-issues"]
    mock_backend_cls, _ = _make_mock_backend_class()
    with (
        patch("autoskillit.config.load_config", return_value=cfg),
        patch("autoskillit.workspace.DefaultSessionSkillManager") as manager_cls,
        # project_dir comes from the shared git-toplevel helper; pin it so the
        # "nothing launched" assertion below stays about launches.
        patch("autoskillit.cli.session._session_cook.resolve_project_dir", Path.cwd),
        patch("autoskillit.cli.session._session_process.run_cook_attempt") as run,
    ):
        with pytest.raises(SystemExit) as exc_info:
            cook_module.cook(backend=mock_backend_cls())

    assert exc_info.value.code == 1
    out = capsys.readouterr().out
    assert "process-issues" in out
    assert "ORCHESTRATOR" in out
    assert "Traceback" not in out
    manager_cls.assert_not_called()
    run.assert_not_called()


def test_cook_reports_fully_invalid_tier_skill_with_hint_and_doctor_pointer(
    tmp_path: Path, capsys
) -> None:
    """T5: a tier-configured skill invalid everywhere (no bundled fallback) reports
    its file path, a remediation hint, and an `autoskillit doctor` pointer — no
    traceback, clean exit."""
    skill_dir = tmp_path / ".claude" / "skills" / "my-broken"
    skill_dir.mkdir(parents=True)
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text(
        '---\nname: my-broken\n---\nSpawn via `Agent(model="sonnet")`.\n',
        encoding="utf-8",
    )

    cfg = AutomationConfig()
    cfg.skills.tier2 = ["my-broken"]
    mock_backend_cls, _ = _make_mock_backend_class()
    with (
        patch("autoskillit.config.load_config", return_value=cfg),
        patch("autoskillit.workspace.DefaultSessionSkillManager") as manager_cls,
        patch(
            "autoskillit.cli.session._session_cook.resolve_project_dir",
            return_value=tmp_path,
        ),
        patch("autoskillit.cli.session._session_process.run_cook_attempt") as run,
    ):
        with pytest.raises(SystemExit) as exc_info:
            cook_module.cook(backend=mock_backend_cls())

    assert exc_info.value.code == 1
    out = capsys.readouterr().out
    assert str(skill_path) in out
    assert "hint:" in out
    assert "autoskillit doctor" in out
    assert "Traceback" not in out
    manager_cls.assert_not_called()
    run.assert_not_called()
