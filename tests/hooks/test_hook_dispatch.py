"""Tests for the stable hook dispatcher (_dispatch.py)."""

from __future__ import annotations

import io
import json
import shlex
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from autoskillit.hook_registry import HOOKS_DIR, generate_hooks_json
from tests.conftest import production_interpreter_env

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.medium]

DISPATCH_SCRIPT = HOOKS_DIR / "_dispatch.py"


def _copy_dispatcher(hooks_dir: Path) -> None:
    """Install the dispatcher with its stdlib-only settings sibling."""
    (hooks_dir / "_dispatch.py").write_text(DISPATCH_SCRIPT.read_text())
    (hooks_dir / "_hook_settings.py").write_text((HOOKS_DIR / "_hook_settings.py").read_text())


@pytest.fixture()
def hook_env(tmp_path: Path) -> Path:
    """Create a temporary hooks directory with _dispatch.py and a test target."""
    hooks_dir = tmp_path / "hooks"
    guards_dir = hooks_dir / "guards"
    guards_dir.mkdir(parents=True)

    _copy_dispatcher(hooks_dir)

    target_script = guards_dir / "quota_guard.py"
    target_script.write_text(
        textwrap.dedent("""\
            import sys
            print("HOOK_OK")
            sys.exit(0)
        """)
    )

    return hooks_dir


@pytest.fixture()
def stdin_echo_env(tmp_path: Path) -> Path:
    """Create a hooks directory with a target that echoes stdin to stdout."""
    hooks_dir = tmp_path / "hooks"
    guards_dir = hooks_dir / "guards"
    guards_dir.mkdir(parents=True)

    _copy_dispatcher(hooks_dir)

    target_script = guards_dir / "echo_hook.py"
    target_script.write_text(
        textwrap.dedent("""\
            import sys
            data = sys.stdin.read()
            sys.stdout.write(data)
            sys.exit(0)
        """)
    )

    return hooks_dir


