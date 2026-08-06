"""State-root resolution contracts for guards running from sibling worktrees.

Kitchen configuration and session markers live under the orchestrating project
root, while a PreToolUse payload can point at a sibling worktree whose checked-in
``.autoskillit/`` directory is not an ancestor of that state. The tests preserve
the explicit state-root signal, upward-walk fallback, and process-cwd fallback
used to resolve those topologies consistently.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import unittest.mock
from pathlib import Path

import pytest

_HOOKS_SRC = str(Path(__file__).resolve().parents[2] / "src" / "autoskillit" / "hooks")
if _HOOKS_SRC not in sys.path:
    sys.path.insert(0, _HOOKS_SRC)

from _hook_payload import resolve_state_root  # type: ignore[import-not-found]  # noqa: E402

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.small]

_STATE_ROUTING_ENV_VARS = frozenset(
    {
        "AUTOSKILLIT_CAMPAIGN_ID",
        "AUTOSKILLIT_STATE_DIR",
        "AUTOSKILLIT_STATE_ROOT",
    }
)


# ---------------------------------------------------------------------------
# resolve_state_root() resolution order
# ---------------------------------------------------------------------------


class TestResolveStateRootOrder:
    def test_env_var_wins_over_walk_and_process_cwd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_root = tmp_path / "env-root"
        env_root.mkdir()
        (env_root / ".autoskillit").mkdir()

        walk_root = tmp_path / "walk-root"
        walk_sub = walk_root / "a" / "b"
        walk_sub.mkdir(parents=True)
        (walk_root / ".autoskillit").mkdir()

        cwd_root = tmp_path / "cwd-root"
        cwd_root.mkdir()

        monkeypatch.setenv("AUTOSKILLIT_STATE_ROOT", str(env_root))
        with unittest.mock.patch("pathlib.Path.cwd", return_value=cwd_root):
            result = resolve_state_root(str(walk_sub))
        assert result == env_root

    def test_walk_upward_finds_nearest_autoskillit_when_no_env_var(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("AUTOSKILLIT_STATE_ROOT", raising=False)
        project_root = tmp_path / "project"
        sub = project_root / "a" / "b" / "c"
        sub.mkdir(parents=True)
        (project_root / ".autoskillit").mkdir()

        result = resolve_state_root(str(sub))
        assert result == project_root

    def test_walk_upward_stops_at_nearest_ancestor(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Two .autoskillit dirs in the ancestor chain — the nearer one wins."""
        monkeypatch.delenv("AUTOSKILLIT_STATE_ROOT", raising=False)
        outer = tmp_path / "outer"
        inner = outer / "inner"
        sub = inner / "a"
        sub.mkdir(parents=True)
        (outer / ".autoskillit").mkdir()
        (inner / ".autoskillit").mkdir()

        result = resolve_state_root(str(sub))
        assert result == inner

    def test_empty_payload_cwd_skips_walk_and_uses_process_cwd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Empty payload_cwd never attempts the upward walk — falls straight to
        Path.cwd(), avoiding any dependence on ancestor directories above
        tmp_path (which are outside test control and may themselves contain
        an unrelated .autoskillit from other host activity)."""
        monkeypatch.delenv("AUTOSKILLIT_STATE_ROOT", raising=False)
        with unittest.mock.patch("pathlib.Path.cwd", return_value=tmp_path):
            result = resolve_state_root("")
        assert result == tmp_path

    def test_no_env_var_no_payload_cwd_matches_process_cwd_exactly(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With both optional signals absent, process cwd is the exact fallback."""
        monkeypatch.delenv("AUTOSKILLIT_STATE_ROOT", raising=False)
        monkeypatch.chdir(tmp_path)
        assert resolve_state_root("") == Path.cwd()

    def test_env_var_resolves_symlinks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        real = tmp_path / "real-root"
        real.mkdir()
        link = tmp_path / "linked-root"
        link.symlink_to(real)

        monkeypatch.setenv("AUTOSKILLIT_STATE_ROOT", str(link))
        result = resolve_state_root("")
        assert result == real.resolve()
        assert result != link

    def test_walk_does_not_follow_symlinked_autoskillit_escaping_trust_anchor(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Issue #4319 regression: a symlinked .autoskillit/ pointing outside
        the project must never be accepted as a match — accepting it would
        let every caller's later `root / ".autoskillit" / "temp" / ...` file
        access transparently follow the symlink outside the trust anchor. The
        walk must skip it and keep looking further up the ancestor chain,
        landing on the next *real* .autoskillit/ directory (a safety-net
        grandparent here, kept entirely inside the test's own tmp_path so the
        assertion never depends on real filesystem state above it)."""
        monkeypatch.delenv("AUTOSKILLIT_STATE_ROOT", raising=False)
        escape_target = tmp_path / "outside-target"
        escape_target.mkdir()

        grandparent = tmp_path / "grandparent"
        (grandparent / ".autoskillit").mkdir(parents=True)
        project = grandparent / "project"
        sub = project / "a"
        sub.mkdir(parents=True)
        (project / ".autoskillit").symlink_to(escape_target)

        result = resolve_state_root(str(sub))
        assert result != project, "symlinked .autoskillit/ must never be accepted as a match"
        assert result == grandparent, (
            "walk must continue past the rejected symlink and find the next real ancestor"
        )


# ---------------------------------------------------------------------------
# Worktree-topology guard enforcement: state under the orchestrating project
# root; hook payload cwd pointing into a sibling worktree with its own
# checked-in .autoskillit/ that must NOT be mistaken for the real state root.
# ---------------------------------------------------------------------------


def _run_hook(mod_import_path: str, stdin_content: str, env: dict[str, str]) -> str:
    import importlib

    if mod_import_path in sys.modules:
        del sys.modules[mod_import_path]
    mod = importlib.import_module(mod_import_path)
    buf = io.StringIO()
    with (
        unittest.mock.patch.dict(os.environ, env, clear=True),
        unittest.mock.patch("sys.stdin", io.StringIO(stdin_content)),
        contextlib.redirect_stdout(buf),
    ):
        try:
            mod.main()
        except SystemExit as exc:
            assert exc.code in (0, None), f"unexpected exit code {exc.code!r}"
    return buf.getvalue()


def _is_denied(output: str) -> bool:
    if not output.strip():
        return False
    data = json.loads(output)
    return data.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


@pytest.fixture
def worktree_topology(tmp_path: Path) -> tuple[Path, Path]:
    """Build (orchestrating_project, sibling_worktree) — two unrelated
    directories, each with its own checked-in .autoskillit/, neither an
    ancestor of the other."""
    orchestrating = tmp_path / "orchestrating-project"
    (orchestrating / ".autoskillit" / "temp").mkdir(parents=True)
    sibling_worktree = tmp_path / "worktrees" / "impl-something"
    (sibling_worktree / ".autoskillit" / "temp").mkdir(parents=True)
    return orchestrating, sibling_worktree


class TestPrCreateGuardWorktreeTopology:
    def test_still_enforces_via_state_root_env_var(
        self, worktree_topology: tuple[Path, Path]
    ) -> None:
        orchestrating, sibling_worktree = worktree_topology
        (orchestrating / ".autoskillit" / "temp" / ".hook_config.json").write_text("{}")
        # The sibling worktree's own .autoskillit/temp/ has NO hook_config —
        # a guard that mistakenly resolved state there would fail-open.

        blocked_cmd = " ".join(["gh", "pr", "create", "--title", "x"])
        stdin = json.dumps(
            {
                "tool_name": "mcp__autoskillit__local__autoskillit__run_cmd",
                "tool_input": {"cmd": blocked_cmd, "cwd": str(sibling_worktree)},
                "cwd": str(orchestrating),
            }
        )
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("AUTOSKILLIT_SKILL_NAME", "AUTOSKILLIT_SESSION_TYPE")
        }
        env["AUTOSKILLIT_STATE_ROOT"] = str(orchestrating)
        out = _run_hook("autoskillit.hooks.guards.pr_create_guard", stdin, env)
        assert _is_denied(out), (
            "pr_create_guard must still enforce when the orchestrating project's "
            "kitchen state is reachable only via AUTOSKILLIT_STATE_ROOT, not the "
            "sibling worktree the run_cmd tool's own cwd points into"
        )


class TestGitOpsGuardWorktreeTopology:
    def test_still_enforces_via_state_root_env_var(
        self, worktree_topology: tuple[Path, Path]
    ) -> None:
        orchestrating, sibling_worktree = worktree_topology
        (orchestrating / ".autoskillit" / "temp" / ".hook_config.json").write_text("{}")

        stdin = json.dumps(
            {
                "tool_name": "mcp__autoskillit__local__autoskillit__run_cmd",
                "tool_input": {"cmd": "git commit --amend", "cwd": str(sibling_worktree)},
                "cwd": str(orchestrating),
            }
        )
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("AUTOSKILLIT_SKILL_NAME", "AUTOSKILLIT_SESSION_TYPE")
        }
        env["AUTOSKILLIT_HEADLESS"] = "1"
        env["AUTOSKILLIT_STATE_ROOT"] = str(orchestrating)
        out = _run_hook("autoskillit.hooks.guards.git_ops_guard", stdin, env)
        assert _is_denied(out), (
            "git_ops_guard must still enforce a destructive op when the kitchen "
            "config is reachable only via AUTOSKILLIT_STATE_ROOT"
        )


class TestAskUserQuestionGuardWorktreeTopology:
    def test_does_not_spuriously_deny(self, worktree_topology: tuple[Path, Path]) -> None:
        from datetime import UTC, datetime

        orchestrating, sibling_worktree = worktree_topology
        marker_dir = orchestrating / ".autoskillit" / "temp" / "kitchen_state"
        marker_dir.mkdir(parents=True, exist_ok=True)
        (marker_dir / "sess-xyz.json").write_text(
            json.dumps(
                {
                    "session_id": "sess-xyz",
                    "opened_at": datetime.now(UTC).isoformat(),
                    "recipe_name": "test-recipe",
                    "marker_version": 1,
                }
            )
        )
        # No marker anywhere under sibling_worktree — a guard that resolved
        # state there (or fell back to process cwd) would spuriously deny.

        stdin = json.dumps(
            {
                "tool_name": "AskUserQuestion",
                "tool_input": {},
                "session_id": "sess-xyz",
                "cwd": str(sibling_worktree),
            }
        )
        env = {k: v for k, v in os.environ.items() if k not in _STATE_ROUTING_ENV_VARS}
        env["AUTOSKILLIT_HEADLESS"] = "1"
        env["AUTOSKILLIT_STATE_ROOT"] = str(orchestrating)
        with unittest.mock.patch("pathlib.Path.cwd", return_value=sibling_worktree):
            out = _run_hook("autoskillit.hooks.guards.ask_user_question_guard", stdin, env)
        assert out.strip() == "", (
            f"ask_user_question_guard spuriously denied in the worktree topology: {out!r}"
        )

    def test_denies_when_state_root_genuinely_has_no_marker(
        self, worktree_topology: tuple[Path, Path]
    ) -> None:
        """Companion negative case: the fix must not make the guard blind —
        with no marker anywhere (including under AUTOSKILLIT_STATE_ROOT), it
        still denies."""
        orchestrating, sibling_worktree = worktree_topology

        stdin = json.dumps(
            {
                "tool_name": "AskUserQuestion",
                "tool_input": {},
                "session_id": "sess-none",
                "cwd": str(sibling_worktree),
            }
        )
        env = {k: v for k, v in os.environ.items() if k not in _STATE_ROUTING_ENV_VARS}
        env["AUTOSKILLIT_HEADLESS"] = "1"
        env["AUTOSKILLIT_STATE_ROOT"] = str(orchestrating)
        out = _run_hook("autoskillit.hooks.guards.ask_user_question_guard", stdin, env)
        assert _is_denied(out)
