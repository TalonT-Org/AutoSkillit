# Skill write boundaries

Interactive write containment uses the loaded skill's `write_paths` frontmatter as
the authority for allowed directories. An omitted value leaves the skill unrestricted;
an empty list makes it read-only. When several skills are loaded, their boundaries are
intersected so a later skill can narrow the allowed set but cannot widen it.

The installation-integrity guard remains active without a skill binding and protects
AutoSkillit installation trees in every session class.

The protection waiver registry records each intentionally excluded session class.
Most scoped guards address a policy that does not apply in the excluded class: for
example, interactive users may ask questions while headless workers cannot wait
for one. Those entries say `not-applicable` and explain the policy boundary.
The interactive PR creation guard is a reachable hook delegate for the headless
PR body guard. Codex's workspace sandbox covers the write-prefix guard's Codex
exit; the installation-integrity guard still checks protected package targets.
