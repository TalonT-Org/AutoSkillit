---
id: plan-part-form
title: Plan-part authoring form
summary: Shared authoring rules for plan parts accepted by the plan-set allocation verifier.
---
**Plan-part form.** `bind_plan_set` deterministically checks the allocation table,
resolves each row's step key and checks that its ID cell occurs in that step's text.
Follow this authoring form; the verifier's existing acceptance rules stay unchanged:
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
- Each row is the bare Requirement ID with no description, then `owned` or `shared`,
  then exactly one step key copied from a heading (`Step 2.1`): not a list, a range, a
  backticked key or a key with its title. One row per ID.
- Use the issue's own labels as IDs when it enumerates items; otherwise number your own
  `P-1`, `P-2`. `owned` means this part alone delivers the item; `shared` means another
  part allocates it too.
