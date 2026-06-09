from __future__ import annotations

import abc
import dataclasses

import pytest

from autoskillit.core import BackendCapabilities, CodingAgentBackend

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


class BackendContractBase(abc.ABC):
    @abc.abstractmethod
    def make_backend(self) -> CodingAgentBackend: ...

    def _require_capability(self, cap: str) -> None:
        valid = {f.name for f in dataclasses.fields(BackendCapabilities)}
        if cap not in valid:
            raise AttributeError(
                f"{cap!r} is not a field of BackendCapabilities; valid fields: {sorted(valid)}"
            )
        if not getattr(self.make_backend().capabilities, cap):
            pytest.skip(f"{cap!r} not supported by this backend")
