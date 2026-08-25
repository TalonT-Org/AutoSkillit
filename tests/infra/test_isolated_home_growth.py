"""C9: isolated-home directories stay bounded within a generation.

Uses pytester to run a nested pytest sub-suite exercising the same mktemp +
addfinalizer(shutil.rmtree) pattern _isolated_home uses (tests/conftest.py), then asserts
the standing directory count after the sub-run stays near zero rather than accumulating one
entry per test -- before S3-5, 6,737-13,426 such directories were measured per xdist worker
within a single generation.
"""

from __future__ import annotations

import pytest

pytest_plugins = ["pytester"]

pytestmark = [pytest.mark.layer("infra"), pytest.mark.medium]

_CONFTEST_SOURCE = """
import shutil
import pytest

@pytest.fixture(autouse=True)
def _isolated_home(tmp_path_factory, request):
    isolated_home = tmp_path_factory.mktemp("isolated-home")
    request.addfinalizer(lambda: shutil.rmtree(isolated_home, ignore_errors=True))
    return isolated_home
"""

_TEST_SOURCE = "\n".join(f"def test_case_{i}(_isolated_home): pass" for i in range(20))


def test_isolated_home_directories_stay_bounded_within_a_generation(
    pytester: pytest.Pytester,
) -> None:
    pytester.makeconftest(_CONFTEST_SOURCE)
    pytester.makepyfile(test_many=_TEST_SOURCE)

    result = pytester.runpytest("-p", "no:cacheprovider")
    result.assert_outcomes(passed=20)

    remaining = list(pytester.path.rglob("isolated-home*"))
    assert len(remaining) <= 1, (
        f"expected the finalizer to clean up every isolated-home dir except at most the "
        f"one belonging to a test running concurrently with the last one torn down; "
        f"found {len(remaining)}: {remaining}"
    )
