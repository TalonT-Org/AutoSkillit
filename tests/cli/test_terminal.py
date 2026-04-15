"""Tests for cli/_terminal.py terminal_guard() context manager.

Uses mock-based approach — no real subprocess needed. Pattern follows
the investigation's test strategy recommendation.
"""

from __future__ import annotations

import os
import termios
from unittest.mock import patch

import pytest

from autoskillit.cli._terminal import _RESET_SPEC


class TestTerminalGuardTTYRestore:
    """terminal_guard() saves and restores termios attrs in all exit paths."""

    def test_restores_on_normal_exit(self):
        """tcsetattr(TCSAFLUSH, saved_attrs) is called after normal yield exit."""
        from autoskillit.cli._terminal import terminal_guard

        fake_attrs = [0, 0, 0, 0, 0, 0, [b"\x00"] * 32]
        with (
            patch("autoskillit.cli._terminal.sys.stdin") as mock_stdin,
            patch("autoskillit.cli._terminal.termios") as mock_termios,
            patch("autoskillit.cli._terminal.sys.stdout"),
        ):
            mock_stdin.isatty.return_value = True
            mock_stdin.fileno.return_value = 0
            mock_termios.tcgetattr.return_value = fake_attrs
            mock_termios.TCSAFLUSH = termios.TCSAFLUSH
            mock_termios.error = termios.error

            with terminal_guard():
                pass

            mock_termios.tcsetattr.assert_called_once_with(0, termios.TCSAFLUSH, fake_attrs)

    def test_restores_on_keyboard_interrupt(self):
        """tcsetattr is called even when KeyboardInterrupt is raised inside the guard."""
        from autoskillit.cli._terminal import terminal_guard

        fake_attrs = [0, 0, 0, 0, 0, 0, [b"\x00"] * 32]
        with (
            patch("autoskillit.cli._terminal.sys.stdin") as mock_stdin,
            patch("autoskillit.cli._terminal.termios") as mock_termios,
            patch("autoskillit.cli._terminal.sys.stdout"),
        ):
            mock_stdin.isatty.return_value = True
            mock_stdin.fileno.return_value = 0
            mock_termios.tcgetattr.return_value = fake_attrs
            mock_termios.TCSAFLUSH = termios.TCSAFLUSH
            mock_termios.error = termios.error

            with pytest.raises(KeyboardInterrupt):
                with terminal_guard():
                    raise KeyboardInterrupt

            mock_termios.tcsetattr.assert_called_once_with(0, termios.TCSAFLUSH, fake_attrs)

    def test_restores_on_system_exit(self):
        """tcsetattr is called when SystemExit is raised (non-zero subprocess returncode)."""
        from autoskillit.cli._terminal import terminal_guard

        fake_attrs = [0, 0, 0, 0, 0, 0, [b"\x00"] * 32]
        with (
            patch("autoskillit.cli._terminal.sys.stdin") as mock_stdin,
            patch("autoskillit.cli._terminal.termios") as mock_termios,
            patch("autoskillit.cli._terminal.sys.stdout"),
        ):
            mock_stdin.isatty.return_value = True
            mock_stdin.fileno.return_value = 0
            mock_termios.tcgetattr.return_value = fake_attrs
            mock_termios.TCSAFLUSH = termios.TCSAFLUSH
            mock_termios.error = termios.error

            with pytest.raises(SystemExit):
                with terminal_guard():
                    raise SystemExit(1)

            mock_termios.tcsetattr.assert_called_once_with(0, termios.TCSAFLUSH, fake_attrs)

    @pytest.mark.parametrize("entry", _RESET_SPEC, ids=lambda e: e.name)
    def test_emits_all_spec_sequences_on_normal_exit(self, entry):
        """Every sequence in _RESET_SPEC is emitted by terminal_guard() on normal exit."""
        from autoskillit.cli._terminal import terminal_guard

        with (
            patch("autoskillit.cli._terminal.sys.stdin") as mock_stdin,
            patch("autoskillit.cli._terminal.termios"),
            patch("autoskillit.cli._terminal.sys.stdout") as mock_stdout,
        ):
            mock_stdin.isatty.return_value = True
            mock_stdin.fileno.return_value = 0

            with terminal_guard():
                pass

            written = "".join(c.args[0] for c in mock_stdout.write.call_args_list if c.args)
            assert entry.sequence in written, (
                f"Must emit {entry.name} ({entry.sequence!r}) — layer: {entry.layer.value}"
            )

    @pytest.mark.parametrize("entry", _RESET_SPEC, ids=lambda e: e.name)
    def test_emits_all_spec_sequences_on_exception(self, entry):
        """Every sequence in _RESET_SPEC is emitted by terminal_guard() on exception."""
        from autoskillit.cli._terminal import terminal_guard

        with (
            patch("autoskillit.cli._terminal.sys.stdin") as mock_stdin,
            patch("autoskillit.cli._terminal.termios"),
            patch("autoskillit.cli._terminal.sys.stdout") as mock_stdout,
        ):
            mock_stdin.isatty.return_value = True
            mock_stdin.fileno.return_value = 0

            with pytest.raises(KeyboardInterrupt):
                with terminal_guard():
                    raise KeyboardInterrupt

            written = "".join(c.args[0] for c in mock_stdout.write.call_args_list if c.args)
            assert entry.sequence in written, (
                f"Must emit {entry.name} ({entry.sequence!r}) — layer: {entry.layer.value}"
            )

    def test_noop_in_non_tty_environment(self):
        """When stdin is not a TTY, tcgetattr and tcsetattr are never called."""
        from autoskillit.cli._terminal import terminal_guard

        with (
            patch("autoskillit.cli._terminal.sys.stdin") as mock_stdin,
            patch("autoskillit.cli._terminal.termios") as mock_termios,
        ):
            mock_stdin.isatty.return_value = False

            with terminal_guard():
                pass

            mock_termios.tcgetattr.assert_not_called()
            mock_termios.tcsetattr.assert_not_called()

    def test_handles_tcgetattr_error_gracefully(self):
        """termios.error from tcgetattr does not propagate — guard becomes no-op."""
        from autoskillit.cli._terminal import terminal_guard

        with (
            patch("autoskillit.cli._terminal.sys.stdin") as mock_stdin,
            patch("autoskillit.cli._terminal.termios") as mock_termios,
            patch("autoskillit.cli._terminal.sys.stdout"),
        ):
            mock_stdin.isatty.return_value = True
            mock_stdin.fileno.return_value = 0
            mock_termios.error = termios.error
            mock_termios.tcgetattr.side_effect = termios.error("not a tty")

            with terminal_guard():  # must not raise
                pass

            mock_termios.tcsetattr.assert_not_called()

    def test_stty_fallback_on_tcsetattr_error(self):
        """os.system('stty sane') is called if tcsetattr raises termios.error."""
        from autoskillit.cli._terminal import terminal_guard

        fake_attrs = [0, 0, 0, 0, 0, 0, [b"\x00"] * 32]
        with (
            patch("autoskillit.cli._terminal.sys.stdin") as mock_stdin,
            patch("autoskillit.cli._terminal.termios") as mock_termios,
            patch("autoskillit.cli._terminal.sys.stdout"),
            patch("autoskillit.cli._terminal.os") as mock_os,
        ):
            mock_stdin.isatty.return_value = True
            mock_stdin.fileno.return_value = 0
            mock_termios.tcgetattr.return_value = fake_attrs
            mock_termios.TCSAFLUSH = termios.TCSAFLUSH
            mock_termios.error = termios.error
            mock_termios.tcsetattr.side_effect = termios.error("pipe")

            with terminal_guard():
                pass

            mock_os.system.assert_called_once_with("stty sane 2>/dev/null")

    def test_does_not_emit_entry_alt_screen_sequence(self):
        """terminal_guard() must NOT emit \\033[?1049h before yielding.

        DECSET 1049 (?1049h) is a boolean toggle — no nesting counter.
        The subprocess (e.g. Claude Code Ink TUI) emits its own ?1049h on
        startup. A prior ?1049h from terminal_guard() would overwrite the
        DECSC cursor save point, corrupting Ink's viewport height calculation
        and removing the scrollbar. terminal_guard() is an exit-only safety
        net: it must never emit entry-side terminal mode-switch sequences.

        Regression guard for: investigation_terminal_guard_alt_screen_scrollbar
        """
        from autoskillit.cli._terminal import terminal_guard

        writes_before_yield: list[str] = []
        all_writes: list[str] = []

        with (
            patch("autoskillit.cli._terminal.sys.stdin") as mock_stdin,
            patch("autoskillit.cli._terminal.termios"),
            patch("autoskillit.cli._terminal.sys.stdout") as mock_stdout,
        ):
            mock_stdin.isatty.return_value = True
            mock_stdin.fileno.return_value = 0
            mock_stdout.write.side_effect = lambda s: all_writes.append(s)

            with terminal_guard():
                writes_before_yield.extend(all_writes)

        assert not any("\033[?1049h" in s for s in writes_before_yield), (
            "terminal_guard() must not emit \\033[?1049h (smcup) on entry. "
            "The subprocess (e.g. Ink TUI) owns alt-screen entry. "
            f"Found in pre-yield writes: {writes_before_yield!r}"
        )

    def test_emits_exit_alt_screen_on_system_exit(self):
        """terminal_guard() emits \\033[?1049l in finally even when SystemExit raised."""
        from autoskillit.cli._terminal import terminal_guard

        with (
            patch("autoskillit.cli._terminal.sys.stdin") as mock_stdin,
            patch("autoskillit.cli._terminal.termios"),
            patch("autoskillit.cli._terminal.sys.stdout") as mock_stdout,
        ):
            mock_stdin.isatty.return_value = True
            mock_stdin.fileno.return_value = 0

            with pytest.raises(SystemExit):
                with terminal_guard():
                    raise SystemExit(1)

            written = "".join(c.args[0] for c in mock_stdout.write.call_args_list if c.args)
            assert "\033[?1049l" in written, (
                "\\033[?1049l (exit alternate screen) must be emitted on SystemExit"
            )

    def test_noop_does_not_emit_escape_sequences(self):
        """When stdin is not a TTY, no VT100 escape sequences are written to stdout."""
        from autoskillit.cli._terminal import terminal_guard

        with (
            patch("autoskillit.cli._terminal.sys.stdin") as mock_stdin,
            patch("autoskillit.cli._terminal.termios"),
            patch("autoskillit.cli._terminal.sys.stdout") as mock_stdout,
        ):
            mock_stdin.isatty.return_value = False

            with terminal_guard():
                pass

            mock_stdout.write.assert_not_called()

    def test_kitty_sequences_emitted_on_supported_terminal(self):
        """Kitty KBP sequences are emitted when TERM_PROGRAM indicates support."""
        from autoskillit.cli._terminal import terminal_guard

        with (
            patch("autoskillit.cli._terminal.sys.stdin") as mock_stdin,
            patch("autoskillit.cli._terminal.termios"),
            patch("autoskillit.cli._terminal.sys.stdout") as mock_stdout,
            patch.dict("os.environ", {"TERM_PROGRAM": "kitty"}, clear=False),
        ):
            mock_stdin.isatty.return_value = True
            mock_stdin.fileno.return_value = 0

            with terminal_guard():
                pass

            written = "".join(c.args[0] for c in mock_stdout.write.call_args_list if c.args)
            assert "\033[=0u" in written, "Must hard-disable Kitty keyboard protocol"
            assert "\033[<99u" in written, "Must drain Kitty keyboard protocol push stack"

    def test_kitty_sequences_not_emitted_on_unsupported_terminal(self):
        """Kitty KBP sequences must NOT be emitted on unsupported terminals.

        JediTerm (JetBrains IDEs) echoes literal garbage from \\033[<99u.
        """
        from autoskillit.cli._terminal import terminal_guard

        env = os.environ.copy()
        env.pop("TERM_PROGRAM", None)
        env.pop("KITTY_WINDOW_ID", None)

        with (
            patch("autoskillit.cli._terminal.sys.stdin") as mock_stdin,
            patch("autoskillit.cli._terminal.termios"),
            patch("autoskillit.cli._terminal.sys.stdout") as mock_stdout,
            patch.dict("os.environ", env, clear=True),
        ):
            mock_stdin.isatty.return_value = True
            mock_stdin.fileno.return_value = 0

            with terminal_guard():
                pass

            written = "".join(c.args[0] for c in mock_stdout.write.call_args_list if c.args)
            assert "\033[=0u" not in written, (
                "Kitty hard-disable must not be emitted on unsupported terminals"
            )
            assert "\033[<99u" not in written, (
                "Kitty stack drain must not be emitted on unsupported terminals"
            )
            # Base sequences must still be present
            assert "\033[?2004l" in written, "Base reset must still be emitted"
            assert "\033[!p" in written, "DECSTR must still be emitted"

    def test_kitty_sequences_emitted_via_kitty_window_id(self):
        """KITTY_WINDOW_ID triggers Kitty KBP sequences regardless of TERM_PROGRAM."""
        from autoskillit.cli._terminal import terminal_guard

        with (
            patch("autoskillit.cli._terminal.sys.stdin") as mock_stdin,
            patch("autoskillit.cli._terminal.termios"),
            patch("autoskillit.cli._terminal.sys.stdout") as mock_stdout,
            patch.dict("os.environ", {"KITTY_WINDOW_ID": "1"}, clear=False),
        ):
            mock_stdin.isatty.return_value = True
            mock_stdin.fileno.return_value = 0

            with terminal_guard():
                pass

            written = "".join(c.args[0] for c in mock_stdout.write.call_args_list if c.args)
            assert "\033[=0u" in written, "Must hard-disable Kitty keyboard protocol"

    def test_kitty_protocol_sequences_emitted_after_decstr(self):
        """Kitty KBP sequences must follow DECSTR to avoid being reset."""
        from autoskillit.cli._terminal import terminal_guard

        with (
            patch("autoskillit.cli._terminal.sys.stdin") as mock_stdin,
            patch("autoskillit.cli._terminal.termios"),
            patch("autoskillit.cli._terminal.sys.stdout") as mock_stdout,
            patch.dict("os.environ", {"TERM_PROGRAM": "kitty"}, clear=False),
        ):
            mock_stdin.isatty.return_value = True
            mock_stdin.fileno.return_value = 0

            with terminal_guard():
                pass

            written = "".join(c.args[0] for c in mock_stdout.write.call_args_list if c.args)
            decstr_pos = written.index("\033[!p")
            kitty_hard_pos = written.index("\033[=0u")
            kitty_drain_pos = written.index("\033[<99u")
            assert decstr_pos < kitty_hard_pos, "Kitty hard-disable must follow DECSTR"
            assert decstr_pos < kitty_drain_pos, "Kitty stack drain must follow DECSTR"


