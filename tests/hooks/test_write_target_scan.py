"""Direct expectations for the shared evaluated-command write-target scan."""

from __future__ import annotations

from pathlib import Path

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


@pytest.mark.parametrize(
    ("command", "targets", "unresolved", "has_write"),
    [
        ("npm install --prefix /opt/app pkg", ("/opt/app",), False, True),
        ("npm install --prefix=/opt/app pkg", ("/opt/app",), False, True),
        ("npm --prefix /opt/app install pkg", ("/opt/app",), False, True),
        ("npm install --prefix 'relative app' pkg", ("/workspace/relative app",), False, True),
        ('npm install --prefix="$NPM_PREFIX" pkg', ("/resolved/npm",), False, True),
        ("npm install --prefix='$NPM_UNKNOWN' pkg", ("/workspace/$NPM_UNKNOWN",), False, True),
        ('npm install --prefix="$NPM_UNKNOWN" pkg', (), True, True),
        ("npm install --prefix", (), True, True),
        ("npm install -- --prefix /opt/app", (), False, False),
    ],
    ids=(
        "detached-after-install",
        "equals-after-install",
        "detached-before-install",
        "quoted-relative",
        "attached-known-variable",
        "attached-single-quoted-variable",
        "attached-unknown-variable",
        "missing-prefix-value",
        "option-terminator",
    ),
)
def test_npm_install_explicit_prefix(
    command: str,
    targets: tuple[str, ...],
    unresolved: bool,
    has_write: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NPM_PREFIX", "/resolved/npm")
    monkeypatch.delenv("NPM_UNKNOWN", raising=False)

    result = scan_write_targets(command, _WORKSPACE)

    assert result.targets == targets
    assert result.entry_targets == ()
    assert result.unresolved is unresolved
    assert result.has_write is has_write


@pytest.mark.parametrize(
    ("command", "entry_targets"),
    [
        ("ln source link", ("/workspace/link",)),
        ("ln -s source link", ("/workspace/link",)),
        ("ln -f source link", ("/workspace/link",)),
        ("ln -sf source link", ("/workspace/link",)),
        ("ln source", ("/workspace/source",)),
        ("ln -t /links path/to/source", ("/links/source",)),
        ("ln --target-directory /links source", ("/links/source",)),
        ("ln --target-directory=/links source", ("/links/source",)),
        ("ln -t/links source", ("/links/source",)),
        ("ln -t /links one/a two/b", ("/links/a", "/links/b")),
    ],
)
def test_ln_literal_entry_destinations(command: str, entry_targets: tuple[str, ...]) -> None:
    result = scan_write_targets(command, _WORKSPACE)

    assert result.targets == ()
    assert result.entry_targets == entry_targets
    assert result.unresolved is False
    assert result.has_write is True


def test_ln_directory_and_final_symlink_semantics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "directory"
    directory.mkdir()
    directory_link = tmp_path / "directory-link"
    directory_link.symlink_to(directory, target_is_directory=True)
    monkeypatch.delenv("LN_SOURCE", raising=False)
    cases = (
        (f"ln source {directory}", (str(directory / "source"),), False),
        (f"ln source {directory_link}", (str(directory_link / "source"),), False),
        (f'ln -T "$LN_SOURCE" {directory_link}', (str(directory_link),), False),
        (f'ln -n "$LN_SOURCE" {directory_link}', (str(directory_link),), False),
        (f"ln -n source {directory}", (str(directory / "source"),), False),
        (f'ln "$LN_SOURCE" {directory}', (), True),
    )

    for command, entries, unresolved in cases:
        result = scan_write_targets(command, str(tmp_path))
        assert result.entry_targets == entries, command
        assert result.unresolved is unresolved, command
        assert result.has_write is True, command


@pytest.mark.parametrize(
    "command",
    [
        "ln --unknown value source link",
        "ln -t /links -T source",
        "ln --target-directory /links --no-target-directory source",
        "ln --target-directory /links",
        "ln source --target-directory",
        "install -t /dest -T source",
        "install --target-directory /dest --no-target-directory source",
        "install source --target-directory",
        "install --target-directory /dest",
    ],
)
def test_unsupported_or_incomplete_destination_grammar_is_unresolved(command: str) -> None:
    result = scan_write_targets(command, _WORKSPACE)

    assert result.targets == ()
    assert result.entry_targets == ()
    assert result.unresolved is True
    assert result.has_write is True


@pytest.mark.parametrize(
    ("command", "targets"),
    [
        ("install -t /dest path/to/source", ("/dest/source",)),
        ("install path/to/source -t /dest", ("/dest/source",)),
        ("install -t/dest path/to/source", ("/dest/source",)),
        ("install --target-directory=/dest path/to/source", ("/dest/source",)),
        ("install --target-directory /dest one/a two/b", ("/dest/a", "/dest/b")),
    ],
)
def test_install_target_directory_emits_child_content_destinations(
    command: str, targets: tuple[str, ...]
) -> None:
    result = scan_write_targets(command, _WORKSPACE)

    assert result.targets == targets
    assert result.entry_targets == ()
    assert result.unresolved is False
    assert result.has_write is True


@pytest.mark.parametrize(
    ("command", "targets", "entry_targets"),
    [
        (
            'ln --target-directory="$TARGET_DIRECTORY" source',
            (),
            ("/resolved/target/source",),
        ),
        (
            "ln --target-directory='$UNKNOWN_DIRECTORY' source",
            (),
            ("/workspace/$UNKNOWN_DIRECTORY/source",),
        ),
        (
            'install --target-directory="$TARGET_DIRECTORY" path/to/source',
            ("/resolved/target/source",),
            (),
        ),
    ],
)
def test_attached_target_directory_preserves_value_provenance(
    command: str,
    targets: tuple[str, ...],
    entry_targets: tuple[str, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TARGET_DIRECTORY", "/resolved/target")
    monkeypatch.delenv("UNKNOWN_DIRECTORY", raising=False)

    result = scan_write_targets(command, _WORKSPACE)

    assert result.targets == targets
    assert result.entry_targets == entry_targets
    assert result.unresolved is False
    assert result.has_write is True


def test_install_derived_child_preserves_spelling_and_requires_source_basename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "dest"
    destination.mkdir()
    child = destination / "source"
    child.symlink_to(tmp_path / "outside")
    monkeypatch.delenv("INSTALL_SOURCE", raising=False)

    literal = scan_write_targets(f"install -t {destination} path/to/source", str(tmp_path))
    unresolved = scan_write_targets(f'install -t {destination} "$INSTALL_SOURCE"', str(tmp_path))

    assert literal.targets == (str(child),)
    assert unresolved.targets == ()
    assert unresolved.unresolved is True
    assert unresolved.has_write is True


@pytest.mark.parametrize(
    ("command", "targets", "entry_targets"),
    [
        (
            "cd /tmp && npm install --prefix /tmp/jqinst node-jq 2>&1 | tail -10; "
            "ls /tmp/jqinst/node_modules/node-jq/bin 2>/dev/null",
            ("/tmp/jqinst",),
            (),
        ),
        (
            "ln -sf /tmp/jqinst/node_modules/node-jq/bin/jq /usr/local/bin/jq 2>&1; "
            "jq --version 2>&1",
            (),
            ("/usr/local/bin/jq",),
        ),
        ("echo x > /tmp/same; ln source /tmp/same", ("/tmp/same",), ("/tmp/same",)),
        ("echo x > /dev/null; ln -f source /dev/null", (), ("/dev/null",)),
    ],
    ids=("npm-incident", "ln-incident", "same-spelling-dual-role", "entry-pseudo-device"),
)
def test_content_and_entry_aggregation(
    command: str, targets: tuple[str, ...], entry_targets: tuple[str, ...]
) -> None:
    result = scan_write_targets(command, _WORKSPACE)

    assert result.targets == targets
    assert result.entry_targets == entry_targets
    assert result.unresolved is False
    assert result.has_write is True
