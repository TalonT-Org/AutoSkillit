"""Deterministic, semantics-preserving recipe display compaction.

Stdlib-only sibling module for ``_fmt_recipe.py`` (see ``hooks/formatters/AGENTS.md``
for the standalone-hook import architecture). Reduces the byte size of agent-visible
recipe YAML without altering any parsed value except the presentation-only fields this
module explicitly drops (top-level ``description``/``summary``, direct step
``description``): compacts structural indentation to one column per nesting level,
drops all leading whitespace from quoted-scalar continuation lines (semantically inert
— YAML folding discards it before joining), shifts literal/folded block scalar bodies
by their container's indentation delta while preserving content-relative indentation
and bytes, and removes duplicated per-stop-step message payloads from the displayed
orchestration rules text. Every transformation is fixed (not conditionally retried at
increasing aggressiveness) — see issue #4253 Part A.

Line classification never inspects continuation-line *content* to decide whether a new
mapping key/list item has started; it only compares indentation against the most recent
structural line's original indentation, combined with whether that structural line had
an inline value (a YAML mapping key cannot have both an inline scalar value and nested
children). This avoids false-positive re-interpretation of prose that happens to contain
a ``:`` or quote character.
"""

from __future__ import annotations

_BLOCK_INDICATORS: frozenset[str] = frozenset({"|", "|-", "|+", ">", ">-", ">+"})


