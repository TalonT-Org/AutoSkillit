"""Tests for core/bash_write_targets.py — Bash write-target extraction."""

import pytest

from autoskillit.core.bash_write_targets import extract_bash_write_targets

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


class TestExtractBashWriteTargets:
    def test_redirect(self):
        assert extract_bash_write_targets("echo x > /path/out.txt") == ["/path/out.txt"]

    def test_tee(self):
        assert extract_bash_write_targets("cat file | tee /path/out.txt") == ["/path/out.txt"]

    def test_no_write(self):
        assert extract_bash_write_targets("cat /path/file.txt") == []

    def test_slash_command_clean(self):
        assert extract_bash_write_targets("/autoskillit:test-skill-flat") == []

    def test_gh_api_clean(self):
        assert extract_bash_write_targets("gh api /repos/owner/repo") == []

    def test_cp_destination(self):
        assert extract_bash_write_targets("cp source.txt /path/dest.txt") == ["/path/dest.txt"]

    def test_sed_inplace(self):
        assert extract_bash_write_targets("sed -i 's/a/b/' /path/file.txt") == ["/path/file.txt"]

    def test_mv_destination(self):
        assert extract_bash_write_targets("mv /old/file /new/file") == ["/new/file"]

    def test_pipe_chain(self):
        assert extract_bash_write_targets("cat /a | grep foo > /b") == ["/b"]

    def test_dev_null_excluded(self):
        assert extract_bash_write_targets("cmd 2>/dev/null") == []

    def test_pseudo_devices_excluded(self):
        assert extract_bash_write_targets("echo x > /dev/stderr") == []
        assert extract_bash_write_targets("tee /dev/null") == []

    def test_relative_path_resolved(self):
        result = extract_bash_write_targets("echo x > output.txt", cwd="/workspace")
        assert result == ["/workspace/output.txt"]

    def test_variable_assignment_clean(self):
        assert extract_bash_write_targets("BASE_DIR=/source/repo/configs") == []

    def test_append_redirect(self):
        assert extract_bash_write_targets("echo x >> /path/out.txt") == ["/path/out.txt"]

    def test_git_checkout_files(self):
        result = extract_bash_write_targets("git checkout branch -- /path/file.txt")
        assert result == ["/path/file.txt"]

    def test_rm_detected(self):
        result = extract_bash_write_targets("rm /path/file.txt")
        assert result == ["/path/file.txt"]

    def test_cp_with_redirect(self):
        result = extract_bash_write_targets("cp /a /outside/dest > /log")
        assert "/outside/dest" in result
        assert "/log" in result

    def test_mv_with_redirect(self):
        result = extract_bash_write_targets("mv /a /outside/dest > /log")
        assert "/outside/dest" in result
        assert "/log" in result

    def test_tee_multiple_targets(self):
        result = extract_bash_write_targets("cat file | tee /path/a /path/b")
        assert result == ["/path/a", "/path/b"]

    def test_shell_variable_unknown_failopen(self):
        result = extract_bash_write_targets("echo x > $UNKNOWN_DIR/out.txt", cwd="/workspace")
        assert result == []

    def test_shell_variable_known_expands(self, monkeypatch):
        monkeypatch.setenv("MY_DIR", "/workspace/.autoskillit/temp")
        result = extract_bash_write_targets("echo x > $MY_DIR/out.txt", cwd="/workspace")
        assert result == ["/workspace/.autoskillit/temp/out.txt"]


