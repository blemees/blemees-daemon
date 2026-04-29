# `agent.*` event vocabulary

Authoritative mapping for the unified event namespace introduced in
`blemees/2`. Every event the daemon forwards is normalised into one of
the types below, regardless of which backend produced it.

This document is the contract for the translation layer. The two
backends speak very different native protocols:

* **Claude Code** writes [Anthropic stream-json](https://docs.anthropic.com/en/docs/build-with-claude/streaming) line-delimited
  events on the `claude -p` child's stdout: `system`, `stream_event`
  (Anthropic Messages API `MessageStreamEvent`), `assistant`, `user`,
  `partial_assistant`, `result`.
* **Codex** runs as an MCP server (`codex mcp-server`). It speaks
  JSON-RPC 2.0 over stdio, with a custom `notifications/codex/event`
  for streaming. The standard MCP `notifications/progress` is not
  used. Each event carries `_meta.{requestId,threadId}` and a
  `msg.{type,...}` body. The final result of a `tools/call` arrives as
  the JSON-RPC response, with `structuredContent.threadId` for resume.

The translation table below maps both feeds onto a single set of
`agent.*` frames clients can switch on without branching by backend.

## Common fields

Every `agent.*` frame the daemon emits carries:

| Field | Required | Notes |
|---|---|---|
| `type` | yes | One of the types in the table below. |
| `session_id` | yes | The blemees session id (the daemon's, not the backend's). |
| `seq` | yes | Monotonic per-session integer. |
| `backend` | yes | `"claude"` or `"codex"`. Lets clients still distinguish if they want to. |
| `raw` | optional | The native event the daemon translated from. Off by default; opt in per session via `blemeesd.open.options.<backend>.include_raw_events: true`. Format is the *un-namespaced* native frame (CC's stream-json line dict, or Codex's `msg` body). |

The on-the-wire shape never carries `null`s for absent fields — keys
either appear with a value or are omitted entirely.

## Event vocabulary

| Type | Purpose | Payload (besides common fields) |
|---|---|---|
| `agent.system_init` | First frame after spawn. Tells the client which backend, model, cwd, tools, and native session id are in play. | `model?, cwd?, tools?, native_session_id?, capabilities?, context_window?` |
| `agent.delta` | Incremental output during a turn. | `kind: "text" \| "thinking" \| "tool_input"`, plus one of: `text` (text/thinking) or `partial_json` (tool_input). May carry `item_id?` (Codex) or `index?` (CC content-block index) for clients that want to reassemble. |
| `agent.message` | A complete message from the assistant role (post-stream). | `role: "assistant", content: [...], phase?` |
| `agent.user_echo` | Echo of a user-side message the backend replays in its event stream — useful for logging, not for the original turn. CC emits these for both real user turns and for tool-result blocks; Codex emits them for the user message after the turn starts. | `message: { role:"user", content:... }` |
| `agent.tool_use` | A tool invocation request emitted by the model. | `tool_use_id, name, input` |
| `agent.tool_result` | The result the backend received for a tool invocation. | `tool_use_id, output, is_error?` |
| `agent.notice` | Backend-side informational events that are neither output nor errors — `mcp_startup_*` from codex, rate-limit pings, etc. Clients may ignore. | `level: "info" \| "warn", category: string, text?, data?` |
| `agent.result` | Turn-end. Always the last frame for a turn. The daemon uses this to mark `turn_active=False`. | `subtype: "success" \| "error" \| "interrupted" \| ..., duration_ms?, num_turns?, turn_id?, usage?: NormalisedUsage` |

`NormalisedUsage`:

```jsonc
{
  "input_tokens": 0,
  "output_tokens": 0,
  "cache_read_input_tokens": 0,       // CC `cache_read_input_tokens` / Codex `cached_input_tokens`
  "cache_creation_input_tokens": 0,   // CC only; absent for Codex
  "reasoning_output_tokens": 0        // Codex only; absent for CC
}
```

The accumulator keeps unknown keys verbatim so future fields pass
through (existing CC behaviour). `reasoning_output_tokens` is **not**
folded into `output_tokens`: surfacing them separately matches what
Codex actually meters and lets clients budget independently.

## Translation: Claude Code → `agent.*`

| CC native event | `agent.*` translation | Notes |
|---|---|---|
| `system{subtype:"init"}` | `agent.system_init{model, cwd, tools, native_session_id: <CC session-id>}` | One frame per spawn. Pass `tools` array through verbatim. |
| `system{subtype:"<other>"}` | `agent.notice{category:"system_<subtype>", data:<rest>}` | Forward-compat for future CC system frames. |
| `stream_event{message_start}` | dropped (folded into `agent.system_init` if not yet emitted) | |
| `stream_event{content_block_start{type:"text"}}` | dropped | Block boundary; deltas alone carry the content. |
| `stream_event{content_block_start{type:"tool_use", id, name, input?}}` | `agent.tool_use{tool_use_id:id, name, input: {} \| input}` | Initial tool_use blocks usually have empty `input` filled in by `input_json_delta` events. |
| `stream_event{content_block_start{type:"thinking"}}` | dropped | |
| `stream_event{content_block_delta{delta:{type:"text_delta", text}}}` | `agent.delta{kind:"text", text, index}` | |
| `stream_event{content_block_delta{delta:{type:"thinking_delta", thinking}}}` | `agent.delta{kind:"thinking", text:thinking, index}` | |
| `stream_event{content_block_delta{delta:{type:"input_json_delta", partial_json}}}` | `agent.delta{kind:"tool_input", partial_json, index}` | Client must accumulate. |
| `stream_event{content_block_stop}` | dropped | |
| `stream_event{message_delta{usage}}` | dropped | Final usage arrives on `result`. |
| `stream_event{message_stop}` | dropped | |
| `assistant{message}` | `agent.message{role:"assistant", content: message.content}` | |
| `partial_assistant{message}` | dropped (only `--include-partial-messages` produces these; redundant once we emit deltas) | |
| `user{message: {content: string \| [text-only]}}` | `agent.user_echo{message}` | |
| `user{message: {content: [..., {type:"tool_result", tool_use_id, content, is_error}, ...]}}` | one `agent.tool_result{tool_use_id, output:content, is_error}` per `tool_result` block; remaining text blocks emit a single `agent.user_echo`. | |
| `result{subtype, duration_ms, num_turns, usage}` | `agent.result{subtype, duration_ms, num_turns, usage: <pass-through>}` | |

## Translation: Codex MCP → `agent.*`

Codex's stream is `notifications/codex/event` frames carrying
`msg.{type,...}`. Most fields below come from `msg`; `_meta.threadId`
is the native session id surfaced on `agent.system_init`. The final
`agent.result` is synthesised from the JSON-RPC `result` of the
originating `tools/call`, plus the preceding `task_complete` and last
`token_count`.

| Codex `msg.type` | `agent.*` translation | Notes |
|---|---|---|
| `session_configured` | `agent.system_init{model, cwd, native_session_id: msg.session_id, capabilities: {sandbox_policy, approval_policy, permission_profile, reasoning_effort, rollout_path}}` | One per spawn. `model_provider_id`, `history_*` go under `raw`. |
| `mcp_startup_update` | `agent.notice{level:"info", category:"backend_mcp_startup", data:{server, status}}` | Codex's own external MCP children. |
| `mcp_startup_complete` | `agent.notice{level:"info", category:"backend_mcp_startup_complete", data:{ready, failed, cancelled}}` | |
| `task_started` | `agent.notice{level:"info", category:"task_started", data:{turn_id, model_context_window, started_at}}` *or* fold `model_context_window` into `agent.system_init` if not yet emitted | We chose: fold context window into init when known; emit notice with `turn_id`. |
| `raw_response_item` | dropped from primary stream; surfaced under `raw` when opt-in | Duplicates the structured `item_*` events. |
| `item_started{item:{type:"UserMessage", content}}` | dropped (we wait for completed) | |
| `item_completed{item:{type:"UserMessage", content}}` | `agent.user_echo{message:{role:"user", content: <translated>}}` | |
| `item_started{item:{type:"AgentMessage", id, content}}` | dropped (we emit deltas as they arrive) | |
| `item_completed{item:{type:"AgentMessage", id, content, phase}}` | `agent.message{role:"assistant", content: <translated>, phase}` | |
| `item_started{item:{type:"Reasoning", id}}` / `item_completed{item:{type:"Reasoning", ...}}` | dropped from primary stream | Encrypted reasoning is opaque; appears under `raw`. |
| `agent_message_content_delta{item_id, delta}` | `agent.delta{kind:"text", text:delta, item_id}` | |
| `agent_message_delta{delta}` | dropped (duplicate of `agent_message_content_delta`) | Both flavours arrive; we keep only the one with `item_id`. |
| `agent_message{message, phase}` | dropped (duplicate of `item_completed{AgentMessage}`) | |
| `user_message{message}` | dropped (duplicate of `item_completed{UserMessage}`) | |
| `token_count{info: null, rate_limits}` | `agent.notice{level:"info", category:"rate_limits", data: rate_limits}` | Mid-turn rate-limit ping. |
| `token_count{info:{total_token_usage, last_token_usage, model_context_window}, rate_limits}` | held; folded into the synthesised `agent.result.usage` (using `last_token_usage`) at turn end. | |
| `exec_command_begin` (and family) | `agent.tool_use{tool_use_id: msg.call_id, name:"shell" \| msg.tool, input: msg.command \| msg.params}` | *Not observed in the captured trace; mapping locked from Codex source. Re-trace with a tool-using prompt before Phase 3 implementation.* |
| `exec_command_end` (and family) | `agent.tool_result{tool_use_id, output, is_error}` | Same caveat. |
| `task_complete{turn_id, duration_ms, time_to_first_token_ms, last_agent_message}` | folded into the synthesised `agent.result` | |
| JSON-RPC `result{content, structuredContent:{threadId, content}}` | terminal `agent.result{subtype:"success", duration_ms, num_turns:1, turn_id, usage}` | Errors surface as `subtype:"error"` with the JSON-RPC error data on `agent.result.error`. |
| Cancelled turn (we sent `notifications/cancelled`) | `agent.result{subtype:"interrupted"}` | |

## Inbound: user turns

Inbound from client to daemon stays a single shape regardless of
backend:

```jsonc
{
  "type": "agent.user",
  "session_id": "<blemees session>",
  "message": {
    "role": "user",
    "content": "..." // or [content blocks]
  }
}
```

Per-backend translation:

* **Claude:** the daemon writes one stream-json line on `claude -p`
  stdin: `{"type":"user","message":<message>,"session_id":<CC native id>}`. `content` may be a string or an array of CC content blocks (text, image, document, …); the daemon does not validate the inner block types.
* **Codex:** the daemon issues a `tools/call` with `name:"codex"` (first turn) or `name:"codex-reply"` (subsequent turns) and `arguments:{prompt:<string>, threadId:<native id>?}`. Multimodal `content` arrays are flattened to a single string by concatenating text blocks; non-text blocks are rejected with `invalid_message` until Codex grows the inputs.

A future addition (`agent.user.attachments` or similar) can lift this
limitation when Codex exposes image/file inputs through MCP. For
`blemees/2`, text-only is the documented behaviour for the codex backend.

## What `raw` carries

When the client opens a session with `options.<backend>.include_raw_events: true`,
every `agent.*` frame produced from a native event includes the original
event under `raw`:

* **Claude:** the un-prefixed CC stream-json dict (e.g.
  `{"type":"stream_event","event":{...}}`).
* **Codex:** the contents of `msg` from the `notifications/codex/event`
  notification (e.g. `{"type":"agent_message_content_delta","item_id":"...","delta":"..."}`),
  plus a sibling `_meta` field copied from the notification when present.

Synthetic frames (`agent.system_init` assembled from multiple events,
the synthesised `agent.result`) carry `raw` as `null` or omit it.

## Drift policy

This document is the source of truth for both the translation layer and
the schema set under `blemees/schemas/`. When a backend grows a new
event type:

1. Capture a fresh trace under `docs/traces/`.
2. Add a row to the relevant translation table above.
3. Decide: drop, route to existing `agent.*` type, or extend the vocab.
   Extending the vocab is a `blemees/3` change.
4. Update schemas + tests in lockstep.

Backends that gain *fields* on an existing event type are non-breaking:
the translator is permissive on input, and `additionalProperties: true`
on output payload schemas means clients see the new field automatically.