def _leading_spaces(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _strip_newline(line: str) -> str:
    return line[:-1] if line.endswith("\n") else line


def _quote_closes_on_line(text: str, quote_char: str) -> bool:
    """Whether `text` contains the unescaped closing quote for `quote_char`.

    Single-quoted YAML scalars escape a literal quote as `''`. Double-quoted
    scalars use backslash escaping (`\\"`, `\\\\`).
    """
    i = 0
    n = len(text)
    if quote_char == "'":
        while i < n:
            if text[i] == "'":
                if i + 1 < n and text[i + 1] == "'":
                    i += 2
                    continue
                return True
            i += 1
        return False
    escape = False
    while i < n:
        ch = text[i]
        if escape:
            escape = False
        elif ch == "\\":
            escape = True
        elif ch == '"':
            return True
        i += 1
    return False


def _scalar_intro_value(content: str) -> str:
    """Return the value portion of a structural line (after `key:` or `- `).

    Returns "" when the line is a bare container node (mapping/sequence key
    with no inline value) — the caller then knows any more-indented lines
    that follow are genuinely nested structure, not a scalar continuation.
    """
    text = content.lstrip(" ")
    while text.startswith("- "):
        text = text[2:]
    if text in ("-", ""):
        return ""
    if text[0] in ("'", '"'):
        return text
    sep = text.find(": ")
    if sep != -1:
        return text[sep + 2 :]
    if text.endswith(":"):
        return ""
    return text


def _mapping_key(content: str) -> str | None:
    """Return the bare mapping key name for a `key: value` or `key:` line."""
    text = content.lstrip(" ")
    if not text or text[0] in ("'", '"', "-", "#"):
        return None
    sep = text.find(": ")
    if sep != -1:
        return text[:sep]
    if text.endswith(":"):
        return text[:-1]
    return None


def _value_span_end(lines: list[str], start: int, key_indent: int, value: str) -> int:
    """Exclusive end index of every line belonging to `lines[start]`'s value.

    Handles block scalars, quoted scalars (single- or double-quoted, with
    escape-aware closing-quote detection), and plain scalars that fold across
    lines — all three continue while the following line is blank or indented
    more than `key_indent`, except quoted scalars which continue until the
    unescaped closing quote is found (indentation-independent).
    """
    n = len(lines)
    if not value:
        return start + 1
    head = value.strip()
    if head in _BLOCK_INDICATORS:
        i = start + 1
        while i < n and (lines[i].strip() == "" or _leading_spaces(lines[i]) > key_indent):
            i += 1
        return i
    if value[0] in ("'", '"'):
        qc = value[0]
        if _quote_closes_on_line(value[1:], qc):
            return start + 1
        i = start + 1
        while i < n:
            if _quote_closes_on_line(_strip_newline(lines[i]), qc):
                return i + 1
            i += 1
        return n
    i = start + 1
    while i < n and (lines[i].strip() == "" or _leading_spaces(lines[i]) > key_indent):
        i += 1
    return i


def _strip_top_level_fields(yaml_text: str, keys: frozenset[str]) -> str:
    """Remove top-level (indent-0) mapping entries whose key is in `keys`."""
    lines = yaml_text.splitlines(keepends=True)
    n = len(lines)
    out: list[str] = []
    i = 0
    while i < n:
        line = lines[i]
        if line.strip() == "":
            out.append(line)
            i += 1
            continue
        if _leading_spaces(line) == 0:
            content = _strip_newline(line)
            if _mapping_key(content) in keys:
                value = _scalar_intro_value(content)
                i = _value_span_end(lines, i, 0, value)
                continue
        out.append(line)
        i += 1
    return "".join(out)


def _strip_step_descriptions(yaml_text: str) -> str:
    """Remove direct `description:` fields of steps under the top-level `steps:` map.

    Scoped structurally: only indent-4 `description` keys while inside the
    top-level `steps:` mapping are removed. Ingredient descriptions (also at
    indent 4, but under `ingredients:`) and any nested `description` inside
    step command/tool content are untouched.
    """
    lines = yaml_text.splitlines(keepends=True)
    n = len(lines)
    out: list[str] = []
    i = 0
    in_steps = False
    while i < n:
        line = lines[i]
        if line.strip() == "":
            out.append(line)
            i += 1
            continue
        indent = _leading_spaces(line)
        content = _strip_newline(line)
        if indent == 0:
            in_steps = _mapping_key(content) == "steps"
        elif in_steps and indent == 4 and _mapping_key(content) == "description":
            value = _scalar_intro_value(content)
            i = _value_span_end(lines, i, indent, value)
            continue
        out.append(line)
        i += 1
    return "".join(out)


def _compact_indentation(yaml_text: str) -> str:
    """Compact structural indentation to one column per nesting level.

    Literal/folded block scalar bodies keep their content-relative
    indentation and bytes — only shifted left by the same amount their
    introducing key line shrank. Quoted (single/double) scalar continuation
    lines lose their leading whitespace entirely: YAML folding discards it
    unconditionally before joining the lines, so no amount of it is part of
    the parsed value.

    New indentation is assigned by a level stack (one entry per currently
    open nesting level, `(original_raw_indent, new_indent)`), not by halving
    each line's raw indent independently: a `- key: value` list item's fixed
    two-character `- ` marker does not shrink under compaction, so its
    inline content — and everything nested under it — sits one column
    further right than plain `raw_indent // 2` arithmetic would place it.
    Assigning `parent.new_indent + 1` to each newly seen level keeps every
    descendant consistent with its actual parent regardless of how many
    list-item markers separate them.
    """
    lines = yaml_text.splitlines(keepends=True)
    n = len(lines)
    out: list[str] = []
    # stack[0] is a sentinel root below any real indent (raw indent 0 always
    # opens a fresh level 0 against it).
    stack: list[tuple[int, int]] = [(-1, -1)]
    i = 0
    while i < n:
        line = lines[i]
        if line.strip() == "":
            out.append(line)
            i += 1
            continue
        indent = _leading_spaces(line)
        content = _strip_newline(line)

        while len(stack) > 1 and indent < stack[-1][0]:
            stack.pop()
        if indent == stack[-1][0]:
            new_indent = stack[-1][1]
        else:
            new_indent = stack[-1][1] + 1
            stack.append((indent, new_indent))

        new_line = " " * new_indent + content[indent:]
        out.append(new_line + ("\n" if line.endswith("\n") else ""))

        # Each '- ' marker opens one additional virtual level for its inline
        # content. The marker itself is 2 literal, non-halvable columns, so
        # a sibling field of the same list item (e.g. `route:` following
        # `- when: ...`) must align 2 new-indent columns past the dash, not
        # 1 — descendants nested deeper than that inherit the usual +1-per-
        # level step from this pushed level.
        key_indent = indent
        key_new = new_indent
        text = content[indent:]
        while text.startswith("- "):
            key_indent += 2
            key_new += 2
            text = text[2:]
        if key_indent != indent:
            stack.append((key_indent, key_new))

        value = _scalar_intro_value(content)
        end = _value_span_end(lines, i, key_indent, value)
        is_block = value.strip() in _BLOCK_INDICATORS
        is_quoted = bool(value) and value[0] in ("'", '"') and end > i + 1
        if is_block:
            # Literal/folded block scalar body: shift every line left by the
            # same amount the introducing key line shrank, preserving each
            # line's indentation relative to the others (and all non-
            # whitespace bytes) — only the block's overall container
            # indentation changes, per the fixed compaction projection.
            delta = key_indent - key_new
            for body_line in lines[i + 1 : end]:
                if delta <= 0 or body_line.strip() == "":
                    out.append(body_line)
                    continue
                cut = min(delta, _leading_spaces(body_line))
                out.append(body_line[cut:])
        elif is_quoted:
            # Quoted (single/double) scalar continuation lines: YAML folding
            # discards ALL leading whitespace on every continuation line
            # before joining them into the scalar's value, so no amount of
            # leading whitespace changes the parsed string — dropping it
            # entirely is safe and maximizes compaction.
            for body_line in lines[i + 1 : end]:
                if body_line.strip() == "":
                    out.append(body_line)
                    continue
                out.append(body_line[_leading_spaces(body_line) :])
        else:
            out.extend(lines[i + 1 : end])
        i = end
    return "".join(out)


_TOP_LEVEL_STRIP_FIELDS: frozenset[str] = frozenset(
    {
        "description",
        "summary",
        "name",
        "recipe_version",
        "requires_packs",
    }
)


def _strip_structural_blanks(text: str) -> str:
    """Strip trailing whitespace and remove blank lines between step entries.

    YAML trailing spaces are never semantically significant. Blank lines
    between step mapping entries (where the next non-blank line is at indent
    ≤ 1 after compaction, indicating a step name or top-level key) are
    cosmetic separators; those inside literal/folded block scalars are NOT
    removed because block-scalar content lines are deeper.
    """
    lines = text.split("\n")
    n = len(lines)
    out: list[str] = []
    i = 0
    while i < n:
        line = lines[i]
        if line.strip() == "":
            j = i + 1
            while j < n and lines[j].strip() == "":
                j += 1
            if j < n and _leading_spaces(lines[j]) <= 1:
                i = j
                continue
            out.append(line)
            i = i + 1
        else:
            out.append(line.rstrip())
            i += 1
    return "\n".join(out)


def compact_recipe_display(yaml_text: str) -> str:
    """Apply the fixed, deterministic display-compaction projection.

    Removes presentation-only top-level metadata and direct step
    ``description`` fields (already rendered elsewhere in the formatted
    response — ``name`` and ``recipe_version`` in the header, ``summary``
    via the STEP FLOW block, ``requires_packs`` as an internal gating
    field, descriptions nowhere — they are pure narration), then halves
    structural YAML indentation and removes cosmetic blank lines between
    step entries. All tools, actions, routes, commands, captures, messages,
    notes, and guard fields are preserved as parsed values — see
    tests/infra/test_pretty_output_recipe.py::test_compact_recipe_display_preserves_execution_semantics.
    """
    text = _strip_top_level_fields(yaml_text, _TOP_LEVEL_STRIP_FIELDS)
    text = _strip_step_descriptions(text)
    text = _compact_indentation(text)
    return _strip_structural_blanks(text)


_STOP_STEP_MESSAGE_PREFIX = "  Stop step '"
_PER_STOP_PREFIX = "- For stop step '"


def compact_orchestration_rules(text: str) -> str:
    """Drop repeated per-stop-step `message:` payload lines and consolidate
    per-stop L3 sentinel directives into a compact table.

    The exact message text remains in each stop step's own YAML ``message``
    field (rendered in the RECIPE section). Only the duplicated copy emitted
    by ``_build_stop_step_semantics()`` is dropped here — that function and
    its dedicated ``stop_step_semantics`` Channel B field are untouched.

    Per-stop L3 sentinel directives (``- For stop step 'X': emit ...``) are
    consolidated into ``success=true: A, B`` / ``success=false: C, D`` lines.
    """
    lines = text.split("\n")
    kept: list[str] = []
    success_true: list[str] = []
    success_false: list[str] = []
    for line in lines:
        if line.startswith(_STOP_STEP_MESSAGE_PREFIX) and "' message: " in line:
            continue
        if line.startswith(_PER_STOP_PREFIX):
            name_end = line.index("'", len(_PER_STOP_PREFIX))
            name = line[len(_PER_STOP_PREFIX) : name_end]
            if "success=true" in line:
                success_true.append(name)
            else:
                success_false.append(name)
            continue
        kept.append(line)
    if success_true or success_false:
        if success_true:
            kept.append(f"  success=true: {', '.join(success_true)}")
        if success_false:
            kept.append(f"  success=false: {', '.join(success_false)}")
    return "\n".join(kept)
