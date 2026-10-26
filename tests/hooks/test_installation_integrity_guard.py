"""Real-subprocess coverage for the installation integrity guard."""

from __future__ import annotations

import errno
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from autoskillit.hooks._runtime import UNRESOLVED_WRITE_TARGET_REMEDIATION
from tests.conftest import production_interpreter_env

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.small]

SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "src/autoskillit/hooks/guards/installation_integrity_guard.py"
)


def _run(event: object, *, env: dict[str, str] | None = None) -> tuple[int, str]:
    child_env = production_interpreter_env()
    for key in tuple(child_env):
        if key in {"AUTOSKILLIT_HEADLESS", "AUTOSKILLIT_AGENT_BACKEND"} or key.startswith(
            "AUTOSKILLIT_ALLOWED_WRITE_PREFIX"
        ):
            child_env.pop(key)
    if env:
        child_env.update(env)
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        env=child_env,
    )
    return result.returncode, result.stdout


def _decision(stdout: str) -> str:
    return json.loads(stdout)["hookSpecificOutput"]["permissionDecision"]


def _bash(command: str) -> dict[str, object]:
    return {"tool_name": "Bash", "tool_input": {"command": command}}


@pytest.mark.parametrize(
    "command_template",
    [
        "cat > {target} <<'PYEOF'\nPYEOF",
        "cp /tmp/source {target}",
        "mv /tmp/source {target}",
        "patch -i {target}",
        "tee {target} </dev/null",
        "sed -i 's/a/b/' {target}",
        "install /tmp/source {target}",
    ],
)
def test_blocks_install_tree_bash_writes(tmp_path: Path, command_template: str) -> None:
    target = tmp_path / "lib/python3.13/site-packages/autoskillit/__init__.py"
    target.parent.mkdir(parents=True)
    code, stdout = _run(_bash(command_template.format(target=target)))

    assert code == 0
    assert _decision(stdout) == "deny"


