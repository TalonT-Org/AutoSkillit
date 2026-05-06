"""Tests: cook CLI order command — recipe picker, resume flows, session parsing."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from autoskillit import cli
from autoskillit.core import ClaudeFlags
from tests.cli.conftest import _SCRIPT_YAML

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium]


class TestCLIOrderPicker:
    @pytest.fixture(autouse=True)
    def _stub_preview(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Stub terminal preview to avoid subprocess.run collision with git calls."""
        monkeypatch.setattr(
            "autoskillit.cli._preview.show_cook_preview",
            lambda *a, **kw: None,
        )

    @pytest.fixture(autouse=True)
    def _interactive_stdin(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Most order() paths require an interactive TTY — default to True for this class."""
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    @pytest.fixture(autouse=True)
    def _stub_ingredients_table(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Stub _get_ingredients_table in app.py to prevent subprocess.run git calls."""
        import importlib
        import sys as _sys

        _app_mod = _sys.modules.get("autoskillit.cli.session._order") or importlib.import_module(
            "autoskillit.cli.session._order"
        )
        monkeypatch.setattr(_app_mod, "_get_ingredients_table", lambda *a, **kw: "| col | val |")

    @patch("autoskillit.cli.subprocess.run")
    def test_order_no_recipe_prompts_user(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """order prompts for recipe name when none is provided."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "my-script.yaml").write_text(_SCRIPT_YAML)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        monkeypatch.setattr("builtins.input", lambda _prompt="": "test-script")

        cli.order()  # no recipe argument

        mock_run.assert_called_once()

    def test_order_no_recipe_no_available_exits(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """order exits 1 when no recipe is given and no recipes are available."""
        import autoskillit.recipe as _recipe_mod

        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)

        mock_result = MagicMock()
        mock_result.items = []
        monkeypatch.setattr(_recipe_mod, "list_recipes", lambda *a, **kw: mock_result)

        with pytest.raises(SystemExit) as exc_info:
            cli.order()
        assert exc_info.value.code == 1

    @patch("autoskillit.cli.subprocess.run")
    def test_order_picker_shows_zero_option(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """Picker output includes '0. Open kitchen' line."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "my-script.yaml").write_text(_SCRIPT_YAML)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        monkeypatch.setattr("builtins.input", lambda _prompt="": "test-script")

        cli.order()

        captured = capsys.readouterr()
        assert "0. Open kitchen" in captured.out

    @patch("autoskillit.cli.subprocess.run")
    def test_order_picker_zero_launches_open_kitchen(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Typing '0' launches a session without a recipe YAML in the system prompt."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "my-script.yaml").write_text(_SCRIPT_YAML)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        monkeypatch.setattr("builtins.input", lambda _prompt="": "0")

        cli.order()

        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        system_prompt_idx = cmd.index(ClaudeFlags.APPEND_SYSTEM_PROMPT) + 1
        assert "--- RECIPE ---" not in cmd[system_prompt_idx]

    def test_order_picker_out_of_range_exits(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """Out-of-range numeric input exits 1 with an error message."""
        import autoskillit.recipe as _recipe_mod

        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)

        fake_recipe = MagicMock()
        fake_recipe.name = "some-recipe"
        mock_result = MagicMock()
        mock_result.items = [fake_recipe]
        monkeypatch.setattr(_recipe_mod, "list_recipes", lambda *a, **kw: mock_result)
        monkeypatch.setattr("builtins.input", lambda _prompt="": "99")

        with pytest.raises(SystemExit) as exc_info:
            cli.order()
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Invalid selection" in captured.out

    @patch("autoskillit.cli.subprocess.run")
    def test_order_resume_bare_flag_produces_bare_resume_skips_discovery(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """order(resume=True) passes bare --resume; find_latest_session_id must not be called."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "my-script.yaml").write_text(_SCRIPT_YAML)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        monkeypatch.setattr("builtins.input", lambda _prompt="": "")
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        discovery_calls: list = []

        with patch(
            "autoskillit.core.find_latest_session_id",
            side_effect=lambda *a, **kw: discovery_calls.append(1) or "sess_abc",
        ):
            cli.order("test-script", resume=True)

        cmd = mock_run.call_args[0][0]
        assert "--resume" in cmd
        idx = cmd.index("--resume")
        # bare --resume: next token (if any) must not be a session ID;
        # session IDs have no spaces, initial prompts and flags are distinguishable
        if idx + 1 < len(cmd):
            next_tok = str(cmd[idx + 1])
            assert " " in next_tok or next_tok.startswith("-"), (
                f"bare --resume must not be followed by a session ID, got: {next_tok!r}"
            )
        assert not discovery_calls, "find_latest_session_id must not be called for bare --resume"

    @patch("autoskillit.cli.subprocess.run")
    def test_order_resume_explicit_session_id_skips_discovery(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """order(resume=True, session_id='explicit-abc') uses explicit id; discovery not called."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "my-script.yaml").write_text(_SCRIPT_YAML)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        monkeypatch.setattr("builtins.input", lambda _prompt="": "")
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        discovery_calls: list = []

        def fake_discover(cwd=None):
            discovery_calls.append(cwd)
            return "should-not-be-used"

        with patch("autoskillit.core.find_latest_session_id", side_effect=fake_discover):
            cli.order("test-script", resume=True, session_id="explicit-abc")

        cmd = mock_run.call_args[0][0]
        assert "--resume" in cmd
        assert cmd[cmd.index("--resume") + 1] == "explicit-abc"
        assert not discovery_calls, (
            "find_latest_session_id must not be called when session_id is explicit"
        )

    @patch("autoskillit.cli.subprocess.run")
    def test_order_resume_bare_flag_always_emits_resume(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """order(resume=True) always emits bare --resume; Claude Code handles empty history."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "my-script.yaml").write_text(_SCRIPT_YAML)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        monkeypatch.setattr("builtins.input", lambda _prompt="": "")
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        cli.order("test-script", resume=True)

        cmd = mock_run.call_args[0][0]
        assert "--resume" in cmd
        idx = cmd.index("--resume")
        # bare --resume: next token (if any) must not be a session ID;
        # session IDs have no spaces, initial prompts and flags are distinguishable
        if idx + 1 < len(cmd):
            next_tok = str(cmd[idx + 1])
            assert " " in next_tok or next_tok.startswith("-"), (
                f"bare --resume must not be followed by a session ID, got: {next_tok!r}"
            )

    @patch("autoskillit.cli.subprocess.run")
    def test_order_bare_resume_no_recipe_invokes_picker(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """order(resume=True) with no recipe invokes pick_session for order sessions."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        monkeypatch.setattr("builtins.input", lambda _prompt="": "")
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        picker_calls: list = []

        def fake_pick_session(session_type: str, project_dir) -> None:
            picker_calls.append(session_type)
            return None

        with patch("autoskillit.cli.session._session_picker.pick_session", fake_pick_session):
            with patch("autoskillit.core.write_registry_entry"):
                cli.order(resume=True)

        assert picker_calls == ["order"], f"Expected ['order'], got {picker_calls}"

    def test_order_picker_renders_family_recipes_header(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Picker must print 'Family Recipes' header when project recipes exist."""
        from autoskillit.core import RecipeSource
        from autoskillit.recipe.schema import RecipeInfo

        project_recipe = RecipeInfo(
            name="proj-recipe",
            description="d",
            source=RecipeSource.PROJECT,
            path=tmp_path / "proj.yaml",
            experimental=False,
        )
        monkeypatch.setattr(
            "autoskillit.recipe.list_recipes",
            lambda *a, **kw: type("R", (), {"items": [project_recipe]})(),
        )
        monkeypatch.setattr("autoskillit.cli.ui._menu.timed_prompt", lambda *a, **kw: "0")
        monkeypatch.setattr(shutil, "which", lambda cmd: None)
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)

        with pytest.raises(SystemExit):
            cli.order()

        out = capsys.readouterr().out
        assert "Family Recipes" in out

    def test_order_picker_renders_bundled_recipes_header(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Picker must print 'Bundled Recipes' header when builtin recipes exist."""
        from autoskillit.core import RecipeSource
        from autoskillit.recipe.schema import RecipeInfo

        builtin_recipe = RecipeInfo(
            name="impl",
            description="d",
            source=RecipeSource.BUILTIN,
            path=tmp_path / "impl.yaml",
            experimental=False,
            requires_packs=["github"],
        )
        monkeypatch.setattr(
            "autoskillit.recipe.list_recipes",
            lambda *a, **kw: type("R", (), {"items": [builtin_recipe]})(),
        )
        monkeypatch.setattr("autoskillit.cli.ui._menu.timed_prompt", lambda *a, **kw: "0")
        monkeypatch.setattr(shutil, "which", lambda cmd: None)
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)

        with pytest.raises(SystemExit):
            cli.order()

        out = capsys.readouterr().out
        assert "Bundled Recipes" in out

    def test_order_picker_renders_experimental_header(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Picker must print 'Experimental' header when experimental recipes exist."""
        from autoskillit.core import RecipeSource
        from autoskillit.recipe.schema import RecipeInfo

        exp_recipe = RecipeInfo(
            name="research",
            description="d",
            source=RecipeSource.BUILTIN,
            path=tmp_path / "research.yaml",
            experimental=True,
        )
        monkeypatch.setattr(
            "autoskillit.recipe.list_recipes",
            lambda *a, **kw: type("R", (), {"items": [exp_recipe]})(),
        )
        monkeypatch.setattr("autoskillit.cli.ui._menu.timed_prompt", lambda *a, **kw: "0")
        monkeypatch.setattr(shutil, "which", lambda cmd: None)
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)

        with pytest.raises(SystemExit):
            cli.order()

        out = capsys.readouterr().out
        assert "Experimental" in out

    def test_order_picker_omits_empty_group_header(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Picker must not print 'Family Recipes' header when no project recipes exist."""
        from autoskillit.core import RecipeSource
        from autoskillit.recipe.schema import RecipeInfo

        builtin_recipe = RecipeInfo(
            name="impl",
            description="d",
            source=RecipeSource.BUILTIN,
            path=tmp_path / "impl.yaml",
            experimental=False,
        )
        monkeypatch.setattr(
            "autoskillit.recipe.list_recipes",
            lambda *a, **kw: type("R", (), {"items": [builtin_recipe]})(),
        )
        monkeypatch.setattr("autoskillit.cli.ui._menu.timed_prompt", lambda *a, **kw: "0")
        monkeypatch.setattr(shutil, "which", lambda cmd: None)
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)

        with pytest.raises(SystemExit):
            cli.order()

        out = capsys.readouterr().out
        assert "Family Recipes" not in out

    def test_order_picker_renders_bundled_add_ons_header(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Picker must print 'Bundled Add-ons' header for BUILTIN recipes with non-core packs."""
        from autoskillit.core import RecipeSource
        from autoskillit.recipe.schema import RecipeInfo

        addon_recipe = RecipeInfo(
            name="planner",
            description="d",
            source=RecipeSource.BUILTIN,
            path=tmp_path / "planner.yaml",
            experimental=False,
            requires_packs=["kitchen-core"],
        )
        monkeypatch.setattr(
            "autoskillit.recipe.list_recipes",
            lambda *a, **kw: type("R", (), {"items": [addon_recipe]})(),
        )
        monkeypatch.setattr("autoskillit.cli.ui._menu.timed_prompt", lambda *a, **kw: "0")
        monkeypatch.setattr(shutil, "which", lambda cmd: None)
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)

        with pytest.raises(SystemExit):
            cli.order()

        out = capsys.readouterr().out
        assert "Bundled Add-ons" in out

    def test_order_picker_contiguous_numbering_across_groups(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Picker numbering must be contiguous across all groups (1, 2, 3...)."""
        from autoskillit.core import RecipeSource
        from autoskillit.recipe.schema import RecipeInfo

        recipes = [
            RecipeInfo(
                name="proj-a",
                description="d",
                source=RecipeSource.PROJECT,
                path=tmp_path / "a.yaml",
                experimental=False,
            ),
            RecipeInfo(
                name="impl",
                description="d",
                source=RecipeSource.BUILTIN,
                path=tmp_path / "b.yaml",
                experimental=False,
            ),
            RecipeInfo(
                name="exp-r",
                description="d",
                source=RecipeSource.BUILTIN,
                path=tmp_path / "c.yaml",
                experimental=True,
            ),
        ]
        monkeypatch.setattr(
            "autoskillit.recipe.list_recipes", lambda *a, **kw: type("R", (), {"items": recipes})()
        )
        monkeypatch.setattr("autoskillit.cli.ui._menu.timed_prompt", lambda *a, **kw: "0")
        monkeypatch.setattr(shutil, "which", lambda cmd: None)
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)

        with pytest.raises(SystemExit):
            cli.order()

        out = capsys.readouterr().out
        numbered = re.findall(r"^\s+([1-9]\d*)\.", out, re.MULTILINE)
        numbered_ints = [int(n) for n in numbered]
        assert numbered_ints == list(range(1, len(recipes) + 1))


# REQ-CLI-003
class TestOrderResumeParsing:
    """CLI-level parsing tests for `order --resume [session-id]`."""

    @pytest.fixture(autouse=True)
    def _stub_preview(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Stub terminal preview to avoid subprocess calls."""
        monkeypatch.setattr(
            "autoskillit.cli._preview.show_cook_preview",
            lambda *a, **kw: None,
        )

    @pytest.fixture(autouse=True)
    def _interactive_stdin(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """order() requires an interactive TTY — default to True for this class."""
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    def test_order_recipe_resume_with_session_id(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """order my-recipe --resume <uuid> passes NamedResume to launch — REQ-CLI-003."""
        from autoskillit.cli.app import app
        from autoskillit.core import NamedResume, NoResume

        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)

        captured: dict = {}

        def fake_launch(
            prompt,
            *,
            initial_message=None,
            extra_env=None,
            resume_spec=NoResume(),
            project_dir=None,
        ):
            captured["resume_spec"] = resume_spec

        with (
            patch("autoskillit.cli.session._order._launch_cook_session", side_effect=fake_launch),
            patch(
                "autoskillit.recipe.find_recipe_by_name",
                return_value=MagicMock(path=tmp_path / "dummy.yaml", name="my-recipe"),
            ),
            patch("autoskillit.recipe.load_recipe", return_value=MagicMock()),
            patch("autoskillit.recipe.validate_recipe", return_value=[]),
            patch("builtins.input", return_value=""),
        ):
            with pytest.raises(SystemExit) as exc_info:
                app(["order", "my-recipe", "--resume", "fa910a41-d1ca-4cae-b878-01028a0c7c1c"])
            assert exc_info.value.code == 0

        assert captured["resume_spec"] == NamedResume(
            session_id="fa910a41-d1ca-4cae-b878-01028a0c7c1c"
        )

    def test_order_resume_uuid_without_recipe_routes_correctly(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """order --resume <uuid> (no recipe name) reroutes UUID to session_id — REQ-CLI-003."""
        from autoskillit.cli.app import app
        from autoskillit.core import NamedResume, NoResume

        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)

        captured: dict = {}

        def fake_launch(prompt, *, initial_message=None, extra_env=None, resume_spec=NoResume()):
            captured["resume_spec"] = resume_spec

        with patch("autoskillit.cli.session._order._launch_cook_session", side_effect=fake_launch):
            with pytest.raises(SystemExit) as exc_info:
                app(["order", "--resume", "4b581974-1f19-4aec-8405-78c5ede5e233"])
            assert exc_info.value.code == 0

        assert captured["resume_spec"] == NamedResume(
            session_id="4b581974-1f19-4aec-8405-78c5ede5e233"
        )

    def test_order_bare_resume_skips_recipe_validation(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """order --resume (no uuid, no recipe) calls _launch_cook_session; no recipe validation."""
        from autoskillit.cli.app import app
        from autoskillit.core import NoResume

        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)

        captured: dict = {}

        def fake_launch(prompt, *, initial_message=None, extra_env=None, resume_spec=NoResume()):
            captured["resume_spec"] = resume_spec

        with (
            patch("autoskillit.cli.session._order._launch_cook_session", side_effect=fake_launch),
            patch("autoskillit.cli.session._session_picker.pick_session", return_value=None),
        ):
            with pytest.raises(SystemExit) as exc_info:
                app(["order", "--resume"])
            assert exc_info.value.code == 0

        assert captured, "fake_launch was never called"
        assert captured["resume_spec"] == NoResume()

    def test_order_resume_uuid_does_not_validate_recipe(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """order --resume <uuid> bypasses find_recipe_by_name — UUID is not a recipe name."""
        from autoskillit.cli.app import app

        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)

        find_called = []
        monkeypatch.setattr(
            "autoskillit.recipe.find_recipe_by_name",
            lambda *a, **kw: find_called.append(a) or None,
        )

        with patch("autoskillit.cli.session._order._launch_cook_session"):
            with pytest.raises(SystemExit) as exc_info:
                app(["order", "--resume", "4b581974-1f19-4aec-8405-78c5ede5e233"])
            assert exc_info.value.code == 0

        assert find_called == [], "find_recipe_by_name must NOT be called on resume without recipe"
