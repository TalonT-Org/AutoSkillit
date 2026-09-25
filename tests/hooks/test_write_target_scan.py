"""Direct expectations for the shared evaluated-command write-target scan."""

from __future__ import annotations

import pytest

from autoskillit.hooks import scan_write_targets

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.small]

_WORKSPACE = "/workspace"
_WRITE_TARGET_CASES: list[tuple[str, str, tuple[str, ...], bool]] = [
    ("echo x > /path/out.txt", _WORKSPACE, ("/path/out.txt",), False),
    ("cat file | tee /path/out.txt", _WORKSPACE, ("/path/out.txt",), False),
    ("cat /path/file.txt", _WORKSPACE, (), False),
    ("/autoskillit:test-skill-flat", _WORKSPACE, (), False),
    ("gh api /repos/owner/repo", _WORKSPACE, (), False),
    ("cp source.txt /path/dest.txt", _WORKSPACE, ("/path/dest.txt",), False),
    ("install -m 644 a /workspace/dest", _WORKSPACE, ("/workspace/dest",), False),
    ("sed -i 's/a/b/' /path/file.txt", _WORKSPACE, ("/path/file.txt",), False),
    ("mv /old/file /new/file", _WORKSPACE, ("/new/file",), False),
    ("cat /a | grep foo > /b", _WORKSPACE, ("/b",), False),
    ("cmd 2>/dev/null", _WORKSPACE, (), False),
    ("echo x > /dev/stderr", _WORKSPACE, (), False),
    ("tee /dev/null", _WORKSPACE, (), False),
    ("echo x > output.txt", _WORKSPACE, ("/workspace/output.txt",), False),
    ("BASE_DIR=/source/repo/configs", _WORKSPACE, (), False),
    ("echo x >> /path/out.txt", _WORKSPACE, ("/path/out.txt",), False),
    ("echo x 2> /path/err.txt", _WORKSPACE, ("/path/err.txt",), False),
    ("echo x 2>/path/err.txt", _WORKSPACE, ("/path/err.txt",), False),
    (
        "(echo x > /path/nested.txt) > /path/outer.txt)",
        _WORKSPACE,
        ("/path/nested.txt", "/path/outer.txt)"),
        False,
    ),
    ("echo x 2>&1", _WORKSPACE, (), False),
    ("git checkout branch -- /path/file.txt", _WORKSPACE, ("/path/file.txt",), False),
    ("rm /path/file.txt", _WORKSPACE, ("/path/file.txt",), False),
    (
        "cp /a /outside/dest > /log",
        _WORKSPACE,
        ("/outside/dest", "/log"),
        False,
    ),
    (
        "mv /a /outside/dest > /log",
        _WORKSPACE,
        ("/outside/dest", "/log"),
        False,
    ),
    ("cat file | tee /path/a /path/b", _WORKSPACE, ("/path/a", "/path/b"), False),
    ("echo x > $UNKNOWN_DIR/out.txt", _WORKSPACE, (), True),
    ("echo x > $MY_DIR/out.txt", _WORKSPACE, ("/workspace/.autoskillit/temp/out.txt",), False),
    (
        "sudo git checkout -- ../outside/evil.txt",
        _WORKSPACE,
        ("/workspace/../outside/evil.txt",),
        False,
    ),
    ("sudo -u root git checkout -- /path/file.txt", _WORKSPACE, ("/path/file.txt",), False),
    ("sudo --user=root rm /path/file.txt", _WORKSPACE, ("/path/file.txt",), False),
    ("nice -n 5 tee /path/out.txt", _WORKSPACE, ("/path/out.txt",), False),
    (
        "FOO=bar git checkout -- ../outside/evil.txt",
        _WORKSPACE,
        ("/workspace/../outside/evil.txt",),
        False,
    ),
    ("env -C /tmp git checkout -- /path/file.txt", _WORKSPACE, ("/path/file.txt",), False),
    ("timeout 30 tee /path/out.txt", _WORKSPACE, ("/path/out.txt",), False),
    ("timeout 30 patch /path/file.txt p.diff", _WORKSPACE, ("/path/file.txt",), False),
    ("sudo nohup", _WORKSPACE, (), False),
    ("env -- tee /path/out.txt", _WORKSPACE, ("/path/out.txt",), False),
    ("env --chdir=/tmp tee /path/out.txt", _WORKSPACE, ("/path/out.txt",), False),
    ("env --chdir /tmp tee /path/out.txt", _WORKSPACE, ("/path/out.txt",), False),
    ("env CACHE_DIR=/tmp tee /path/out.txt", _WORKSPACE, ("/path/out.txt",), False),
    ("sudo", _WORKSPACE, (), False),
    ("CACHE_DIR=/tmp", _WORKSPACE, (), False),
    ("patch /path/file.txt < diff.patch", _WORKSPACE, ("/path/file.txt",), False),
    ("unlink /path/file.txt", _WORKSPACE, ("/path/file.txt",), False),
    (
        "git --namespace refs/foo checkout -- /clone/src/main.py",
        _WORKSPACE,
        ("/clone/src/main.py",),
        False,
    ),
    ("git reset --hard HEAD", _WORKSPACE, (), False),
    ("gh api /repos/owner/repo/pulls", _WORKSPACE, (), False),
    ("cat /source/repo/README.md > /tmp/out.txt", _WORKSPACE, ("/tmp/out.txt",), False),
    ("echo hello > /tmp/out.txt", _WORKSPACE, ("/tmp/out.txt",), False),
    ("echo hello >> /path/log.txt", _WORKSPACE, ("/path/log.txt",), False),
    ("echo x > $MY_OUTPUT_DIR/out.txt", _WORKSPACE, (), True),
    ("tee $MY_OUTPUT_DIR/out.txt", _WORKSPACE, (), True),
    (
        "REVIEW_OUTPUT_DIR='.autoskillit/temp' && echo x > $REVIEW_OUTPUT_DIR/out.txt",
        _WORKSPACE,
        (),
        True,
    ),
    ("python3 - <<'EOF'\nif x > 3:\n    pass\nEOF", _WORKSPACE, (), False),
    (
        "cat <<'EOF' > /workspace/out.txt\nbody content\nEOF",
        _WORKSPACE,
        ("/workspace/out.txt",),
        False,
    ),
    (
        "cat <<EOF > /real/file.txt\nbody\nEOF",
        _WORKSPACE,
        ("/real/file.txt",),
        False,
    ),
    ("echo ok&&pip install -e .", _WORKSPACE, (), False),
    ("echo ok;pip install -e .", _WORKSPACE, (), False),
    ('echo "pip && install -e"', _WORKSPACE, (), False),
    ("FOO=bar pip install -e . > /tmp/x", _WORKSPACE, ("/tmp/x",), False),
    ("sudo pip install -e . > /tmp/x", _WORKSPACE, ("/tmp/x",), False),
    ("env PIP_CACHE_DIR=/tmp pip install -e . > /tmp/x", _WORKSPACE, ("/tmp/x",), False),
]


@pytest.mark.parametrize(
    ("command", "cwd", "expected_targets", "expected_unresolved"),
    _WRITE_TARGET_CASES,
    ids=[case[0].splitlines()[0][:48] for case in _WRITE_TARGET_CASES],
)
def test_scan_write_target_expectations(
    command: str,
    cwd: str,
    expected_targets: tuple[str, ...],
    expected_unresolved: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MY_DIR", "/workspace/.autoskillit/temp")
    for name in ("UNKNOWN_DIR", "MY_OUTPUT_DIR", "REVIEW_OUTPUT_DIR"):
        monkeypatch.delenv(name, raising=False)

    result = scan_write_targets(command, cwd)

    assert result.targets == expected_targets, f"targets for {command!r}"
    assert result.unresolved is expected_unresolved, f"unresolved status for {command!r}"
