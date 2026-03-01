# Smoke Task Skill

Execute an arbitrary prose task as a headless Claude session. For smoke-test pipeline use only.

## Usage

    /autoskillit:smoke-task <prose description of the task>

## When to Use

Used exclusively by `recipes/smoke-test.yaml` to test that the headless runner can execute
arbitrary tasks and produce capturable outputs. Not for production pipeline use.

## Instructions

Execute the task described in the argument provided to this skill invocation.
Complete the task exactly as specified. If the task asks you to output a specific line
(e.g. `key=value`), output it on its own line so it can be captured.
