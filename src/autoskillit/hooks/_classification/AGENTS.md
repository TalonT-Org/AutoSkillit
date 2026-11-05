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
- `_source_map.py` — `SourceMappedText` / `SourceMapBuilder` carry per-character
  original-command intervals through pre-lex rewrites; leaf module.
- `_shell_structure.py` — quote-aware substitution masking, newline normalization,
  output-redirect marking, and grouping rewrites used by `_tokenizer.py` before
  lexing.
- `_interpreters.py` — interpreter and nested-shell payload classification
  (`all_evaluated_segments_with_provenance`, `live_command_text`,
  `EvaluatedSegment`, `StdinLiteral`, `strip_heredoc_bodies`).
- `_flags.py` — flag-parsing tables, shell-substitution regexes, and
  protected-read classification.
- `_option_grammar.py` — Git parse-options engine (`parse_options`, `last_option`,
  `OptionArity`, `OptionDef`, `ParsedArgv`): bundled, stuck, `=`-joined,
  abbreviated and negated options over per-subcommand tables; leaf module.
- `_flag_arity_classification.py` — leaf sibling holding the `_FlagArity` enum
  referenced by `_flags.py` and `_interpreters.py`. Lifted out so neither
  classification module owns the cross-import that would otherwise create a
  bidirectional `_flags` ↔ `_interpreters` coupling.
- `_python_program_analysis.py` — Python interpreter and subprocess invocation
  analysis (`_InterpreterCommandSpec`, `_python_c_program`,
  `_python_program_command_specs`, `_python_program_evaluated_specs`).
- `_substitution_scanning.py` — shell-substitution / process-substitution
  scanning and the legacy shell-payload segment traversal.
- `_github_mutation_cli_analysis.py` — `gh` / `curl` mutation grammar.
- `_github_mutation_request_analysis.py` — request-body mutation vocabulary
  consumed by the cli analyzer.
- `_output_redirect.py` — output-redirect partitioning (`OutputRedirectPartition`
  dataclass and the `_partition_output_redirect_indices` /
  `_partition_output_redirects` / `_select_executable_argv_tokens` /
  `extract_redirect_targets_with_status` / `resolve_write_target` projections).
  Sole producer of these symbols; the facade re-exports them.
`scan_write_targets` and `WriteTargetScan` are defined in
`_runtime/_write_target_scan.py` and re-exported by
`_runtime/_command_classification.py`. The scanner consumes
`_runtime/_shell_segment_walk.py`, the single authority for the cwd in effect for
each evaluated segment: it tracks mutable cwd and scope state and, beyond the
shell's logical directory, whether a `cd` certainly ran (`cwd_certain`).

`_CommandSegment` carries the logical operator that joined it to the previous
segment (`connector`: `&&`, `||`, `|`, `&`, `;` or empty) and whether it is the first
statement after a dropped bare keyword line (`after_keyword`). The first operator
after a command wins, so a newline after `&&`, `||`, `|` or `&` keeps that operator.
`_shell_structure` emits the newline boundary before its `;`, so a trailing `#`
comment cannot swallow the boundary.

## Bootstrap Conventions

Cross-folder imports use the dual dotted/bare-name pattern established in
`_runtime/AGENTS.md`: `if __package__ == "autoskillit.hooks._classification":
from .._runtime import _command_classification as _classification` so both
the facade's relative imports and any bare-name bootstrap (hook scripts that
push `hooks/_classification/` onto `sys.path`) resolve the same symbols.

Within-folder imports of leaf siblings (`_tokenizer.py`, `_flag_arity_classification.py`,
`_source_map.py`, `_option_grammar.py`)
stay at module top-level — they have no back-references into the facade or
into other `_classification/` modules, so the relative-import dance isn't
required.
