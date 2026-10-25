# Claude 5.5 Model Pins Verification (#5276)

Date: 2026-10-25
Base: `f9d103002` (`develop`)
Client: `2.1.296 (Claude Code)`

The evidence client was installed with
`npm install --prefix .autoskillit/temp/5276/cc @anthropic-ai/claude-code@2.1.296`.
Raw stdout, stderr, and stub requests are retained under `.autoskillit/temp/5276/`
in the original checkout. They are local artifacts, excluded from the commit.

## Isolation and authentication

Every probe used `env -i`, preserving only `PATH`, an explicit fresh `HOME`, and
the credentials or stub routing needed by that probe. Working directories were
`/`, outside either checkout. For tool checks, notes and an empty extra directory
lived under the evidence directory and prompts used the absolute notes path;
`--allowedTools Read` enabled the read without an interactive permission prompt.
Each start/resume pair shared its isolated home and working directory.

The first-party credential was the existing Claude login's access token, passed
as `CLAUDE_CODE_OAUTH_TOKEN`. No provider endpoint override was supplied. MiniMax's
token came from AutoSkillit's merged provider configuration, using
`load_config(project_root).providers.profiles["minimax"]["ANTHROPIC_AUTH_TOKEN"]`.
The E2E harness's `write_claude_settings` wrote its exact provider settings into a
separate home, with no first-party credential in that process's environment.
That home was deleted after the canaries. Credential values are not in this record.

No probe supplied `--effort`, `CLAUDE_CODE_EFFORT_LEVEL`, `CLAUDE_CONFIG_DIR`, or a
saved `effortLevel`. User and project settings did not participate.

## Decisions

