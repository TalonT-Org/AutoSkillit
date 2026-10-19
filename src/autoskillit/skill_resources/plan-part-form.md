---
id: plan-part-form
title: Plan-part authoring form
summary: Shared authoring rules for plan parts accepted by the plan-set allocation verifier.
---
**Plan-part form.** `bind_plan_set` deterministically checks the allocation table,
resolves each row's step key and checks that its ID cell occurs in that step's text.
Follow this authoring form; the verifier enforces these allocation rules:
- Write each step as a level-3 heading `### Step N: title` (sub-steps
  `### Step N.M: title`). Other heading levels, and a dash in place of the colon, are
  not recognised. Do not repeat a step heading anywhere in the file, quoted examples
  included.
- On the line under each heading, name every Requirement ID the step delivers
  (`Requirements: P-1, P-2`). The ID must appear in that step's own text, which ends at
  the next step heading or the next line starting with `# ` or `## `, code fences
  included.
- The line `## Issue Requirement Allocation` appears once in the file, and its section
  holds only the table: no sentence before, between or after the rows.
- An implementation row is the bare Requirement ID with no description, then `owned`
  or `shared`, then exactly one step key copied from a heading (`Step 2.1`): not a list,
  a range, a backticked key or a key with its title. Use one implementation row per ID.
- A stage row is the bare Requirement ID, then `stage`, then exactly one stage obligation
  key from this list:
  - `pr_opened` — the recipe must open the pull request; OPEN or MERGED on the expected
    base branch satisfies the outcome.
  - `pr_left_unmerged` — the pull request must remain OPEN on the expected base branch.
- When one requirement has both code work and a recipe-owned outcome, allocate one `owned`
  or `shared` implementation row plus its `stage` rows. A stage-only ID is never cited in
  an implementation step. Use `stage` only for an outcome the source explicitly requests.
- Use the issue's own labels as IDs when it enumerates items; otherwise number your own
  `P-1`, `P-2`. `owned` means this part alone delivers the item; `shared` means another
  part allocates it too.
Complete stage-only allocation example:

```markdown
## Issue Requirement Allocation
| Requirement ID | Allocation | Implementation Step |
|---|---|---|
| R3 | stage | pr_opened |
```