class TestResetSpecificationCompleteness:
    """Bidirectional completeness tests for the terminal reset specification.

    Follows the GATED_TOOLS pattern: a structured specification is tested
    via set operations to detect missing, extra, or duplicate entries.
    """

    def test_reset_spec_covers_all_layers(self):
        """Every ResetLayer enum member must have >= 1 entry in _RESET_SPEC."""
        from autoskillit.cli._terminal import _RESET_SPEC, ResetLayer

        covered_layers = {entry.layer for entry in _RESET_SPEC}
        missing = set(ResetLayer) - covered_layers
        assert not missing, (
            f"ResetLayer members {missing} have no entry in _RESET_SPEC. "
            "Every layer must have at least one reset sequence."
        )

    def test_base_reset_matches_spec_bidirectional(self):
        """_BASE_RESET must contain exactly the sequences in _RESET_SPEC."""
        import re

        from autoskillit.cli._terminal import _BASE_RESET, _RESET_SPEC

        spec_sequences = {entry.sequence for entry in _RESET_SPEC}
        for seq in spec_sequences:
            assert seq in _BASE_RESET, f"Spec sequence {seq!r} missing from _BASE_RESET"

        # Reverse: parse _BASE_RESET and verify every sequence is in the spec.
        found = set(re.findall(r"\033(?:[\[\(!][^a-zA-Z]*[a-zA-Z]|[>-~])", _BASE_RESET))
        unregistered = found - spec_sequences
        assert not unregistered, (
            f"Sequences in _BASE_RESET not registered in _RESET_SPEC: {unregistered}"
        )

    def test_content_layer_sequences_are_last(self):
        """CONTENT layer sequences must follow all other layers in _BASE_RESET."""
        from autoskillit.cli._terminal import _BASE_RESET, _RESET_SPEC, ResetLayer

        content_seqs = [e.sequence for e in _RESET_SPEC if e.layer == ResetLayer.CONTENT]
        other_seqs = [e.sequence for e in _RESET_SPEC if e.layer != ResetLayer.CONTENT]

        for content_seq in content_seqs:
            content_pos = _BASE_RESET.index(content_seq)
            for other_seq in other_seqs:
                other_pos = _BASE_RESET.index(other_seq)
                assert other_pos < content_pos, (
                    f"CONTENT sequence {content_seq!r} must follow "
                    f"non-CONTENT sequence {other_seq!r}"
                )

    def test_reset_spec_has_no_duplicate_sequences(self):
        """Each escape sequence must appear exactly once in _RESET_SPEC."""
        from autoskillit.cli._terminal import _RESET_SPEC

        sequences = [e.sequence for e in _RESET_SPEC]
        duplicates = [s for s in sequences if sequences.count(s) > 1]
        assert not duplicates, f"Duplicate sequences in _RESET_SPEC: {set(duplicates)}"


