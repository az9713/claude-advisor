# Advisor tool reference

Complete field reference for the advisor tool.

---

## Tool definition fields

```python
{
    "type": "advisor_20260301",
    "name": "advisor",
    "model": "claude-opus-4-6",
    "max_uses": 3,
    "caching": {"type": "ephemeral", "ttl": "5m"},
}
```

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `type` | string | yes | — | Must be `"advisor_20260301"`. Versioned type identifier. |
| `name` | string | yes | — | Must be `"advisor"`. |
| `model` | string | yes | — | The advisor model ID. Must form a valid pair with the executor. See [Valid model pairs](#valid-model-pairs). |
| `max_uses` | integer | no | unlimited | Maximum advisor calls per API request. When reached, subsequent calls return `advisor_tool_result_error` with `error_code: "max_uses_exceeded"`. Per-request cap only; track conversation-level limits client-side. |
| `caching` | object | no | `null` (off) | Advisor-side prompt caching across calls in the same conversation. See [Caching](#caching). |

### `caching` object

| Field | Type | Values | Description |
|-------|------|--------|-------------|
| `type` | string | `"ephemeral"` | Cache type. Currently only `"ephemeral"`. |
| `ttl` | string | `"5m"`, `"1h"` | Cache time-to-live. |

`caching` is an on/off switch. It is not a `cache_control` breakpoint; the server decides where cache boundaries go within the advisor's transcript.

---

## Valid model pairs

The advisor must be at least as capable as the executor.

| Executor | Advisor |
|----------|---------|
| `claude-haiku-4-5` | `claude-opus-4-6` |
| `claude-sonnet-4-6` | `claude-opus-4-6` |
| `claude-opus-4-6` | `claude-opus-4-6` |

Any other combination returns `400 invalid_request_error` naming the unsupported pair.

---

## Beta activation

The advisor tool is a beta feature. Activate it via the Python SDK:

```python
response = client.beta.messages.create(
    ...
    betas=["advisor-tool-2026-03-01"],
)
```

Using `client.messages.create()` with `extra_headers={"anthropic-beta": "advisor-tool-2026-03-01"}` does **not** work. The `advisor_20260301` tool type will be rejected.

---

## Response content blocks

### `server_tool_use` — advisor call signal

| Field | Type | Description |
|-------|------|-------------|
| `type` | `"server_tool_use"` | Block type |
| `id` | string | Block ID, referenced by the following `advisor_tool_result` |
| `name` | `"advisor"` | Always `"advisor"` |
| `input` | `{}` | Always empty. The server constructs context from the full transcript. |

### `advisor_tool_result` — advisor response

| Field | Type | Description |
|-------|------|-------------|
| `type` | `"advisor_tool_result"` | Block type |
| `tool_use_id` | string | Matches the `id` of the preceding `server_tool_use` block |
| `content` | object | Discriminated union. See variants below. |

#### `advisor_result` variant

| Field | Type | Description |
|-------|------|-------------|
| `type` | `"advisor_result"` | Discriminator |
| `text` | string | The advisor's plaintext response |

#### `advisor_redacted_result` variant

| Field | Type | Description |
|-------|------|-------------|
| `type` | `"advisor_redacted_result"` | Discriminator |
| `encrypted_content` | opaque | Encrypted blob. Do not read or log. Pass through verbatim. The server decrypts it for the executor on the next turn. |

#### `advisor_tool_result_error` variant

| Field | Type | Description |
|-------|------|-------------|
| `type` | `"advisor_tool_result_error"` | Discriminator |
| `error_code` | string | See [error codes](#error-codes). |

---

## Error codes

| `error_code` | Cause | Effect |
|---|---|---|
| `max_uses_exceeded` | The request reached the `max_uses` cap on the tool definition | Executor continues without advice. Subsequent calls in the same request also return this error. |
| `too_many_requests` | Advisor sub-inference was rate-limited. Rate limits draw from the same per-model bucket as direct Opus calls. | Executor continues without advice. |
| `overloaded` | Advisor sub-inference hit capacity limits | Executor continues without advice. |
| `prompt_too_long` | The transcript exceeded the advisor model's context window | Executor continues without advice. |
| `execution_time_exceeded` | The advisor sub-inference timed out | Executor continues without advice. |
| `unavailable` | Any other advisor failure | Executor continues without advice. |

In all error cases, the overall request does not fail. Only the advisor sub-inference failed; the executor receives the error in its context and continues.

---

## `stop_reason` values

| `stop_reason` | Meaning | Action |
|---|---|---|
| `"end_turn"` | Executor finished normally | Print final output; exit loop |
| `"tool_use"` | Executor called one or more client-side tools | Execute tools; append results; loop |
| `"pause_turn"` | Dangling advisor call; request paused mid-turn | Do not append anything new; loop to resume |

`"pause_turn"` occurs when the advisor is running and the overall request times out or is interrupted. The advisor executes on resumption.

---

## `usage.iterations` format

```json
{
  "type": "message",
  "input_tokens": 412,
  "cache_read_input_tokens": 0,
  "cache_creation_input_tokens": 0,
  "output_tokens": 89
}
```

```json
{
  "type": "advisor_message",
  "model": "claude-opus-4-6",
  "input_tokens": 823,
  "cache_read_input_tokens": 0,
  "cache_creation_input_tokens": 0,
  "output_tokens": 1612
}
```

| Field | Present on | Description |
|-------|-----------|-------------|
| `type` | both | `"message"` (executor) or `"advisor_message"` (advisor) |
| `model` | `advisor_message` only | Advisor model ID |
| `input_tokens` | both | Input tokens for this iteration |
| `cache_read_input_tokens` | both | Cache-read tokens |
| `cache_creation_input_tokens` | both | Cache-write tokens |
| `output_tokens` | both | Output tokens for this iteration |

The top-level `usage.input_tokens` and `usage.output_tokens` reflect executor iterations only. Use `iterations` for per-model cost tracking.

---

## Cost control

### Per-request cap

Set `max_uses` on the tool definition. Once reached, the executor continues without further advice for that request.

### Conversation-level cap

There is no built-in conversation-level cap. Track advisor calls client-side. When you reach your ceiling:

1. Remove the advisor tool from your `tools` array.
2. Strip all `advisor_tool_result` blocks from your message history.

Omitting step 2 while the advisor is absent from `tools` causes:

```
400 invalid_request_error
```

---

## Platform availability

The advisor tool is available on the Anthropic API only. It is not available on Amazon Bedrock, Google Vertex AI, or Microsoft Foundry.

---

## Compatibility notes

| Feature | Status |
|---------|--------|
| Batch processing | Supported. `usage.iterations` reported per item. Advisor runs server-side, so batch requests can include the advisor without client-side tool round-trips. |
| Streaming | Supported. Advisor sub-inference does not stream; stream pauses then `advisor_tool_result` arrives as a single `content_block_start`. |
| Token counting | Returns executor first-iteration input tokens only. For a rough advisor estimate, call `count_tokens` with the advisor model and same messages separately. |
| `context_editing` / `clear_tool_uses` | Not yet fully compatible with advisor tool blocks. Full support planned. |
| `clear_thinking` with `keep != "all"` | Causes advisor-side cache misses by shifting the quoted transcript. Set `keep: "all"` to preserve advisor cache stability. |
| Zero Data Retention (ZDR) | Eligible. When your organisation has a ZDR arrangement, data is not stored after the API response. |

See also: [Advisor API shapes](../concepts/advisor-api-shapes.md) · [Usage and cost tracking](../concepts/usage-and-cost.md)
