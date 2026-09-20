# Skill write boundaries

Interactive write containment uses the loaded skill's `write_paths` frontmatter as
the authority for allowed directories. An omitted value leaves the skill unrestricted;
an empty list makes it read-only. When several skills are loaded, their boundaries are
intersected so a later skill can narrow the allowed set but cannot widen it.

The installation-integrity guard remains active without a skill binding and protects
AutoSkillit installation trees in every session class.