class TestDispatchResolution:
    def test_dispatch_resolves_current_script(self, hook_env: Path) -> None:
        result = subprocess.run(
            [sys.executable, str(hook_env / "_dispatch.py"), "guards/quota_guard"],
            env=production_interpreter_env(),
            input=b'{"tool_name": "Read", "tool_input": {}}',
            capture_output=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert b"HOOK_OK" in result.stdout

    def test_dispatch_resolves_retired_name(self, tmp_path: Path) -> None:
        hooks_dir = tmp_path / "hooks"
        guards_dir = hooks_dir / "guards"
        guards_dir.mkdir(parents=True)

        _copy_dispatcher(hooks_dir)

        target = guards_dir / "skill_orchestration_guard.py"
        target.write_text(
            textwrap.dedent("""\
                import sys
                print("RENAMED_OK")
                sys.exit(0)
            """)
        )

        result = subprocess.run(
            [
                sys.executable,
                str(hooks_dir / "_dispatch.py"),
                "guards/leaf_orchestration_guard",
            ],
            env=production_interpreter_env(),
            input=b'{"tool_name": "Read", "tool_input": {}}',
            capture_output=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert b"RENAMED_OK" in result.stdout

    def test_dispatch_unknown_hook_exits_zero(self, hook_env: Path) -> None:
        result = subprocess.run(
            [sys.executable, str(hook_env / "_dispatch.py"), "guards/nonexistent_hook"],
            env=production_interpreter_env(),
            input=b'{"tool_name": "Read", "tool_input": {}}',
            capture_output=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert b"unknown hook" in result.stderr

    def test_dispatch_buffers_stdin(self, stdin_echo_env: Path) -> None:
        payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}})
        result = subprocess.run(
            [sys.executable, str(stdin_echo_env / "_dispatch.py"), "guards/echo_hook"],
            env=production_interpreter_env(),
            input=payload.encode(),
            capture_output=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert payload.encode() in result.stdout


class TestRetiredMappingIntegrity:
    def test_retired_mapping_targets_exist(self) -> None:
        from autoskillit.hook_registry import canonical_script_basenames
        from autoskillit.hooks._dispatch import _RETIRED_MAPPING

        canonical = canonical_script_basenames()
        canonical_logical = {s.removesuffix(".py") for s in canonical}

        for old_name, new_name in _RETIRED_MAPPING.items():
            target_path = HOOKS_DIR / (new_name + ".py")
            assert target_path.is_file(), (
                f"_RETIRED_MAPPING target missing: {old_name} -> {new_name} "
                f"(expected {target_path})"
            )
            assert old_name != new_name, f"Self-referencing entry: {old_name}"
            assert old_name not in canonical_logical, (
                f"Retired name '{old_name}' is still a live canonical name"
            )


@pytest.mark.parametrize(
    ("case", "expected_diagnostic", "expected_event_kind"),
    [
        ("unknown", "unknown hook: guards/not_a_registered_hook", "unknown_target"),
        ("missing_retired", "retired target missing:", "retired_target_missing"),
        ("exec_oserror", "exec failed for", "exec_failure"),
    ],
)
def test_dispatch_degrades_with_observable_exit_zero_diagnostic(
    case: str,
    expected_diagnostic: str,
    expected_event_kind: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Dispatch failures remain visible without turning hook invocation fatal."""
    from autoskillit.hooks import _dispatch

    logical_name = "guards/not_a_registered_hook"
    if case == "missing_retired":
        logical_name = "guards/retired_but_missing"
        monkeypatch.setattr(
            _dispatch,
            "_RETIRED_MAPPING",
            {logical_name: "guards/no_longer_present"},
        )
    elif case == "exec_oserror":
        logical_name = "guards/quota_guard"

        def _raise_oserror(*_args: object, **_kwargs: object) -> None:
            raise OSError("simulated exec failure")

        monkeypatch.setattr(_dispatch.subprocess, "run", _raise_oserror)

    monkeypatch.setattr(sys, "argv", ["_dispatch.py", logical_name])
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"{}")))
    monkeypatch.setenv("AUTOSKILLIT_LOG_DIR", str(tmp_path))

    with pytest.raises(SystemExit) as exit_info:
        _dispatch.main()

    assert exit_info.value.code == 0
    assert expected_diagnostic in capsys.readouterr().err
    records = [
        json.loads(line)
        for line in (tmp_path / "hook_dispatch_diagnostics.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    assert len(records) == 1
    assert records[0]["event_kind"] == expected_event_kind
    assert records[0]["logical_hook_name"] == logical_name
    assert expected_diagnostic in records[0]["reason"]


class TestCrossVersionSimulation:
    def test_stale_command_still_resolves_after_rename(self, tmp_path: Path) -> None:
        """Simulates the temporal bug: hook installed at version N, script renamed at N+1."""
        hooks_dir = tmp_path / "hooks"
        guards_dir = hooks_dir / "guards"
        guards_dir.mkdir(parents=True)

        _copy_dispatcher(hooks_dir)

        target = guards_dir / "skill_orchestration_guard.py"
        target.write_text(
            textwrap.dedent("""\
                import sys
                print("RESOLVED_VIA_RETIRED_MAPPING")
                sys.exit(0)
            """)
        )

        stale_command = f"python3 {hooks_dir / '_dispatch.py'} guards/leaf_orchestration_guard"
        parts = stale_command.split()

        result = subprocess.run(
            parts,
            input=b'{"tool_name": "mcp__autoskillit__run_skill", "tool_input": {}}',
            capture_output=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert b"RESOLVED_VIA_RETIRED_MAPPING" in result.stdout


class TestGenerateHooksJsonFormat:
    def test_generate_hooks_json_uses_dispatcher(self) -> None:
        hooks_json = generate_hooks_json()
        for event_type, entries in hooks_json["hooks"].items():
            for entry in entries:
                for hook in entry.get("hooks", []):
                    cmd = hook["command"]
                    assert "_dispatch.py" in cmd, f"Command does not use dispatcher: {cmd}"
                    # shlex, not bare .split(): commands are quoted
                    # (python3 "${CLAUDE_PLUGIN_ROOT}/hooks/_dispatch.py" name) so the
                    # dispatcher token carries a trailing quote that defeats a plain
                    # whitespace split's .endswith("_dispatch.py") check.
                    parts = shlex.split(cmd)
                    assert parts[-2].endswith("_dispatch.py"), (
                        f"Dispatcher not in expected position: {cmd}"
                    )
                    logical_name = parts[-1]
                    assert "/" in logical_name or logical_name in (
                        "session_start_hook",
                        "lint_after_edit_hook",
                        "token_summary_hook",
                        "quota_post_hook",
                        "quota_guard_state_post_hook",
                        "review_gate_post_hook",
                        "skill_load_post_hook",
                        "resume_gate_post_hook",
                        "recipe_confirmed_post_hook",
                        "shell_capture_hook",
                        "capture_lifecycle_hook",
                    ), f"Unexpected logical name format: {logical_name}"


@pytest.mark.medium
class TestDispatchOverhead:
    def test_dispatch_overhead_acceptable(self, hook_env: Path) -> None:
        direct_script = hook_env / "guards" / "quota_guard.py"
        dispatch_script = hook_env / "_dispatch.py"
        stdin_data = b'{"tool_name": "Read", "tool_input": {}}'
        runs = 10

        direct_times = []
        for _ in range(runs):
            start = time.perf_counter()
            subprocess.run(
                [sys.executable, str(direct_script)],
                env=production_interpreter_env(),
                input=stdin_data,
                capture_output=True,
            )
            direct_times.append(time.perf_counter() - start)

        dispatch_times = []
        for _ in range(runs):
            start = time.perf_counter()
            subprocess.run(
                [sys.executable, str(dispatch_script), "guards/quota_guard"],
                env=production_interpreter_env(),
                input=stdin_data,
                capture_output=True,
            )
            dispatch_times.append(time.perf_counter() - start)

        direct_median = sorted(direct_times)[runs // 2]
        dispatch_median = sorted(dispatch_times)[runs // 2]
        overhead = dispatch_median - direct_median
        assert overhead < 0.100, (
            f"Dispatch overhead {overhead:.3f}s exceeds 100ms threshold "
            f"(direct={direct_median:.3f}s, dispatch={dispatch_median:.3f}s)"
        )
