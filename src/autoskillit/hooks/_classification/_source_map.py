"""Source positions carried through the hook tokenizer's pre-lex rewrites."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SourceMappedText:
    """Each character maps to an original interval; synthesized characters share one.

    Copied characters retain their intervals, so every rewrite already maps to the
    original command. Deleted characters have no entries in the output map.
    """

    text: str
    starts: tuple[int, ...]
    ends: tuple[int, ...]

    @classmethod
    def identity(cls, text: str) -> "SourceMappedText":
        return cls(text, tuple(range(len(text))), tuple(range(1, len(text) + 1)))

    def source_span(self, start: int, end: int) -> tuple[int, int]:
        if not 0 <= start < end <= len(self.text):
            raise ValueError(f"empty or out-of-range span [{start}, {end})")
        return self.starts[start], self.ends[end - 1]


class SourceMapBuilder:
    """Compose copied runs and replacements in original-command coordinates."""

    def __init__(self, source: SourceMappedText) -> None:
        self._source = source
        self._parts: list[str] = []
        self._starts: list[int] = []
        self._ends: list[int] = []

    def copy(self, start: int, end: int) -> None:
        if start >= end:
            return
        self._parts.append(self._source.text[start:end])
        self._starts.extend(self._source.starts[start:end])
        self._ends.extend(self._source.ends[start:end])

    def emit(self, text: str, start: int, end: int) -> None:
        source_start, source_end = self._source.source_span(start, end)
        self._parts.append(text)
        self._starts.extend([source_start] * len(text))
        self._ends.extend([source_end] * len(text))

    def build(self) -> SourceMappedText:
        return SourceMappedText("".join(self._parts), tuple(self._starts), tuple(self._ends))
