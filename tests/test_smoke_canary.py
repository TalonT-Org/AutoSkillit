from autoskillit.smoke_utils import smoke_canary


def test_smoke_canary() -> None:
    assert smoke_canary() is True