_PARITY_CORPUS: list[tuple[str, str]] = [
    ("echo x > /path/out.txt", "/workspace"),
    ("cat /path/file.txt", "/workspace"),
    ("cat file | tee /path/out.txt", "/workspace"),
    ("cp source.txt /path/dest.txt", "/workspace"),
    ("mv /old/file /new/file", "/workspace"),
    ("sed -i 's/a/b/' /path/file.txt", "/workspace"),
    ("rm /path/file.txt", "/workspace"),
    ("echo hello >> /path/log.txt", "/workspace"),
    ("cmd 2>/dev/null", "/workspace"),
    ("tee /dev/null", "/workspace"),
    ("echo x > /dev/stderr", "/workspace"),
    ("gh api /repos/owner/repo/pulls", "/workspace"),
    ("cat /source/repo/README.md > /tmp/out.txt", "/workspace"),
    ("echo hello > /tmp/out.txt", "/workspace"),
    ("git checkout branch -- /path/file.txt", "/workspace"),
    ("git reset --hard HEAD", "/workspace"),
    ("cat /a | grep foo > /b", "/workspace"),
    ("echo x > output.txt", "/workspace"),
    ("patch /path/file.txt < diff.patch", "/workspace"),
    ("unlink /path/file.txt", "/workspace"),
    ("cp /a /outside/dest > /log", "/workspace"),
    ("cat file | tee /path/a /path/b", "/workspace"),
    ("echo x > $MY_OUTPUT_DIR/out.txt", "/workspace"),
    ("tee $MY_OUTPUT_DIR/out.txt", "/workspace"),
    ("REVIEW_OUTPUT_DIR='.autoskillit/temp' && echo x > $REVIEW_OUTPUT_DIR/out.txt", "/workspace"),
    # Heredoc with > comparison in body — must NOT produce false write targets
    ("python3 - <<'EOF'\nif x > 3:\n    pass\nEOF", "/workspace"),
    # Heredoc with real redirect on opening line — must produce write target
    ("cat <<'EOF' > /workspace/out.txt\nbody content\nEOF", "/workspace"),
]


@pytest.mark.parametrize("command,cwd", _PARITY_CORPUS, ids=[c[0][:50] for c in _PARITY_CORPUS])
def test_parity_with_command_classification(
    command: str, cwd: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Core and hooks implementations must produce identical write targets."""
    import shlex
    from pathlib import Path

    hooks_dir = str(
        Path(__file__).resolve().parent.parent.parent / "src" / "autoskillit" / "hooks"
    )
    monkeypatch.syspath_prepend(hooks_dir)

    from _command_classification import (  # type: ignore[import-not-found]
        extract_redirect_targets as hooks_extract_redirect_targets,
    )
    from _command_classification import (
        strip_heredoc_bodies as hooks_strip_heredoc_bodies,
    )
    from _command_classification import (
        tokenize_command_segments as hooks_tokenize_command_segments,
    )
    from guards.write_guard import (  # type: ignore[import-not-found]
        _PSEUDO_DEVICE_PATHS as HOOKS_PSEUDO_DEVICE_PATHS,
    )
    from guards.write_guard import (
        _extract_segment_targets as hooks_extract_segment_targets,
    )

    core_result = extract_bash_write_targets(command, cwd)

    hooks_segments = hooks_tokenize_command_segments(command)
    hooks_all: list[str] = []
    for seg in hooks_segments:
        seg_result = hooks_extract_segment_targets(seg, cwd)
        if seg_result is not None:
            hooks_all.extend(seg_result)
    try:
        flat_tokens = shlex.split(hooks_strip_heredoc_bodies(command))
    except (ValueError, TypeError, AttributeError):
        flat_tokens = []
    redirect_paths = hooks_extract_redirect_targets(flat_tokens, cwd)
    for path in redirect_paths:
        if path not in HOOKS_PSEUDO_DEVICE_PATHS:
            hooks_all.append(path)
    seen: set[str] = set()
    hooks_unique: list[str] = []
    for t in hooks_all:
        if t not in seen:
            seen.add(t)
            hooks_unique.append(t)

    assert core_result == hooks_unique, (
        f"Parity mismatch for {command!r}: core={core_result}, hooks={hooks_unique}"
    )


def test_strip_heredoc_bodies_parity(monkeypatch: pytest.MonkeyPatch) -> None:
    """IL-0 and hooks implementations of strip_heredoc_bodies must agree."""
    from pathlib import Path

    hooks_dir = str(
        Path(__file__).resolve().parent.parent.parent / "src" / "autoskillit" / "hooks"
    )
    monkeypatch.syspath_prepend(hooks_dir)

    from _command_classification import (  # type: ignore[import-not-found]
        strip_heredoc_bodies as hooks_strip_heredoc_bodies,
    )

    from autoskillit.core.bash_write_targets import _strip_heredoc_bodies as core_strip

    cases = [
        "python3 - <<'EOF'\nif x > 3:\n    pass\nEOF",
        "cat <<EOF > /real/file.txt\nbody\nEOF",
        "echo hello > /dev/null",
        "cat <<-DELIM\n\tbody\n\tDELIM",
    ]
    for cmd in cases:
        assert core_strip(cmd) == hooks_strip_heredoc_bodies(cmd), f"Parity mismatch for: {cmd!r}"
