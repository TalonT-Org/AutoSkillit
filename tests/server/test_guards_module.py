"""Tests that _guards functions document their orchestration level requirements."""

import pytest

from autoskillit.hooks import UNRESOLVED_WRITE_TARGET_REMEDIATION, WriteTargetScan

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


@pytest.mark.parametrize(
    ("command", "cwd_kind", "scope_enabled", "expected_message"),
    [
        pytest.param(
            'echo x > "$F"',
            "allowed",
            True,
            UNRESOLVED_WRITE_TARGET_REMEDIATION,
            id="variable-target-fails-closed",
        ),
        pytest.param(
            'cp a "$(echo /outside)/x"',
            "allowed",
            True,
            UNRESOLVED_WRITE_TARGET_REMEDIATION,
            id="subshell-target-fails-closed",
        ),
        pytest.param(
            "install /tmp/s /outside/x",
            "allowed",
            True,
            "/outside/x",
            id="install-outside-prefix",
        ),
        pytest.param(
            "printf '> q' > allowed/out.txt",
            "root",
            True,
            None,
            id="quoted-redirect-operator-is-not-a-target",
        ),
        pytest.param("echo x > out.txt", "allowed", True, None, id="inside-prefix"),
        pytest.param("echo x > /outside/y", "allowed", True, "/outside/y", id="outside-prefix"),
        pytest.param('echo x > "$F"', "allowed", False, None, id="empty-scope-remains-disabled"),
        pytest.param(
            "echo 'unterminated",
            "allowed",
            True,
            UNRESOLVED_WRITE_TARGET_REMEDIATION,
            id="unparseable-fails-closed",
        ),
        pytest.param(
            "echo x > out.txt", "relative-allowed", True, None, id="relative-cwd-is-resolved"
        ),
    ],
)
def test_check_write_target_boundary_cases(
    tmp_path, monkeypatch, command, cwd_kind, scope_enabled, expected_message
):
    from autoskillit.server.lifecycle._guards import _check_write_target_boundary

    monkeypatch.delenv("F", raising=False)
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    if cwd_kind == "root":
        cwd = str(tmp_path)
    elif cwd_kind == "relative-allowed":
        monkeypatch.chdir(tmp_path)
        cwd = "allowed"
    else:
        cwd = str(allowed)
    prefixes = (str(allowed),) if scope_enabled else ()

    result = _check_write_target_boundary(command, cwd, prefixes)

    if expected_message is not None:
        assert result is not None
        assert expected_message in result
    else:
        assert result is None


@pytest.mark.parametrize(
    ("command_template", "destination", "expected_message"),
    [
        pytest.param(
            "npm install --prefix {destination} node-jq",
            "allowed",
            None,
            id="npm-prefix-equals-allowed-root",
        ),
        pytest.param(
            "npm --prefix={destination} install node-jq",
            "outside",
            "outside allowed write prefixes",
            id="npm-outside-prefix",
        ),
        pytest.param(
            'npm install --prefix "$NPM_PREFIX" node-jq',
            "unresolved",
            UNRESOLVED_WRITE_TARGET_REMEDIATION,
            id="npm-unresolved-prefix",
        ),
        pytest.param(
            "ln -sfT /opt/tools/jq {destination}",
            "allowed",
            None,
            id="ln-entry-equals-allowed-root",
        ),
        pytest.param(
            "ln -sf /opt/tools/jq {destination}/jq",
            "outside",
            "outside allowed write prefixes",
            id="ln-outside-entry",
        ),
        pytest.param(
            'ln -sf /opt/tools/jq "$LINK_NAME"',
            "unresolved",
            UNRESOLVED_WRITE_TARGET_REMEDIATION,
            id="ln-unresolved-entry",
        ),
    ],
)
def test_run_cmd_boundary_covers_npm_and_ln_destinations(
    tmp_path, monkeypatch, command_template, destination, expected_message
) -> None:
    from autoskillit.server.lifecycle._guards import _check_write_target_boundary

    monkeypatch.delenv("NPM_PREFIX", raising=False)
    monkeypatch.delenv("LINK_NAME", raising=False)
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    selected = allowed if destination == "allowed" else outside
    command = command_template.format(destination=selected)

    result = _check_write_target_boundary(command, str(tmp_path), (str(allowed),))

    if expected_message is None:
        assert result is None
    else:
        assert result is not None
        assert expected_message in result


@pytest.mark.parametrize(
    "command_template",
    ["echo changed > {destination}", "ln -sfT /opt/tools/jq {destination}"],
    ids=["content-target", "entry-target"],
)
def test_run_cmd_boundary_denies_sibling_path_sharing_root_prefix(
    tmp_path, command_template: str
) -> None:
    from autoskillit.server.lifecycle._guards import _check_write_target_boundary

    allowed = tmp_path / "allowed"
    sibling = tmp_path / "allowed_extra"
    allowed.mkdir()
    sibling.mkdir()
    destination = sibling / "target"

    result = _check_write_target_boundary(
        command_template.format(destination=destination), str(tmp_path), (str(allowed),)
    )

    assert result is not None
    assert "outside allowed write prefixes" in result
    assert str(destination) in result


def test_run_cmd_boundary_does_not_early_allow_location_only_scan(tmp_path, monkeypatch) -> None:
    from autoskillit.server.lifecycle import _guards

    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    monkeypatch.setattr(
        _guards,
        "scan_write_targets",
        lambda _cmd, _cwd: WriteTargetScan(
            targets=(),
            unresolved=False,
            parseable=True,
            has_write=True,
            entry_targets=(str(outside / "link"),),
        ),
    )

    result = _guards._check_write_target_boundary("ln", str(tmp_path), (str(allowed),))

    assert result is not None
    assert str(outside / "link") in result


def test_run_cmd_entry_check_uses_link_location_not_referent(tmp_path) -> None:
    from autoskillit.server.lifecycle._guards import _check_write_target_boundary

    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    inside = allowed / "inside"
    inside.write_text("inside", encoding="utf-8")
    outside_link = outside / "link"
    outside_link.symlink_to(inside)

    result = _check_write_target_boundary(
        f"ln -sfT /opt/tools/jq {outside_link}", str(tmp_path), (str(allowed),)
    )

    assert result is not None
    assert str(outside_link) in result


def test_run_cmd_empty_prefixes_still_disable_new_destination_checks(tmp_path) -> None:
    from autoskillit.server.lifecycle._guards import _check_write_target_boundary

    assert (
        _check_write_target_boundary('ln -sf /opt/tools/jq "$LINK_NAME"', str(tmp_path), ())
        is None
    )


def test_run_cmd_content_target_still_follows_final_symlink(tmp_path) -> None:
    from autoskillit.server.lifecycle._guards import _check_write_target_boundary

    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    outside_file = outside / "file"
    outside_file.write_text("outside", encoding="utf-8")
    content_link = allowed / "content-link"
    content_link.symlink_to(outside_file)

    result = _check_write_target_boundary(
        f"echo changed > {content_link}", str(tmp_path), (str(allowed),)
    )

    assert result is not None
    assert str(content_link) in result


def test_check_recipe_read_prohibition_importable():
    from autoskillit.server.lifecycle._guards import _check_recipe_read_prohibition

    doc = _check_recipe_read_prohibition.__doc__ or ""
    assert "recipe" in doc.lower()
    assert "headless" in doc.lower()


def test_check_write_target_boundary_importable():
    from autoskillit.server.lifecycle._guards import _check_write_target_boundary

    doc = _check_write_target_boundary.__doc__ or ""
    assert "write" in doc.lower()
    assert "prefix" in doc.lower()
