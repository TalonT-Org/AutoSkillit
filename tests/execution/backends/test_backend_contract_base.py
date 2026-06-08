"""BackendContractBase — shared ABC mixin for backend conformance test classes."""

from __future__ import annotations

import abc
import dataclasses
import typing

import pytest

from autoskillit.core import BackendCapabilities, CodingAgentBackend

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]

_BOOL_CAPABILITY_NAMES: frozenset[str] = frozenset(
    f.name
    for f in dataclasses.fields(BackendCapabilities)
    if typing.get_type_hints(BackendCapabilities)[f.name] is bool
)


class BackendContractBase(abc.ABC):
    """ABC mixin inherited by sibling conformance test classes (P4-A2 through P4-A4).

    Subclasses implement ``make_backend()`` to return the backend under test.
    ``_require_capability()`` skips the calling test when the backend lacks a
    named bool capability.
    """

    @abc.abstractmethod
    def make_backend(self) -> CodingAgentBackend: ...

    def _require_capability(self, cap: str) -> None:
        all_names = {f.name for f in dataclasses.fields(BackendCapabilities)}
        if cap not in all_names:
            raise AttributeError(
                f"{cap!r} is not a field on BackendCapabilities; valid fields: {sorted(all_names)}"
            )
        if cap not in _BOOL_CAPABILITY_NAMES:
            raise AttributeError(
                f"{cap!r} is not a bool capability field; "
                f"bool fields: {sorted(_BOOL_CAPABILITY_NAMES)}"
            )
        if not getattr(self.make_backend().capabilities, cap):
            pytest.skip(f"{cap!r} not supported by this backend")
