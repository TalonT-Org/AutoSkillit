---
name: legacy-child-spawn-cardinality
description: A previously valid skill whose child cardinality was implicit.
semantic_version: 1
semantic_requirements:
  logical_roles:
    - name: worker
      purpose: Process one fixed task.
  child_spawns:
    - role: worker
---
# legacy-child-spawn-cardinality

Delegates one fixed task and joins the result.
