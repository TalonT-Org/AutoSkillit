"""Command-text and hook-environment channel flags of hooks/_runtime/_git_target.py.

Members: the layout-override rows, the include-in-a-filename rows that are no layout
channel, the alias-channel rows, the hook-environment layout-variable rows and the
ambient-HOME row, all through ``command_channel_flags``.
"""

from __future__ import annotations

import pytest

from autoskillit.hooks._runtime._command_classification import live_command_text
from autoskillit.hooks._runtime._git_target import ChannelFlags, command_channel_flags

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.small]

_LAYOUT_COMMANDS = (
    "export GIT_DIR=/r/.git; git x",
    "GIT_WORK_TREE=/r git x",
    "git --git-dir=/r x",
    "git --bare x",
    "git -c core.worktree=/r x",
    "git -c include.path=/x y",
    "git -c includeIf.gitdir:/a/.path=/x y",
)
_ALIAS_COMMANDS = (
    "git -c alias.x=y x",
    "export GIT_CONFIG_COUNT=1; git x",
    "HOME=/h git x",
    "export XDG_CONFIG_HOME=/x; git x",
    "git config alias.ur update-ref && git ur a b",
)


def _flags(command: str) -> ChannelFlags:
    return command_channel_flags(live_command_text(command))


@pytest.mark.parametrize("command", _LAYOUT_COMMANDS)
def test_layout_channels_in_command_text(command: str) -> None:
    assert _flags(command) == ChannelFlags(layout_override=True, alias_channel=False)


@pytest.mark.parametrize(
    "command",
    [
        'git commit -m "add include.txt" && git checkout feature',
        "cat include.h && git checkout x",
    ],
)
def test_include_in_a_filename_is_not_a_layout_channel(command: str) -> None:
    assert _flags(command) == ChannelFlags(layout_override=False, alias_channel=False)


@pytest.mark.parametrize("command", _ALIAS_COMMANDS)
def test_alias_channels_in_command_text(command: str) -> None:
    assert _flags(command) == ChannelFlags(layout_override=False, alias_channel=True)


@pytest.mark.parametrize("name", ["GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR"])
def test_layout_variable_in_the_hook_environment_sets_layout_override(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    monkeypatch.setenv(name, "/r/.git")
    assert _flags("git x") == ChannelFlags(layout_override=True, alias_channel=False)


def test_ambient_home_alone_sets_neither_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", "/h")
    assert _flags("git x") == ChannelFlags(layout_override=False, alias_channel=False)
