---
name: eval-agent
categories: [eval]
description: >
  Invoke a named agent definition against a provided prompt and capture its output.
  Execution primitive for the agent-eval recipe — isolates a single agent invocation.
---

# Eval Agent Wrapper

Invokes a named agent definition against a provided prompt, captures the full response,
and writes it to a JSON output file. Stateless — does not know about canaries, variants,
or evaluation. Runs one agent against one prompt and captures output.

## When to Use

- Called by the `agent-eval` recipe for each canary x variant evaluation
- Testing individual agent definitions in isolation

## Arguments

The skill receives its arguments as a single string. Parse these arguments:

- `--agent-name {name}` — The agent definition name (without `autoskillit:` prefix).
  The skill prepends `autoskillit:` when calling the Agent tool.
- `--prompt-file {path}` — Absolute path to a file containing the prompt text.

Example invocation:
```
/eval-agent --agent-name wp-elaborator --prompt-file /tmp/test-prompt.txt
```

## Critical Constraints

**NEVER:**
- Fabricate, invent, or embellish information not supported by the available evidence or code.
- Modify any source code files
- Use MCP tools (open_kitchen, run_skill, run_cmd, run_python) — this skill uses only native Claude Code tools
- Create files outside `{{AUTOSKILLIT_TEMP}}/eval-agent/`
- Run subagents in the background (`run_in_background: true` is prohibited)
- Skip writing the output file on agent failure — always write a result
- Use output files as a notepad — do not add useless comments, extraneous fields, or AI-generated boilerplate to the JSON output

**ALWAYS:**
- Parse `--agent-name` and `--prompt-file` from the ARGUMENTS string
- Read the prompt file using the Read tool
- Invoke the agent via `Agent(subagent_type="autoskillit:{agent_name}", prompt=<file contents>)`
- Write output JSON to `{{AUTOSKILLIT_TEMP}}/eval-agent/{agent_name}_output.json` (relative to the current working directory) using the Write tool
- Emit `agent_output_path = <absolute_path>` as a structured output token (plain text, no markdown formatting on the token name)
- The absolute path in the structured output token must be constructed by prepending the current working directory to the relative output path

## Workflow

### Step 1: Parse Arguments

Extract `--agent-name` and `--prompt-file` values from the ARGUMENTS string.

If either argument is missing, proceed to Step 4 (error handling) with an error message
describing the missing argument.

### Step 2: Read Prompt File

Use the `Read` tool to read the contents of the file at the `--prompt-file` path.

If the file cannot be read, proceed to Step 4 (error handling).

### Step 3: Invoke Agent and Capture Output

Invoke the agent using the native Agent tool:

```
Agent(subagent_type="autoskillit:{agent_name}", prompt=<prompt file contents>)
```

Capture the agent's full response text.

On success, write the output JSON:

```json
{
  "agent_name": "{agent_name}",
  "success": true,
  "output": "<agent response text>"
}
```

On agent failure, proceed to Step 4.

### Step 4: Handle Errors

If any step fails, write an error output JSON:

```json
{
  "agent_name": "{agent_name}",
  "success": false,
  "error": "<description of what failed>",
  "output": null
}
```

### Step 5: Write Output and Emit Token

Write the JSON (success or error) to:
`{{AUTOSKILLIT_TEMP}}/eval-agent/{agent_name}_output.json`

Use the native `Write` tool. Construct the absolute path by joining the current working
directory with the relative path.

Emit the structured output token as the final output:

```
agent_output_path = /absolute/path/to/.autoskillit/temp/eval-agent/{agent_name}_output.json
```

## Output

Output file: `{{AUTOSKILLIT_TEMP}}/eval-agent/{agent_name}_output.json` (relative to the current working directory)

Structured output token: `agent_output_path = <absolute_path>`
