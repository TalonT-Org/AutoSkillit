import pytest

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def test_ci_watch_tools_importable():
    from autoskillit.server.tools_ci_watch import wait_for_ci

    assert callable(wait_for_ci)


def test_ci_merge_queue_tools_importable():
    from autoskillit.server.tools_ci_merge_queue import (
        toggle_auto_merge,
    )

    assert callable(toggle_auto_merge)


def test_ci_status_tools_in_reduced_file():
    from autoskillit.server.tools_ci import set_commit_status

    assert callable(set_commit_status)
