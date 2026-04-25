from __future__ import annotations

from pathlib import Path

import pytest


def deselect_arch_items(
    items: list[pytest.Item],
    changed_abs: set[Path],
    arch_dir: Path,
) -> tuple[list[pytest.Item], list[pytest.Item]]:
    """Return (selected, deselected) splitting items by source_file membership.

    Only items inside arch_dir whose callspec has a ``source_file`` param are
    candidates for deselection. All others (one-shot tests, non-arch items,
    and parametrized tests using different param names) pass through unchanged.
    """
    selected: list[pytest.Item] = []
    deselected: list[pytest.Item] = []
    for item in items:
        if not Path(str(item.fspath)).is_relative_to(arch_dir):
            selected.append(item)
            continue
        if not hasattr(item, "callspec") or "source_file" not in item.callspec.params:
            selected.append(item)
            continue
        source_file: Path = item.callspec.params["source_file"]
        if source_file.resolve() in changed_abs:
            selected.append(item)
        else:
            deselected.append(item)
    return selected, deselected
