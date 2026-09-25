"""Tests that _guards functions document their orchestration level requirements."""

import pytest

from autoskillit.hooks import UNRESOLVED_WRITE_TARGET_REMEDIATION

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
            "",
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
        if expected_message:
            assert expected_message in result
    else:
        assert result is None


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
