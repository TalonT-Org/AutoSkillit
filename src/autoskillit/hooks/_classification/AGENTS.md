# _classification/

Classification primitives that feed the runtime command-classification facade
(`hooks/_runtime/_command_classification.py`). Each module owns one narrow
slice of the command-parsing pipeline; the facade re-exports them through its
block B bootstrap so hook scripts can keep importing via
`from autoskillit.hooks._runtime._command_classification import …`.

## Modules

- `_tokenizer.py` — `shlex`-backed tokenization plus `ArgvToken` / `_CommandSegment`
  data classes. Sole producer of those token types; consumed by every other
  module in this folder and by the facade's TYPE_CHECKING block.
- `_shell_structure.py` — quote-aware substitution masking and grouping syntax
  used only by `_tokenizer.py` before lexing.
- `_interpreters.py` — interpreter and nested-shell payload classification
  (`all_evaluated_segments_with_provenance`, `live_command_text`,
  `EvaluatedSegment`, `StdinLiteral`, `strip_heredoc_bodies`).
- `_flags.py` — flag-parsing tables, shell-substitution regexes, and
  protected-read classification.
- `_flag_arity_classification.py` — leaf sibling holding the `_FlagArity` enum
  referenced by `_flags.py` and `_interpreters.py`. Lifted out so neither
  classification module owns the cross-import that would otherwise create a
  bidirectional `_flags` ↔ `_interpreters` coupling.
- `_python_program_analysis.py` — Python interpreter invocation surface
  (`_InterpreterCommandSpec`, `_python_program_command_specs`).
- `_substitution_scanning.py` — shell-substitution / process-substitution
  scanning helpers.
- `_github_mutation_cli_analysis.py` — `gh` / `curl` mutation grammar.
- `_github_mutation_request_analysis.py` — request-body mutation vocabulary
  consumed by the cli analyzer.
- `_output_redirect.py` — output-redirect partitioning (`OutputRedirectPartition`
  dataclass and the `_partition_output_redirect_indices` /
  `_partition_output_redirects` / `_select_executable_argv_tokens` /
  `extract_redirect_targets_with_status` / `resolve_write_target` projections).
  Sole producer of these symbols; the facade re-exports them.

`scan_write_targets` and `WriteTargetScan` live in
`_runtime/_command_classification.py`.

## Bootstrap Conventions

Cross-folder imports use the dual dotted/bare-name pattern established in
`_runtime/AGENTS.md`: `if __package__ == "autoskillit.hooks._classification":
from .._runtime import _command_classification as _classification` so both
the facade's relative imports and any bare-name bootstrap (hook scripts that
push `hooks/_classification/` onto `sys.path`) resolve the same symbols.

Within-folder imports of leaf siblings (`_tokenizer.py`, `_flag_arity_classification.py`)
stay at module top-level — they have no back-references into the facade or
into other `_classification/` modules, so the relative-import dance isn't
required.
