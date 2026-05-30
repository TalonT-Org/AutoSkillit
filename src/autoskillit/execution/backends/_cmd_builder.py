"""Typed command builder that enforces positional-before-variadic ordering by construction."""

from __future__ import annotations

from collections.abc import Mapping

from autoskillit.core import CmdOrigin, CmdSpec

__all__ = ["CmdBuilder", "CmdOrderingError"]


class CmdOrderingError(ValueError):
    """Raised when a positional argument is added after a variadic pair."""


class CmdBuilder:
    """Build a CmdSpec with structural enforcement of argument ordering.

    Sections are assembled in a fixed order:
        binary → mode_flags → kv_flags → positional → variadic_pairs

    Calling ``positional()`` after ``variadic_pair()`` raises ``CmdOrderingError``
    immediately, making argument-ordering bugs impossible by construction.
    """

    def __init__(self, binary: str) -> None:
        self._binary = binary
        self._mode_flags: list[str] = []
        self._kv_flags: list[tuple[str, str]] = []
        self._positional: list[str] = []
        self._variadic_pairs: list[tuple[str, str]] = []
        self._has_variadic = False

    def mode_flag(self, flag: str) -> CmdBuilder:
        self._mode_flags.append(flag)
        return self

    def kv_flag(self, flag: str, value: str) -> CmdBuilder:
        self._kv_flags.append((flag, value))
        return self

    def positional(self, value: str) -> CmdBuilder:
        if self._has_variadic:
            raise CmdOrderingError(
                f"Cannot add positional arg {value!r} after variadic pairs have been added. "
                "Positional args must precede all variadic flag pairs."
            )
        self._positional.append(value)
        return self

    def variadic_pair(self, flag: str, value: str) -> CmdBuilder:
        self._variadic_pairs.append((flag, value))
        self._has_variadic = True
        return self

    def build(self, env: Mapping[str, str] | None = None, cwd: str = "") -> CmdSpec:
        cmd: list[str] = [self._binary]
        cmd.extend(self._mode_flags)
        for flag, value in self._kv_flags:
            cmd.extend([flag, value])
        cmd.extend(self._positional)
        for flag, value in self._variadic_pairs:
            cmd.extend([flag, value])
        origin = CmdOrigin(
            binary=self._binary,
            mode_flags=tuple(self._mode_flags),
            kv_flags=tuple(self._kv_flags),
            positional=tuple(self._positional),
            variadic_pairs=tuple(self._variadic_pairs),
        )
        return CmdSpec(
            cmd=tuple(cmd),
            env=env if env is not None else {},
            cwd=cwd,
            origin=origin,
        )
