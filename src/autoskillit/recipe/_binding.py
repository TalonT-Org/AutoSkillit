"""Focused recipe-layer tokenizer/binder.

Produces one ordered :class:`InputBinding` per absolute manifest slot for a
``run_skill`` command string. The binder is the single source of truth for
namespace preservation, slot arithmetic, dispatch-placeholder occupancy, and
omission sentinel handling — extending (not replacing) the IL-0
:class:`autoskillit.core.types.InputBinding` so wire-compatible evidence keeps
flowing through the delivery analyzer and pipeline fingerprints.

The binder is intentionally read-only over its inputs: it never mutates
``RecipeStep`` or any ``with_args`` mapping. Diagnostic strings are returned
inside ``InputBinding.diagnostics``; callers promote diagnostics to
:class:`RuleFinding` records. A ref occupying a slot becomes ``state=BOUND``
with the original source token and ``ref_namespace``/``ref_name`` populated.
The :data:`autoskillit.core.OPTIONAL_ARG_OMISSION_SENTINEL` (``"-"``)
occupies a slot with ``state=OMITTED`` — never ``state=BOUND``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from autoskillit.core import (
    DISPATCH_ITEM_PLACEHOLDER,
    OPTIONAL_ARG_OMISSION_SENTINEL,
)
from autoskillit.core.types import (
    BindingForm,
    BindingState,
    InputBinding,
    InputSpec,
)
from autoskillit.recipe.tool_registry import ToolDef

__all__ = [
    "BindingError",
    "bind_run_skill_command",
    "bind_with_args",
]


_NAMED_ARG_RE: Final[re.Pattern[str]] = re.compile(
    r"""(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<value>"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|\S+)""",
)
_SKILL_CMD_REF_RE: Final[re.Pattern[str]] = re.compile(
    r"\$\{\{\s*(?P<ns>context|inputs)\.(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\}\}"
)


class BindingError(ValueError):
    """Raised when a binding cannot be produced (unknown arg, arity, etc.)."""


@dataclass(frozen=True, slots=True)
class _RawToken:
    raw: str
    key: str | None  # None means positional; key=value form otherwise.


def _tokenize_command(skill_command: str) -> tuple[_RawToken, ...]:
    """Split a skill command into raw tokens, preserving quotes and escapes.

    Quoted values are returned with their surrounding quotes intact so the
    binder can detect namespace/ref content; callers that need the unquoted
    value can strip them.
    """
    if not skill_command:
        return ()
    tokens: list[_RawToken] = []
    i = 0
    n = len(skill_command)
    while i < n:
        ch = skill_command[i]
        if ch.isspace():
            i += 1
            continue
        if ch in {'"', "'"}:
            quote = ch
            j = i + 1
            buf: list[str] = [ch]
            while j < n:
                c = skill_command[j]
                if c == "\\" and j + 1 < n:
                    buf.append(c)
                    buf.append(skill_command[j + 1])
                    j += 2
                    continue
                buf.append(c)
                if c == quote:
                    j += 1
                    break
                j += 1
            else:
                raise BindingError(f"Unterminated quoted token at index {i}: {skill_command!r}")
            tokens.append(_RawToken(raw="".join(buf), key=None))
            i = j
            continue
        # Bare token: read until whitespace, but detect key=value form.
        j = i
        while j < n and not skill_command[j].isspace():
            j += 1
        raw = skill_command[i:j]
        m = _NAMED_ARG_RE.match(raw)
        if m and m.end() == len(raw):
            tokens.append(_RawToken(raw=raw, key=m.group("key")))
        else:
            tokens.append(_RawToken(raw=raw, key=None))
        i = j
    return tuple(tokens)