class TestCookTerminalGuard:
    """cook() and _launch_cook_session() apply terminal_guard correctly."""

    def test_cook_restores_terminal_on_keyboard_interrupt(self, monkeypatch, tmp_path):
        """cook() must restore terminal even when subprocess.run raises KeyboardInterrupt."""
        import autoskillit.cli._cook as cook_mod

        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        monkeypatch.setattr("sys.stdin.fileno", lambda: 0)
        tcsetattr_calls = []

        monkeypatch.setattr(
            "autoskillit.cli._terminal.termios.tcgetattr",
            lambda fd: [0, 0, 0, 0, 0, 0, []],
        )
        monkeypatch.setattr(
            "autoskillit.cli._terminal.termios.tcsetattr",
            lambda fd, when, attrs: tcsetattr_calls.append(attrs),
        )
        monkeypatch.setattr("autoskillit.cli._terminal.termios.error", termios.error)
        monkeypatch.setattr(
            "subprocess.run",
            lambda *a, **kw: (_ for _ in ()).throw(KeyboardInterrupt()),
        )
        monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/claude")
        monkeypatch.setattr("autoskillit.cli._init_helpers._is_plugin_installed", lambda: False)
        # is_first_run is imported inside cook() body — patch the source module
        monkeypatch.setattr("autoskillit.cli._onboarding.is_first_run", lambda _: False)
        # cook() calls input() for launch confirmation before subprocess.run
        monkeypatch.setattr("builtins.input", lambda _prompt="": "")
        # cook() calls init_session to create a skills directory
        fake_skills_dir = tmp_path / "fake-skills"
        fake_skills_dir.mkdir()
        monkeypatch.setattr(
            "autoskillit.workspace.session_skills.DefaultSessionSkillManager.init_session",
            lambda self, session_id, *, cook_session=False, config=None, project_dir=None: (
                fake_skills_dir
            ),
        )

        with pytest.raises(KeyboardInterrupt):
            cook_mod.cook()

        assert tcsetattr_calls, (
            "tcsetattr must be called even when subprocess raises KeyboardInterrupt"
        )

    def test_launch_cook_session_restores_terminal_on_keyboard_interrupt(self, monkeypatch):
        """_launch_cook_session() must restore terminal on exception."""
        import importlib

        app_mod = importlib.import_module("autoskillit.cli.app")

        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        monkeypatch.setattr("sys.stdin.fileno", lambda: 0)
        tcsetattr_calls = []

        monkeypatch.setattr(
            "autoskillit.cli._terminal.termios.tcgetattr",
            lambda fd: [0, 0, 0, 0, 0, 0, []],
        )
        monkeypatch.setattr(
            "autoskillit.cli._terminal.termios.tcsetattr",
            lambda fd, when, attrs: tcsetattr_calls.append(attrs),
        )
        monkeypatch.setattr("autoskillit.cli._terminal.termios.error", termios.error)
        monkeypatch.setattr(
            "subprocess.run",
            lambda *a, **kw: (_ for _ in ()).throw(KeyboardInterrupt()),
        )
        monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/claude")
        monkeypatch.setattr(app_mod, "_is_plugin_installed", lambda: False)

        with pytest.raises(KeyboardInterrupt):
            app_mod._launch_cook_session("system prompt")

        assert tcsetattr_calls, "terminal must be restored by _launch_cook_session on exception"
