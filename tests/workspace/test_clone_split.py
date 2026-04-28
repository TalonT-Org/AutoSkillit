import pytest

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.small]


def test_detect_helpers_importable_from_submodule():
    from autoskillit.workspace._clone_detect import (
        classify_remote_url,
    )

    assert callable(classify_remote_url)


def test_remote_helpers_importable_from_submodule():
    from autoskillit.workspace._clone_remote import (
        CloneSourceResolution,
    )

    assert CloneSourceResolution is not None


def test_workspace_init_surface_unchanged():
    from autoskillit.workspace import (
        clone_repo,
    )

    assert callable(clone_repo)
