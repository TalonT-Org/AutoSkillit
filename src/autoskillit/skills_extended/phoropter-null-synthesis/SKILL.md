---
name: phoropter-null-synthesis
categories: [research, arch-lens, exp-lens]
write_paths: ["{{AUTOSKILLIT_TEMP}}/phoropter-null-synthesis/"]
description: >
  Concatenate lens output markdown files from a capture directory in
  lexicographic order without reordering, conflict resolution, or
  structured-block parsing, producing a single synthesis-result.md for
  downstream consumption by arch-lens and exp-lens phoropter families.
---

# Phoropter Null-Synthesis Skill

Identity pass-through aggregation skill for the arch-lens and exp-lens
phoropter families. Reads all `.md` files in the capture directory in
lexicographic (alphabetical filename) order, concatenates their contents with
per-file header dividers, and writes a single `synthesis-result.md` output
file. No conflict resolution, no priority ordering, no structured-block
parsing.

## When to Use

- As the `synthesize` step of the arch-lens or exp-lens phoropter in the
  `research` recipe, when no conflict resolution or structured figure-spec
  parsing is needed
- Intended consumers: **arch-lens** and **exp-lens** lens families
- on_success: `create_worktree`
- on_failure: `escalate_stop`

## Arguments

```
/autoskillit:phoropter-null-synthesis {source_dir} {capture_dir}
```

**Positional arguments:**

- `{source_dir}` — Absolute path to the source repo (the CWD before worktree
  creation)
- `{capture_dir}` — Absolute path to the directory containing lens output `.md`
  files to be concatenated

## Critical Constraints

**NEVER:**
- Fabricate, invent, or embellish information not present in the input files.
- Apply priority ordering of lens outputs
- Reorder or filter any lens file content
- Spawn sub-agents or run sub-agents in the background
- Write outputs outside `{{AUTOSKILLIT_TEMP}}/phoropter-null-synthesis/`

**ALWAYS:**
- Read files in lexicographic (alphabetical filename) order
- Write `synthesis-result.md` unconditionally — even when `capture_dir` is
  empty, write an empty synthesis file
- Emit `synthesis_result_path` as a literal plain-text token with no markdown
  formatting
- Use `{{AUTOSKILLIT_TEMP}}/phoropter-null-synthesis/` (relative to the current working directory) for all output paths

---

## Workflow

### Step 0 — Parse Arguments

Extract positional arguments:
- `source_dir` — the source repository path
- `capture_dir` — directory containing lens output `.md` files

### Step 1 — Read Lens Output Files

Read all `.md` files in `capture_dir` in lexicographic (alphabetical filename)
order. Collect the filename and full text content of each file.

If `capture_dir` is empty or contains no `.md` files, proceed to Step 2 with
an empty file list.

### Step 2 — Write synthesis-result.md

Write `synthesis-result.md` to `{{AUTOSKILLIT_TEMP}}/phoropter-null-synthesis/synthesis-result.md`.

For each file collected in Step 1, write a header divider followed by the
file's full content:

```markdown
## {filename}

{full file content}
```

Concatenate all files in the order they were read (lexicographic). If the file
list is empty, write an empty file.

### Step 3 — Emit Structured Token

> **IMPORTANT:** Emit the structured output token as **literal plain text with
> no markdown formatting on the token name**. Do not wrap token names in
> `**bold**`, `*italic*`, or any other markdown. Do not wrap the output block
> in a code fence. The adjudicator performs a regex match on the exact token
> name — decorators and code fences cause match failure.

```
synthesis_result_path = {absolute_path_to_{{AUTOSKILLIT_TEMP}}/phoropter-null-synthesis/synthesis-result.md}
```

The token is mandatory — always emit a non-null absolute path. The output file
is always written (even for empty capture directories), so the token is always
non-null.
