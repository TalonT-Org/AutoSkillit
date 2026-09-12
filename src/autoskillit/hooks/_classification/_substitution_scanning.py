"""Quote/escape-aware raw-text scanners for shell substitution syntax.

Split out of `_interpreters.py` (rectify #4941 Part A) to keep that module's
stdin-consumer/evaluated-payload machinery under the REQ-CNST-010 line cap.
Self-contained: these are pure character-by-character state machines over a
command string, with no dependency on the rest of `_classification/`.
"""

from __future__ import annotations


def _find_substitution_end(command: str, start: int) -> int:
    """Return the closing ``)`` index for a substitution body starting at *start*.

    Quotes open a fresh quoting context inside a substitution, so a literal
    ``)`` within a quoted span must not terminate the scan. Returns
    ``len(command)`` when the substitution is unclosed.
    """
    depth = 1
    n = len(command)
    k = start
    while k < n:
        ch = command[k]
        if ch == "\\" and k + 1 < n:
            k += 2
            continue
        if ch == "'":
            k += 1
            while k < n and command[k] != "'":
                k += 1
            k += 1
            continue
        if ch == '"':
            k += 1
            while k < n and command[k] != '"':
                if command[k] == "\\" and k + 1 < n:
                    k += 2
                    continue
                k += 1
            k += 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return k
        k += 1
    return n


def _extract_process_substitution_occurrences(
    command: str,
) -> tuple[tuple[str, int, int, str, bool], ...]:
    """Return active ``<(...)``/``>(...)`` occurrences in source order.

    Each private tuple is ``(kind, start, end, body, balanced)``. ``kind`` is
    the literal opening syntax (``"<("`` or ``">("``); ``start`` and ``end``
    are raw-source offsets, with ``end`` exclusive; and ``body`` excludes the
    delimiters. An unclosed occurrence has ``balanced=False`` and ends at the
    command's end, allowing callers to retain occurrence identity while making
    their own fail-open or fail-closed decision.

    Process substitution is inactive inside quoted text or when its opening
    character is escaped. The existing balanced-parenthesis scanner supplies
    the quote- and escape-aware body boundary for active occurrences.
    """
    occurrences: list[tuple[str, int, int, str, bool]] = []
    i = 0
    n = len(command)
    while i < n:
        char = command[i]
        if char == "\\" and i + 1 < n:
            i += 2
            continue
        if char in {"'", '"'}:
            quote = char
            i += 1
            while i < n and command[i] != quote:
                if quote == '"' and command[i] == "\\" and i + 1 < n:
                    i += 2
                    continue
                i += 1
            i += 1
            continue
        if char not in {"<", ">"} or i + 1 >= n or command[i + 1] != "(":
            i += 1
            continue

        close = _find_substitution_end(command, i + 2)
        balanced = close < n
        end = close + 1 if balanced else n
        occurrences.append((f"{char}(", i, end, command[i + 2 : close], balanced))
        i = end
    return tuple(occurrences)


def _iter_substitution_occurrences(command: str) -> list[tuple[int, str]]:
    """Quote/escape-aware state machine returning (start_offset, body) pairs.

    ``start_offset`` is the index of the payload body's first character
    (just past the opening `` ` `` or ``$(``) in *command* -- the identity
    `evaluated_payloads` and `_occurrence_owner_index` use to map an
    occurrence back to its owning segment, and to blank it exactly once in
    `live_command_text`, independent of two occurrences sharing identical
    text.
    """
    occurrences: list[tuple[int, str]] = []
    i = 0
    n = len(command)
    while i < n:
        c = command[i]
        if c == "\\" and i + 1 < n:
            i += 2
            continue
        if c == "'":
            # Skip single-quoted span
            j = i + 1
            while j < n and command[j] != "'":
                j += 1
            i = j + 1
            continue
        if c == '"':
            # Walk inside double quotes; substitutions are still active here.
            j = i + 1
            while j < n and command[j] != '"':
                if command[j] == "\\" and j + 1 < n:
                    j += 2
                    continue
                if command[j] == "`":
                    inner_end = j + 1
                    while inner_end < n and command[inner_end] != "`":
                        inner_end += 1
                    occurrences.append((j + 1, command[j + 1 : inner_end]))
                    j = inner_end + 1
                    continue
                if command[j] == "$" and j + 1 < n and command[j + 1] == "(":
                    k = _find_substitution_end(command, j + 2)
                    occurrences.append((j + 2, command[j + 2 : k]))
                    j = k + 1
                    continue
                j += 1
            i = j + 1
            continue
        if c == "`":
            j = i + 1
            while j < n and command[j] != "`":
                if command[j] == "\\" and j + 1 < n:
                    j += 2
                    continue
                j += 1
            occurrences.append((i + 1, command[i + 1 : j]))
            i = j + 1
            continue
        if c == "$" and i + 1 < n and command[i + 1] == "(":
            j = _find_substitution_end(command, i + 2)
            occurrences.append((i + 2, command[i + 2 : j]))
            i = j + 1
            continue
        i += 1
    return occurrences
