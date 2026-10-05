---
name: legacy-namespaced-role
description: A previously valid skill with a namespaced logical role.
semantic_version: 1
semantic_requirements:
  logical_roles:
    - name: "autoskillit:plan-foundation-auditor"
      purpose: Audit one plan slice.
  child_spawns:
    - role: "autoskillit:plan-foundation-auditor"
      count: 1
---
# legacy-namespaced-role

Delegates one plan slice to the registered plan foundation auditor.