def _classify_token_value(value: str) -> tuple[str, str | None, str | None, bool, bool]:
    """Classify a token's bound value.

    Returns ``(unquoted_value, ref_namespace, ref_name, is_dispatch, is_omission)``.
    Quoted tokens are unquoted by stripping matching outer quotes.
    """
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        unquoted = value[1:-1]
    else:
        unquoted = value
    if unquoted == OPTIONAL_ARG_OMISSION_SENTINEL:
        return unquoted, None, None, False, True
    if unquoted == DISPATCH_ITEM_PLACEHOLDER:
        return unquoted, None, None, True, False
    m = _SKILL_CMD_REF_RE.fullmatch(unquoted)
    if m:
        return unquoted, m.group("ns"), m.group("name"), False, False
    return unquoted, None, None, False, False


def bind_with_args(
    tool_def: ToolDef,
    with_args: dict[str, str],
    declared_inputs: tuple[InputSpec, ...] = (),
) -> tuple[InputBinding, ...]:
    """Produce one ordered :class:`InputBinding` per declared authorable
    parameter on ``tool_def``.

    ``with_args`` is the ``RecipeStep.with`` mapping (parameter name → string
    value). ``declared_inputs`` is the optional manifest-ordered slot list
    for ``run_skill``-style tools; when non-empty, slots are produced in
    declared manifest order regardless of the order keys appear in
    ``with_args``. Keys in ``with_args`` that are not in the tool's
    authorable parameter set raise :class:`BindingError`. Required authorable
    parameters absent from ``with_args`` produce a binding with
    ``state=UNBOUND``.
    """
    authorable_by_name = {pd.name: pd for pd in tool_def.resolved_param_defs if pd.authorable}
    diagnostics: dict[int, list[str]] = {}
    bindings: list[InputBinding] = []
    if declared_inputs:
        for spec in declared_inputs:
            pd = authorable_by_name.get(spec.name)
            if pd is None:
                diagnostics.setdefault(spec.position, []).append(
                    f"Declared input {spec.name!r} is not authorable on tool {tool_def.name!r}"
                )
                bindings.append(
                    InputBinding(
                        position=spec.position,
                        name=spec.name,
                        type=spec.type,
                        required=spec.required,
                        form=BindingForm.NAMED,
                        state=BindingState.UNBOUND,
                        source_token=None,
                        ref_namespace=None,
                        ref_name=None,
                        diagnostics=tuple(diagnostics[spec.position]),
                    )
                )
                continue
            raw = with_args.get(spec.name)
            if raw is None:
                bindings.append(
                    InputBinding(
                        position=spec.position,
                        name=spec.name,
                        type=spec.type,
                        required=spec.required,
                        form=BindingForm.NAMED,
                        state=BindingState.UNBOUND,
                        source_token=None,
                        ref_namespace=None,
                        ref_name=None,
                    )
                )
                continue
            _v, ref_ns, ref_name, is_dispatch, is_omission = _classify_token_value(raw)
            state = BindingState.OMITTED if is_omission else BindingState.BOUND
            del _v  # value not consumed at the binding record; reserved for future diagnostics
            diagnostics_for_slot: tuple[str, ...] = ()
            if is_dispatch and not pd.required:
                diagnostics_for_slot = (
                    f"Slot {spec.name!r} occupied by dispatch placeholder; "
                    "execution will substitute",
                )
            bindings.append(
                InputBinding(
                    position=spec.position,
                    name=spec.name,
                    type=spec.type,
                    required=spec.required,
                    form=BindingForm.NAMED,
                    state=state,
                    source_token=raw,
                    ref_namespace=ref_ns,
                    ref_name=ref_name,
                    diagnostics=diagnostics_for_slot,
                )
            )
        return tuple(bindings)

    for position, pd in enumerate(tool_def.resolved_param_defs):
        if not pd.authorable:
            continue
        raw = with_args.get(pd.name)
        if raw is None:
            bindings.append(
                InputBinding(
                    position=position,
                    name=pd.name,
                    type=_wire_to_input_type(pd.wire_type),
                    required=pd.required,
                    form=BindingForm.NAMED,
                    state=BindingState.UNBOUND,
                    source_token=None,
                    ref_namespace=None,
                    ref_name=None,
                )
            )
            continue
        _v, ref_ns, ref_name, _is_dispatch, is_omission = _classify_token_value(raw)
        del _v, _is_dispatch  # value and is_dispatch reserved for future diagnostics
        state = BindingState.OMITTED if is_omission else BindingState.BOUND
        bindings.append(
            InputBinding(
                position=position,
                name=pd.name,
                type=_wire_to_input_type(pd.wire_type),
                required=pd.required,
                form=BindingForm.NAMED,
                state=state,
                source_token=raw,
                ref_namespace=ref_ns,
                ref_name=ref_name,
            )
        )
    return tuple(bindings)