def test_blocks_git_checkout_restore_into_install_tree(tmp_path: Path) -> None:
    target = tmp_path / "lib/python3.13/site-packages/autoskillit/x.py"
    code, stdout = _run(_bash(f"git checkout main -- {target}"))

    assert code == 0
    assert _decision(stdout) == "deny"
    assert (
        "code=protected-installation-target"
        in json.loads(stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    )


@pytest.mark.parametrize(
    "command_template",
    [
        "sudo git checkout -- {target}",
        "FOO=bar git checkout -- {target}",
        "timeout 30 patch {target} /tmp/p.diff",
    ],
)
def test_blocks_prefixed_install_tree_writes(tmp_path: Path, command_template: str) -> None:
    target = tmp_path / "lib/python3.13/site-packages/autoskillit/x.py"
    code, stdout = _run(_bash(command_template.format(target=target)))

    assert code == 0
    assert _decision(stdout) == "deny"
    assert (
        "code=protected-installation-target"
        in json.loads(stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    )


def test_allows_wrapped_write_outside_install_tree(tmp_path: Path) -> None:
    code, stdout = _run(_bash(f"timeout 30 tee {tmp_path / 'outside.txt'}"))

    assert code == 0
    assert stdout == ""


@pytest.mark.parametrize(
    "command_template",
    [
        "echo x > >(tee {target})",
        "sed --in-place=bak 's/a/b/' {target}",
    ],
)
def test_blocks_process_substitution_install_tree_write(
    tmp_path: Path, command_template: str
) -> None:
    target = tmp_path / "lib/python3.13/site-packages/autoskillit/x.py"
    code, stdout = _run(_bash(command_template.format(target=target)))

    assert code == 0
    assert _decision(stdout) == "deny"
    assert (
        "code=protected-installation-target"
        in json.loads(stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    )


@pytest.mark.parametrize(
    "command_template",
    [
        "(cp /tmp/s {target})",
        "{ cp /tmp/s {target}; }",
        "(rm -rf {target})",
        "(echo x > {target})",
        "true && (tee {target} < /dev/null)",
    ],
    ids=["subshell-copy", "brace-copy", "subshell-remove", "subshell-redirect", "chained-tee"],
)
def test_blocks_grouped_install_tree_writes(tmp_path: Path, command_template: str) -> None:
    target = tmp_path / "lib/python3.13/site-packages/autoskillit/x.py"
    command = command_template.replace("{target}", str(target))
    event = _bash(command)
    event["cwd"] = str(tmp_path)
    code, stdout = _run(event)

    assert code == 0
    # Grouping invariance keeps protected writes denied inside either shell group.
    assert _decision(stdout) == "deny"
    assert (
        "code=protected-installation-target"
        in json.loads(stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    )


def test_allows_comment_only_command() -> None:
    code, stdout = _run(_bash("# Run linting and tests"))

    assert code == 0
    assert stdout == ""


def test_allows_grouped_read_only_command() -> None:
    code, stdout = _run(_bash("(cd /tmp && ls)"))

    assert code == 0
    assert stdout == ""


def test_unresolved_deny_message_names_literal_remediation() -> None:
    code, stdout = _run(_bash('F=x; echo > "$F"'))

    assert code == 0
    assert _decision(stdout) == "deny"
    assert (
        UNRESOLVED_WRITE_TARGET_REMEDIATION
        in json.loads(stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    )


@pytest.mark.parametrize(
    "command_template",
    [
        'echo x > "$(echo {root})/probe.txt"',
        "echo x > `echo {root}`/probe.txt",
        'cp /tmp/s "$(echo {root})/probe.txt"',
        'tee "$(echo {root})/probe.txt"',
        "cp /tmp/s $(echo a {root})/probe.txt",
        "cp /tmp/s `echo a {root}`/probe.txt",
        "echo x > $[0]{root}/probe.txt",
        'cd "$(echo {root})" && echo x > probe.txt',
    ],
    ids=[
        "redirect-command-substitution",
        "redirect-backticks",
        "copy-command-substitution",
        "tee-command-substitution",
        "copy-unquoted-command-substitution",
        "copy-unquoted-backticks",
        "legacy-arithmetic",
        "dynamic-cd",
    ],
)
def test_blocks_substituted_install_tree_targets(tmp_path: Path, command_template: str) -> None:
    root = tmp_path / "lib/python3.13/site-packages/autoskillit"
    command = command_template.format(root=root)
    event = _bash(command)
    event["cwd"] = str(tmp_path)

    code, stdout = _run(event)

    assert code == 0
    assert _decision(stdout) == "deny"
    assert (
        "code=unresolved-write-target"
        in json.loads(stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    )


@pytest.mark.parametrize("tool_name", ["Write", "Edit"])
def test_blocks_install_tree_direct_writes(tmp_path: Path, tool_name: str) -> None:
    target = tmp_path / "lib/python3.13/site-packages/autoskillit/__init__.py"
    code, stdout = _run({"tool_name": tool_name, "tool_input": {"file_path": str(target)}})

    assert code == 0
    assert _decision(stdout) == "deny"


@pytest.mark.parametrize("tool_name", ["Write", "Edit"])
@pytest.mark.parametrize(
    "file_path", ["$(printf x)/notes.txt", "`printf x`/notes.txt", "~/notes.txt"]
)
def test_direct_write_paths_with_shell_syntax_are_literal(
    tmp_path: Path, tool_name: str, file_path: str
) -> None:
    protected_home = tmp_path / "lib/python3.13/site-packages/autoskillit"
    protected_home.mkdir(parents=True)
    code, stdout = _run(
        {"tool_name": tool_name, "cwd": str(tmp_path), "tool_input": {"file_path": file_path}},
        env={"HOME": str(protected_home)},
    )

    assert code == 0
    assert stdout == ""


@pytest.mark.parametrize("path", ["$HOME/probe.txt", "~/probe.txt"])
@pytest.mark.parametrize("tool_name", ["Bash", "Write", "Edit"])
def test_blocks_literal_quoted_expansion_under_install_tree(
    tmp_path: Path, tool_name: str, path: str
) -> None:
    root = tmp_path / "lib/python3.13/site-packages/autoskillit"
    root.mkdir(parents=True)
    home = tmp_path / "other-home"
    home.mkdir()
    event = (
        _bash(f"echo x > '{path}'")
        if tool_name == "Bash"
        else {"tool_name": tool_name, "tool_input": {"file_path": path}}
    )
    event["cwd"] = str(root)

    code, stdout = _run(event, env={"HOME": str(home)})

    assert code == 0
    assert _decision(stdout) == "deny"
    assert (
        "code=protected-installation-target"
        in json.loads(stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    )


def test_blocks_install_tree_apply_patch(tmp_path: Path) -> None:
    target = tmp_path / "lib/python3.13/site-packages/autoskillit/__init__.py"
    patch = f"*** Begin Patch\n*** Update File: {target}\n@@\n+x\n*** End Patch"
    code, stdout = _run({"tool_name": "apply_patch", "tool_input": {"command": patch}})

    assert code == 0
    assert _decision(stdout) == "deny"


def test_blocks_interpreter_write_into_install_tree(tmp_path: Path) -> None:
    target = tmp_path / "lib/python3.13/site-packages/autoskillit/__init__.py"
    code, stdout = _run(_bash(f"python3 -c \"open('{target}', 'w').write('')\""))

    assert code == 0
    assert _decision(stdout) == "deny"


@pytest.mark.parametrize(
    "error", [errno.ENOENT, errno.ENOTDIR, errno.EACCES, errno.ELOOP, errno.EIO]
)
def test_entry_stat_errors_only_allow_absent_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: int
) -> None:
    from autoskillit.hooks.guards import installation_integrity_guard as guard

    target = tmp_path / "entry"
    original_lstat = os.lstat

    def lstat(path, *args, **kwargs):
        if os.fspath(path) == str(target):
            raise OSError(error, "injected stat failure")
        return original_lstat(path, *args, **kwargs)

    monkeypatch.setattr(guard, "_known_roots", lambda: ())
    monkeypatch.setattr(guard.os, "lstat", lstat)

    assert guard._is_protected_entry_target(str(target)) is (
        error not in {errno.ENOENT, errno.ENOTDIR}
    )


@pytest.mark.parametrize("failure_site", ["destination_location", "destination_within"])
def test_entry_resolution_failure_has_unresolved_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys, failure_site: str
) -> None:
    from autoskillit.hooks.guards import installation_integrity_guard as guard

    def fail_resolution(*args):
        raise RuntimeError("injected resolution failure")

    event = _bash(f"ln -s source {tmp_path / 'entry'}")
    event["cwd"] = str(tmp_path)
    monkeypatch.setattr(guard, "_known_roots", lambda: (tmp_path / "protected",))
    monkeypatch.setattr(guard, failure_site, fail_resolution)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(event)))
    with pytest.raises(SystemExit) as result:
        guard.main()
    assert result.value.code == 0
    output = json.loads(capsys.readouterr().out)["hookSpecificOutput"]
    assert output["permissionDecision"] == "deny"
    assert "code=unresolved-write-target" in output["permissionDecisionReason"]


def test_blocks_hardlink_alias_of_uv_tool_file(tmp_path: Path) -> None:
    home = tmp_path / "home"
    original = home / ".local/share/uv/tools/autoskillit/lib/site-packages/autoskillit/core.py"
    original.parent.mkdir(parents=True)
    original.write_text("source", encoding="utf-8")
    alias = tmp_path / "ordinary-project" / "linked.py"
    alias.parent.mkdir()
    os.link(original, alias)

    code, stdout = _run(
        {"tool_name": "Write", "tool_input": {"file_path": str(alias)}},
        env={"HOME": str(home)},
    )

    assert code == 0
    assert _decision(stdout) == "deny"


def test_blocks_ln_replacement_of_hardlink_alias_of_uv_tool_file(tmp_path: Path) -> None:
    home = tmp_path / "home"
    original = home / ".local/share/uv/tools/autoskillit/lib/site-packages/autoskillit/core.py"
    original.parent.mkdir(parents=True)
    original.write_text("source", encoding="utf-8")
    alias = tmp_path / "ordinary-project" / "linked.py"
    alias.parent.mkdir()
    os.link(original, alias)

    code, stdout = _run(
        _bash(f"ln -f {tmp_path / 'replacement.py'} {alias}"),
        env={"HOME": str(home)},
    )

    assert code == 0
    assert _decision(stdout) == "deny"


@pytest.mark.parametrize(
    "command_template",
    [
        "npm install --prefix {target} package",
        "ln -sf /tmp/source {target}",
    ],
    ids=["npm-prefix", "ln-entry"],
)
def test_blocks_explicit_install_tree_destinations(tmp_path: Path, command_template: str) -> None:
    target = tmp_path / "lib/python3.13/site-packages/autoskillit/destination"

    code, stdout = _run(_bash(command_template.format(target=target)))

    assert code == 0
    assert _decision(stdout) == "deny"
    assert (
        "code=protected-installation-target"
        in json.loads(stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    )


@pytest.mark.parametrize(
    "command",
    [
        'npm install --prefix "$DESTINATION" package',
        'ln -sf /tmp/source "$DESTINATION"',
    ],
    ids=["npm-prefix", "ln-entry"],
)
def test_blocks_unresolved_explicit_destinations(
    command: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DESTINATION", raising=False)
    code, stdout = _run(_bash(command))

    assert code == 0
    assert _decision(stdout) == "deny"
    assert (
        "code=unresolved-write-target"
        in json.loads(stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    )


def test_ln_entry_location_does_not_follow_outside_symlink_into_install_tree(
    tmp_path: Path,
) -> None:
    protected = tmp_path / "lib/python3.13/site-packages/autoskillit/core.py"
    protected.parent.mkdir(parents=True)
    protected.write_text("protected", encoding="utf-8")
    outside_link = tmp_path / "ordinary-project/core.py"
    outside_link.parent.mkdir()
    outside_link.symlink_to(protected)

    code, stdout = _run(_bash(f"ln -sf {tmp_path / 'replacement.py'} {outside_link}"))

    assert code == 0
    assert stdout == ""


def test_ln_entry_location_protects_symlink_inside_install_tree(tmp_path: Path) -> None:
    protected_link = tmp_path / "lib/python3.13/site-packages/autoskillit/core.py"
    protected_link.parent.mkdir(parents=True)
    outside = tmp_path / "ordinary-project/core.py"
    outside.parent.mkdir()
    outside.write_text("outside", encoding="utf-8")
    protected_link.symlink_to(outside)

    code, stdout = _run(_bash(f"ln -sf {tmp_path / 'replacement.py'} {protected_link}"))

    assert code == 0
    assert _decision(stdout) == "deny"


def test_blocks_shell_local_variable_target(tmp_path: Path) -> None:
    target = tmp_path / "lib/python3.13/site-packages/autoskillit/__init__.py"
    code, stdout = _run(_bash(f"FOO={target.parent}; cat > $FOO/__init__.py <<'EOF'\nEOF"))

    assert code == 0
    assert _decision(stdout) == "deny"


def test_blocks_symlinked_install_target(tmp_path: Path) -> None:
    package = tmp_path / "lib/python3.13/site-packages/autoskillit"
    package.mkdir(parents=True)
    linked = tmp_path / "linked-package"
    linked.symlink_to(package, target_is_directory=True)
    code, stdout = _run(_bash(f"cat > {linked}/new.py <<'EOF'\nEOF"))

    assert code == 0
    assert _decision(stdout) == "deny"


def test_blocks_relative_target_after_cd_into_install_tree(tmp_path: Path) -> None:
    package = tmp_path / "lib/python3.13/site-packages/autoskillit"
    package.mkdir(parents=True)
    event = _bash(f"cd {package} && cat > __init__.py <<'EOF'\nEOF")
    event["cwd"] = str(tmp_path)
    code, stdout = _run(event)

    assert code == 0
    assert _decision(stdout) == "deny"


@pytest.mark.parametrize(
    "command_template",
    [
        "cd -P {package} && echo x > output.py",
        "pushd {package} && echo x > output.py",
        "(cd {package} && echo x > output.py)",
        "{{ cd {package}; echo x > output.py; }}",
    ],
    ids=["cd-physical", "pushd", "subshell", "brace-group"],
)
def test_blocks_relative_write_after_cd_forms_into_install_tree(
    tmp_path: Path, command_template: str
) -> None:
    package = tmp_path / "lib/python3.13/site-packages/autoskillit"
    package.mkdir(parents=True)
    event = _bash(command_template.format(package=package))
    event["cwd"] = str(tmp_path)

    code, stdout = _run(event)

    assert code == 0
    assert _decision(stdout) == "deny"
    assert (
        "code=protected-installation-target"
        in json.loads(stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    )


def test_blocks_install_target_after_unresolved_cd(tmp_path: Path) -> None:
    target = tmp_path / "lib/python3.13/site-packages/autoskillit/output.py"
    event = _bash(f"cd - && echo x > {target}")
    event["cwd"] = str(tmp_path)

    code, stdout = _run(event)

    assert code == 0
    assert _decision(stdout) == "deny"
    assert (
        "code=protected-installation-target"
        in json.loads(stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    )


def test_allows_absolute_write_after_unresolved_cd(tmp_path: Path) -> None:
    root = tmp_path / "lib/python3.13/site-packages/autoskillit"
    root.mkdir(parents=True)
    outside = tmp_path / "outside.txt"
    event = _bash(f'cd "$(git rev-parse --show-toplevel)" && echo x > {outside}')
    event["cwd"] = str(root)

    code, stdout = _run(event)

    assert code == 0
    assert stdout == ""


def test_blocks_tilde_expansion_into_install_tree(tmp_path: Path) -> None:
    home = tmp_path / "home"
    target = "~/.local/share/uv/tools/autoskillit/lib/site-packages/autoskillit/output.py"

    code, stdout = _run(_bash(f"echo x > {target}"), env={"HOME": str(home)})

    assert code == 0
    assert _decision(stdout) == "deny"
    assert (
        "code=protected-installation-target"
        in json.loads(stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    )


def test_blocks_normalized_install_target(tmp_path: Path) -> None:
    target = tmp_path / "lib/python3.13/site-packages/../site-packages/autoskillit/__init__.py"
    code, stdout = _run(_bash(f"cat > {target} <<'EOF'\nEOF"))

    assert code == 0
    assert _decision(stdout) == "deny"


def test_allows_non_install_writes_and_reads(tmp_path: Path) -> None:
    project_file = tmp_path / "project/src/autoskillit/__init__.py"
    package_file = tmp_path / "lib/python3.13/site-packages/autoskillit/__init__.py"
    code, stdout = _run(_bash(f"cat > {project_file} <<'EOF'\nEOF"))

    assert code == 0
    assert stdout == ""

    code, stdout = _run(_bash(f"cat {package_file}"))

    assert code == 0
    assert stdout == ""


def test_allows_quoted_redirect_outside_install_tree(tmp_path: Path) -> None:
    target = tmp_path / "project.txt"

    code, stdout = _run(_bash(f'echo x > "{target}"'))

    assert code == 0
    assert stdout == ""


def test_allows_quoted_redirect_glyph_in_argument(tmp_path: Path) -> None:
    event = _bash(r"printf '> quoted\n' > .autoskillit/temp/x.md")
    event["cwd"] = str(tmp_path)

    code, stdout = _run(event)

    assert code == 0
    assert stdout == ""


def test_allow_non_install_direct_write(tmp_path: Path) -> None:
    code, stdout = _run(
        {"tool_name": "Write", "tool_input": {"file_path": str(tmp_path / "project.py")}}
    )

    assert code == 0
    assert stdout == ""


def test_allows_project_relative_and_temp_writes(tmp_path: Path) -> None:
    for target in ("project.py", ".autoskillit/temp/report.md"):
        code, stdout = _run(
            {"tool_name": "Write", "cwd": str(tmp_path), "tool_input": {"file_path": target}}
        )
        assert code == 0
        assert stdout == ""


@pytest.mark.parametrize("command", ["uv tool install autoskillit", "pip install -e ."])
def test_allows_install_commands_without_a_write_target(command: str) -> None:
    code, stdout = _run(_bash(command))

    assert code == 0
    assert stdout == ""


def test_applies_to_codex_and_headless(tmp_path: Path) -> None:
    target = tmp_path / "lib/python3.13/site-packages/autoskillit/__init__.py"
    event = _bash(f"cat > {target} <<'EOF'\nEOF")

    for env in ({"AUTOSKILLIT_AGENT_BACKEND": "codex"}, {"AUTOSKILLIT_HEADLESS": "1"}):
        code, stdout = _run(event, env=env)
        assert code == 0
        assert _decision(stdout) == "deny"
