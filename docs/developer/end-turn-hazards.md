# End-Turn Hazards in Skill Authoring

## The Problem

Claude sessions terminate when the model produces an `end_turn` stop reason
instead of a `tool_use` stop reason. In headless sessions (no human to press
Enter), an `end_turn` is final -- the session is over. The model decides
which stop reason to emit based on what it believes it should do next: if
the next action is a tool call, it emits `tool_use`; if the next action is
"I'm done" or "let me explain what I just did", it emits `end_turn`.

The dangerous scenario is when the model produces **text output between two
tool calls**. Even a single line of prose like "Done with diagram A. Now
generating diagram B:" causes the API to return with `stop_reason: end_turn`.
The session terminates. The remaining work never executes.

This is **stochastic**, not deterministic. The same SKILL.md instructions
may succeed 4 out of 5 runs and fail on the 5th, because the model's
decision to emit prose between tool calls depends on sampling.

## Why Text Between Tool Calls Is Fatal

The Claude API has exactly two relevant stop reasons during a session:

| Stop reason | What happens | Session continues? |
|---|---|---|
| `tool_use` | Model wants to call a tool | Yes -- tool executes, result fed back |
| `end_turn` | Model is done talking | **No** -- session ends |

There is no "pause and resume" mechanism. When the model generates text
(even one sentence), the API returns `end_turn`. In an interactive CLI
session, the human sees the text and the conversation continues on the next
input. In a headless session, there is no next input. The process exits.

AutoSkillit's runtime detects this as `EARLY_STOP` (a `NATURAL_EXIT` with
no completion marker) and can retry, but retries restart the entire session
from scratch. For a skill on iteration 5 of 8, that re-executes iterations
1--4 wastefully and can fail at the same boundary again.

## The Two Anti-Patterns

### 1. Text-Then-Tool (Intra-Step)

A numbered sub-step tells the model to output prose, and the next sub-step
tells it to make a tool call:

```markdown
### Step 5: Generate Diagrams

**1. Output the PR context block as plain text (NOT as a tool call):**

> [context block here]

**2. THEN load the arch-lens skill via the Skill tool.**
```

After executing sub-step 1 (text output), the model has produced text. The
API returns `end_turn`. Sub-step 2 never executes.

**Fix:** Replace the text output with a Write tool call. Tool-then-tool has
no `end_turn` gap:

```markdown
**1. Write the PR context to a file using the Write tool:**

- Path: .autoskillit/temp/pr-arch-lens-context.md

**2. Immediately call the Skill tool to load the arch-lens skill.**
```

### 2. Loop-Boundary (Inter-Iteration)

A "For each X" loop contains tool calls. After completing one iteration,
the model naturally generates progress text before starting the next
iteration:

```markdown
### Step 5: Generate Diagrams

For each selected lens, follow this exact sequence:

**1. Write the PR context to a file using the Write tool:**
**2. Call the Skill tool to load the arch-lens skill.**
**3. Follow the loaded skill's instructions.**
```

On a good run, the model finishes sub-step 3 for lens A and immediately
starts sub-step 1 for lens B (tool call, no gap). On a bad run, it emits:

> "Process flow diagram generated successfully. Now generating the
> operational diagram:"

That text triggers `end_turn`. Lens B never executes.

**Fix:** Add an explicit anti-prose guard in the loop prologue:

```markdown
For each selected lens, follow this exact sequence:

**CRITICAL:** Do NOT output any prose status text between lens iterations.
After completing all sub-steps for one lens, immediately begin sub-step 1
for the next lens. Progress announcements create end_turn windows that
cause stochastic session termination.

**1. Write the PR context to a file using the Write tool:**
**2. Call the Skill tool to load the arch-lens skill.**
**3. Follow the loaded skill's instructions.**
```

## Why the Guard Works (And Its Limits)

The anti-prose guard is a **probabilistic** fix, not a structural one. It
works because:

- Claude respects explicit instructions about output format with high
  reliability
- The "CRITICAL" framing and concrete examples ("Progress announcements
  like 'Diagram generated. Now calling X:'") give the model a clear
  negative example to avoid
- Placing the guard in the loop prologue (before the first sub-step) means
  the model reads it before each iteration

It does **not** provide a 100% guarantee. The model can still occasionally
emit text despite the instruction. That is why runtime detection
(`EARLY_STOP` in `execution/session.py`) exists as a defense-in-depth
layer, retrying sessions that terminate prematurely.

## Reproducing the Problem

To observe the failure mode in a controlled way:

1. Create a test SKILL.md with a loop that has no guard:

```markdown
# Test Skill

### Step 1: Generate Files

For each item in the list [alpha, beta, gamma]:

**1.** Create a file at `.autoskillit/temp/test-{item}.txt` using the Write tool.
**2.** Read the file back using the Read tool to confirm it exists.
```

2. Run the skill as a headless session multiple times. On some runs, the
   model will emit text like "Created alpha.txt. Moving on to beta:" between
   iterations, and the session will terminate after only 1 or 2 iterations.

3. Add the anti-prose guard after the "For each" line and re-run. The
   failure rate drops significantly.

The failure is more likely when:
- The loop body is short (the model reaches the boundary quickly)
- The loop has many iterations (more boundaries = more chances to fail)
- The tool calls produce visible output the model wants to summarize

## Why Recipes Are Immune

The recipe system handles iteration as Python-level routing between
discrete `RecipeStep` objects. Each step is an atomic MCP tool call that
returns a `SkillResult`. The inter-iteration boundary is a Python function
return, not a model response boundary.

```yaml
# In a recipe, the "loop" is:
#   next_or_done → verify → implement → test → merge → next_or_done
# Each arrow is a Python routing decision, not model inference.
```

Key properties:
- `RecipeStep` has no "prose" field -- only `tool`, `action`, `python`, or
  `constant`
- Routing is declarative YAML validated by semantic rules
- The orchestrator executes tool calls -- it never produces unstructured
  text as a step boundary

Skills, by contrast, use free-form SKILL.md instructions processed by a
single Claude session. Loop iteration is driven by model inference, not
structured routing. The model's natural language behavior includes progress
updates between iterations.

## CI Enforcement

The compliance test suite (`tests/skills/test_skill_compliance.py`) runs
two detectors on every SKILL.md in the project:

| Detector | What it catches |
|---|---|
| `_check_text_then_tool()` | Consecutive numbered sub-steps where one outputs text and the next calls a tool |
| `_check_loop_boundary()` | "For each" loops with tool invocations but no anti-prose guard |

These run as part of `test_no_text_then_tool_in_any_step`, a parametrized
test that scans all 63 bundled skills. Any new skill with an unguarded
loop fails CI automatically.

## If This Gets Fixed Upstream

This entire class of problems goes away if Claude's API adds a mechanism
for headless sessions to continue after text output without requiring a
new human turn. Possible future fixes:

- A session mode where `end_turn` is suppressed and the model continues
  until it explicitly signals completion
- A "thinking aloud" output channel that doesn't trigger `end_turn`
- Structured loop primitives in the tool-use protocol

If any of these land, the anti-prose guards become unnecessary (but
harmless). The compliance tests can be relaxed by removing
`_check_loop_boundary()` from the project-wide scan. The guards in
SKILL.md files can be removed at that point for cleanliness.

Until then, every "For each" loop that contains tool invocations needs a
guard, and the CI test enforces this.
