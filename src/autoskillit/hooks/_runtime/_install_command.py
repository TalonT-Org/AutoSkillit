"""GNU install command operand selection.

Extracts the destination directory from every spelling of ``install``'s
``-t`` / ``--target-directory`` option so write-verb classification can
build a precise allow-list guard without false-negatives on the GNU
combined forms.

Stdlib-only; runs as a bare sibling module under ``hooks/_runtime/``.
"""

from __future__ import annotations


def _has_install_target_flag(tokens: list[str]) -> bool:
    """Return whether any token in *tokens* selects the GNU install target.

    Recognises every spelling that putopt-style parsers accept for the
    `-t` / `--target-directory` option: the spaced short and long forms,
    the GNU combined `--target-directory=DIR` long form, and the GNU
    combined `-tDIR` / `-t=DIR` short form (token starts with `-t` and
    is not the `--target…` long option).
    """
    for token in tokens:
        if token in {"-t", "--target-directory"} or token.startswith("--target-directory="):
            return True
        if len(token) > 2 and token.startswith("-t") and not token.startswith("--"):
            return True
    return False


def _install_target_value(tokens: list[str]) -> str | None:
    """Return the inline value of an install -t / --target-directory flag.

    For GNU combined forms (``-tDIR``, ``-t=DIR``, ``--target-directory=DIR``)
    the value is glued into the flag token; the caller must use the
    returned value as the destination. For the spaced forms the value
    follows as a separate operand — this function returns None so the
    caller reads it from the remaining operands.
    """
    for token in tokens:
        if token.startswith("--target-directory="):
            return token[len("--target-directory=") :]
        if len(token) > 2 and token.startswith("-t") and not token.startswith("--"):
            return token[2:]
    return None


def select_install_operands(tokens: list[str], operands: list[str]) -> list[str] | None:
    """Return the destination operand(s) for a GNU install command.

    Handles every spelling of ``-t`` / ``--target-directory`` — the
    spaced forms (``install -t DIR SRC``, ``install --target-directory
    DIR SRC``) and the GNU combined forms (``install -tDIR SRC``,
    ``install --target-directory=DIR SRC``). Returns ``None`` when no
    target can be resolved; the caller treats that as a fail-closed
    unresolved-target signal.
    """
    inline_value = _install_target_value(tokens)
    if inline_value is not None:
        return [inline_value]
    if _has_install_target_flag(tokens):
        return operands[:1]
    if len(operands) < 2:
        return None
    return operands[-1:]
