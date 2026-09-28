# Skill write boundaries

Every skill declares its write scope in `write_paths` frontmatter, and
`hooks/_write_scope.py` is the single authority that decodes, expands, and composes
it. A declaration has exactly one of three kinds:

| Kind | Frontmatter | Interactive session | Headless closure | `output_dir` narrowing |
|---|---|---|---|---|
| BOUNDED | non-empty list of `{{AUTOSKILLIT_TEMP}}/…` or `.autoskillit/temp/…` directories | union of its prefixes; activates containment | union of its prefixes | enforced by `run_skill` and the recipe rule |
| UNRESTRICTED | `write_paths: unrestricted` | the session is unrestricted, whatever else is loaded | contributes nothing; the dispatcher supplies the scope | not applicable |
| INHERIT | `write_paths: inherit` | abstains: neither widens nor activates containment | contributes nothing | not applicable |

An absent declaration is a `write_boundary_undeclared` contract invalidity, so an
undeclared skill never reaches the catalog or projection manifest; `autoskillit
migrate` inserts `inherit`, which preserves the pre-contract behavior. `null`, an
empty list, and any other value are `write_boundary_invalid`.

Loaded skills compose by union in both session classes: the interactive write guard
folds the bound skills' scopes, and headless `run_skill` folds its dependency
closure through the same function, so the two cannot drift. A declared directory
whose realpath leaves `<cwd>/.autoskillit/temp` is rejected in both.

Cross-skill intersection was retired. Declared BOUNDED scopes are sibling
directories, so no loaded skill's scope nests inside another's in practice, and
narrowing one sibling by another only denied legitimate chains such as
`investigate → prepare-issue`. Narrowing survives where it means something: a
single skill's scope against one dispatch's `output_dir`.

A skill the projection manifest does not know — another plugin's skill or a
built-in command — is bound as foreign: it abstains from containment and from join
obligations and never invalidates the session binding. An `autoskillit:`-named skill
absent from the manifest, an unreadable or malformed manifest entry, and an
unreadable binding are unresolved and deny with the precise cause. A binding
written under an earlier schema, as in a session resumed across an upgrade, is
rejected with `unsupported session-binding schema_version` and fails closed; start
a new session.

The installation-integrity guard remains active without a skill binding and protects
AutoSkillit installation trees in every session class.

The protection waiver registry records each intentionally excluded session class.
Most scoped guards address a policy that does not apply in the excluded class: for
example, interactive users may ask questions while headless workers cannot wait
for one. Those entries say `not-applicable` and explain the policy boundary.
The interactive PR creation guard is a reachable hook delegate for the headless
PR body guard. Codex's workspace sandbox covers the write-prefix guard's Codex
exit; the installation-integrity guard still checks protected package targets.