- Pin all classes: `sonnet` → `claude-sonnet-5-5`, `opus` → `claude-opus-5-5`,
  `haiku` → `claude-haiku-5-5`. Triage uses the backend's translation of `haiku`.
  The [provider alias table](https://code.claude.com/docs/en/model-config#model-aliases)
  documents provider-dependent aliases, which can select older versions.
- Set the support floor to 2.1.296. The documented per-model minimums are
  2.1.280 for Opus 5.5, 2.1.284 for Sonnet 5.5, and 2.1.293 for Haiku 5.5.
  [Release 2.1.296](https://github.com/anthropics/claude-code/releases/tag/v2.1.296)
  fixes adaptive-thinking token counts, including Haiku behind gateways.
  The below-floor fixture is 2.1.295. The registry test enforces #5157's rule
  that the client floor must support every pinned model; image and CI tests
  tie their pins to that floor.
- Keep effort unset. The measurements below establish each class's default,
  rather than choosing effort for AutoSkillit. Whether `medium` is suitable
  remains a per-class judgement; an unsuitable class needs a follow-up issue.
  These short probes do not establish coding-task quality.
- Remove `[1m]` from the E2E Claude model. The successful canaries below reported
  native 1M windows. Configured suffixes remain supported by model translation;
  `sonnet[1m]` would still become `claude-sonnet-5-5[1m]`. The unrecognized
  `MiniMax-M3[1m]` provider ID keeps its explicit window declaration.
- Couple pins to minimum client releases in
  `CLAUDE_MODEL_MIN_CLIENT_VERSIONS` in the alias-registry tests. The Haiku entry
  requires the token-count correction release, beyond its launch minimum.
- Keep #4412 open: this change pins Haiku but adds neither an alias-freshness
  marker nor a doctor freshness check. #5274's suffix removal accompanies the pins.

## Authenticated first-party launches

Each command was `claude -p "Reply with exactly OK" --output-format stream-json
--verbose --model <id>`. All replies were `OK`. The init record, command argument,
and sole usage key agreed on the requested ID; assistant records independently
reported the served model.

| Class | Requested ID and usage key | Served assistant model | Context window | Maximum output | is_error | Credential |
|---|---|---|---:|---:|---|---|
| sonnet | `claude-sonnet-5-5` | `claude-sonnet-5-5` | 1000000 | 128000 | false | Claude OAuth |
| opus | `claude-opus-5-5` | `claude-opus-5-5` | 1000000 | 128000 | false | Claude OAuth |
| haiku | `claude-haiku-5-5` | `claude-haiku-5-5` | 1000000 | 128000 | false | Claude OAuth |

## Effort with no user-level setting

A stdlib HTTP server on `127.0.0.1` accepted Messages requests, ignoring the query
string when matching paths. It returned a token count or a minimal streaming
text reply. Each client received only its isolated home, stub endpoint, and
dummy API key. The recorded requests contained the prompt `hi` and the requested
model. Only the beta, API-version, and user-agent headers were retained.

| Class | Requested model | output_config.effort | anthropic-beta |
|---|---|---|---|
| sonnet | `claude-sonnet-5-5` | `medium` | Sonnet/Opus value below |
| opus | `claude-opus-5-5` | `medium` | Sonnet/Opus value below |
| haiku | `claude-haiku-5-5` | `medium` | Haiku value below |

Sonnet/Opus beta header, in observed order:

```text
claude-code-20250219,interleaved-thinking-2025-05-14,thinking-token-count-2026-05-13,context-management-2025-06-27,prompt-caching-scope-2026-01-05,mid-conversation-system-2026-04-07,per-turn-control-2026-07-01,mid-conversation-tool-changes-2026-07-01,inline-tools-2026-09-15,advisor-tool-2026-03-01,effort-2025-11-24
```

Haiku beta header, in observed order:

```text
interleaved-thinking-2025-05-14,thinking-token-count-2026-05-13,context-management-2025-06-27,prompt-caching-scope-2026-01-05,mid-conversation-system-2026-04-07,claude-code-20250219,per-turn-control-2026-07-01,mid-conversation-tool-changes-2026-07-01,inline-tools-2026-09-15,advisor-tool-2026-03-01,effort-2025-11-24
```

No context-1m beta was present. The stub measures what Claude Code sends; it does
not measure how a provider executes that effort setting.

## MiniMax canary

The harness settings supplied `https://api.minimax.io/anthropic` and its auth token.
Commands used the harness canary prompt, JSON output, and the explicit IDs below.
Both returned `AUTOSKILLIT-E2E-CANARY-OK` with is_error literally false.

| Requested ID | Token present | All usage keys | Reported context window |
|---|---|---|---:|
| `claude-sonnet-5-5` | yes | `claude-sonnet-5-5` | 1000000 |
| `claude-haiku-5-5` | yes | `claude-haiku-5-5` | 1000000 |

These replies establish the configured MiniMax route accepts the requests. The
usage metadata is Claude Code's assigned window, not proof of a million-token
request succeeding at the gateway. The harness retains its observed-window checks.
JSON result envelopes did not expose an assistant model for these canaries.

## Completion marker and resume

Each marker probe read the notes with the Read tool and emitted `%%ORDER_UP%%`
alone on the final line. Both the final result text and an assistant text block
contained that standalone line, covering the two completion-detection channels.

| Model | Read tool used | Result marker | Assistant text marker |
|---|---|---|---|
| `claude-sonnet-5-5` | yes | pass | pass |
| `claude-opus-5-5` | yes | pass | pass |
| `claude-haiku-5-5` | yes | pass | pass |

Sonnet 5.5 and Haiku 5.5 each started with a notes-read request and `--add-dir`.
Each then resumed with `--resume`, `--dangerously-skip-permissions`, JSON output,
and neither `--model` nor `--add-dir`. Both succeeded and returned `RESUME-OK`.
Haiku's resume usage included 338 thinking tokens; no thinking-block rejection
occurred. This is a short-session check, not exhaustive transcript compatibility.

A session started with `--model claude-sonnet-5` then resumed with
`--model claude-sonnet-5-5`. It returned `UPGRADE-OK`, is_error false. Its init
model and new usage entry were `claude-sonnet-5-5`, and the served assistant model
was also `claude-sonnet-5-5`. Cumulative usage retained the earlier Sonnet 5 entry.

AutoSkillit's resume checks remain separate from that direct CLI success.
The managed adapter stores the configured class in `physical_model`; the generic
`validate_resume` rejects changed plugin identities and projection digests.
Child model policies include translated pins in adaptation digests, so changing a
pin can change the projection and trigger a refusal for those skills. An installed
publication also mints a new plugin incarnation. The plan expected an installed-update
refusal through plugin identity, but the current headless skill preparation supplies
an empty plugin identity. That scenario was not established by these CLI probes;
this change does not alter resume identity handling. The generic drift checks are
covered by `tests/execution/test_launch_resolution.py`.

## Haiku launches and quota

Triage, haiku-class `run_skill` calls, and recipe-step models now pass
`claude-haiku-5-5`. In-session `Agent(..., model='haiku')` subagents and
`model: haiku` agent definitions still use Claude Code's alias. Triage ignores
AutoSkillit model overrides; Claude Code's own `modelOverrides` can map its ID.
Triage is outside the pre-launch version check. A rejected call, refusal, or
too-old client can produce an error or empty result; parsing then marks all items
meaningful with the summary "Batch triage parse failed." It does not identify the
cause, and no server-side refusal fallback was added.

The [Haiku context and pricing documentation](https://code.claude.com/docs/en/model-config#haiku-55-context-window-and-pricing)
describes higher per-token rates for prompts above 100K tokens. This session did
not exercise that threshold or refusal classifiers. Existing consumers now select
Haiku 5.5 rather than retaining Haiku 4.5 behavior.

Quota `long_window_patterns` classify usage-API window names in `_is_long_window`,
not model IDs. No quota-pattern change is needed.

## Remaining external verification

The conformance workflow's Ubuntu and macOS rows now select 2.1.296. Their CI runs,
including the live output-budget smoke probe, and the E2E canary workflow need
post-push run URLs and outcomes in the PR description. Local direct CLI probes
above do not substitute for those workflow runs.
