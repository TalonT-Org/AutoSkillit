"""Shared numbered selection menu primitive for CLI commands."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TypeVar

from autoskillit.cli._timed_input import timed_prompt

T = TypeVar("T")

SLOT_ZERO_SELECTED: str = "__slot_zero__"


def resolve_menu_input(
    raw: str,
    items: Sequence[T],
    *,
    name_key: Callable[[T], str] = lambda x: x.name,  # type: ignore[attr-defined]
    slot_zero: bool = False,
) -> T | str | None:
    if not raw:
        return None
    if raw.isdigit():
        n = int(raw)
        if n == 0:
            return SLOT_ZERO_SELECTED if slot_zero else None
        if 1 <= n <= len(items):
            return items[n - 1]
        return None
    return next((item for item in items if name_key(item) == raw), None)


def render_numbered_menu(
    items: Sequence[T],
    *,
    header: str = "Available items:",
    slot_zero_label: str | None = None,
    group_classifier: Callable[[T], int] | None = None,
    group_labels: dict[int, str] | None = None,
    display_fn: Callable[[T], str] | None = None,
    name_key: Callable[[T], str] = lambda x: x.name,  # type: ignore[attr-defined]
) -> None:
    print(header)
    if slot_zero_label is not None:
        print(f"  0. {slot_zero_label}")
    current_rank: int = -1
    for i, item in enumerate(items, 1):
        if group_classifier is not None:
            rank = group_classifier(item)
            if rank != current_rank:
                current_rank = rank
                label = (group_labels or {}).get(rank, str(rank))
                print(f"\n  {label}")
        label_str = display_fn(item) if display_fn is not None else name_key(item)
        print(f"  {i}. {label_str}")


def run_selection_menu(
    items: Sequence[T],
    *,
    header: str = "Available items:",
    slot_zero_label: str | None = None,
    group_classifier: Callable[[T], int] | None = None,
    group_labels: dict[int, str] | None = None,
    display_fn: Callable[[T], str] | None = None,
    name_key: Callable[[T], str] = lambda x: x.name,  # type: ignore[attr-defined]
    timeout: int = 120,
    label: str = "selection",
) -> T | str | None:
    render_numbered_menu(
        items,
        header=header,
        slot_zero_label=slot_zero_label,
        group_classifier=group_classifier,
        group_labels=group_labels,
        display_fn=display_fn,
        name_key=name_key,
    )
    if slot_zero_label is not None:
        prompt_text = f"Select [0-{len(items)}]:"
    else:
        prompt_text = f"Select [1-{len(items)}]:"
    raw = timed_prompt(prompt_text, default="", timeout=timeout, label=label)
    return resolve_menu_input(raw, items, name_key=name_key, slot_zero=slot_zero_label is not None)