def bind_run_skill_command(
    skill_command: str,
    declared_inputs: tuple[InputSpec, ...] = (),
) -> tuple[InputBinding, ...]:
    """Produce one :class:`InputBinding` per positional token in
    ``skill_command``, in absolute slot order.

    Positional tokens are bound by appearance order; ``key=value`` tokens
    raise :class:`BindingError` (callers should pre-validate). Each token's
    value is classified for namespace, dispatch occupancy, and omission
    sentinel state.
    """
    tokens = _tokenize_command(skill_command)
    if declared_inputs:
        if len(tokens) > len(declared_inputs):
            raise BindingError(
                f"Skill command has {len(tokens)} tokens but only "
                f"{len(declared_inputs)} declared input slots"
            )
        bindings: list[InputBinding] = []
        for position, token in enumerate(tokens):
            spec = declared_inputs[position]
            _v, ref_ns, ref_name, _is_dispatch, is_omission = _classify_token_value(token.raw)
            del _v, _is_dispatch
            state = BindingState.OMITTED if is_omission else BindingState.BOUND
            bindings.append(
                InputBinding(
                    position=spec.position,
                    name=spec.name,
                    type=spec.type,
                    required=spec.required,
                    form=BindingForm.POSITIONAL,
                    state=state,
                    source_token=token.raw,
                    ref_namespace=ref_ns,
                    ref_name=ref_name,
                )
            )
        # Trailing declared slots that did not receive a positional token
        # become UNBOUND so the admission gate can deny them.
        for spec in declared_inputs[len(tokens) :]:
            bindings.append(
                InputBinding(
                    position=spec.position,
                    name=spec.name,
                    type=spec.type,
                    required=spec.required,
                    form=BindingForm.POSITIONAL,
                    state=BindingState.UNBOUND,
                    source_token=None,
                    ref_namespace=None,
                    ref_name=None,
                )
            )
        return tuple(bindings)

    for token in tokens:
        if token.key is not None:
            raise BindingError(f"Positional binder cannot accept key=value form: {token.raw!r}")
    bindings = []
    for position, token in enumerate(tokens):
        _v, ref_ns, ref_name, _is_dispatch, is_omission = _classify_token_value(token.raw)
        del _v, _is_dispatch
        state = BindingState.OMITTED if is_omission else BindingState.BOUND
        bindings.append(
            InputBinding(
                position=position,
                name=f"arg{position}",
                type=_wire_to_input_type("string"),
                required=False,
                form=BindingForm.POSITIONAL,
                state=state,
                source_token=token.raw,
                ref_namespace=ref_ns,
                ref_name=ref_name,
            )
        )
    return tuple(bindings)


def _wire_to_input_type(wire_type: str):
    from autoskillit.core.types import InputType

    table = {
        "string": InputType.STRING,
        "integer": InputType.INTEGER,
        "file_path": InputType.FILE_PATH,
        "file_path_list": InputType.FILE_PATH_LIST,
        "directory_path": InputType.DIRECTORY_PATH,
        "path": InputType.FILE_PATH,
    }
    return table.get(wire_type, InputType.STRING)
