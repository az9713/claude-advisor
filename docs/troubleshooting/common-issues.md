# Common issues

---

## `400 invalid_request_error` on startup

**Cause:** Using `client.messages.create()` instead of `client.beta.messages.create()`.

**Fix:** Change the API call:

```python
# Wrong
response = client.messages.create(
    ...
    extra_headers={"anthropic-beta": "advisor-tool-2026-03-01"},
)

# Correct
response = client.beta.messages.create(
    ...
    betas=["advisor-tool-2026-03-01"],
)
```

The `advisor_20260301` tool type is only accepted through the beta endpoint. `extra_headers` on the standard endpoint does not activate the beta.

---

## `400 invalid_request_error` on the second turn

**Cause:** The message history contains `advisor_tool_result` blocks but the advisor tool is absent from `tools` on this turn.

**Fix:** Either keep the advisor tool in `tools`, or remove it and also strip all `advisor_tool_result` blocks from the message history:

```python
# When removing the advisor:
tools_without_advisor = [t for t in TOOLS if t.get("name") != "advisor"]

messages_without_advisor_results = [
    msg for msg in messages
    if not (
        msg["role"] == "assistant"
        and any(
            getattr(b, "type", None) == "advisor_tool_result"
            for b in (msg["content"] if isinstance(msg["content"], list) else [])
        )
    )
]
```

---

## The advisor is never called

**Cause 1:** The system prompt doesn't tell the executor when to use the advisor.

**Fix:** Add explicit guidance to the system prompt:

```
When you need expert judgment — prioritising findings, confirming a vulnerability,
or crafting remediation advice — call the advisor tool.
```

**Cause 2:** `max_uses` was set to `0` or the cap was already reached in a previous request.

**Fix:** Check `max_uses` value and how many advisor calls occurred earlier in the conversation.

---

## `usage.iterations` is missing or empty

**Cause:** The SDK version you're using doesn't yet expose `usage.iterations`.

**Fix:** `UsageAccumulator` already handles this with a fallback:

```python
if iterations:
    # parse per-model
elif usage:
    # fallback: attribute all to executor
```

Upgrade the SDK to get per-model breakdowns: `pip install --upgrade anthropic`.

---

## Advisor tokens show up as zero in the cost report

**Cause:** `ingest_response()` is checking `iteration.model` instead of `iteration.type` to identify advisor iterations.

**Fix:** The correct discriminator is `type`, not `model`:

```python
# Wrong
if model == self.advisor_model:

# Correct
if it_type == "advisor_message":
```

`model` is absent on `type: "message"` iterations. `type` is always present.

---

## Stream hangs with no output

**Cause:** The advisor sub-inference is running. This is expected — the advisor does not stream. The executor stream pauses while Opus runs.

**What to expect:** SSE `ping` keepalives every ~30 seconds during long advisor calls. Short advisor calls may produce no pings. The stream resumes with a single `content_block_start` event containing the complete `advisor_tool_result`.

---

## Batch audit returns `tool_use` stop reason with no results

**Cause:** Client-side tools (`list_files`, `read_file`, `search_code`) were included in the batch request. The batch API is single-turn and cannot handle client round-trips.

**Fix:** Use `submit_batch_audit()` as written — it provides codebase content inline and uses only the server-side advisor tool. Do not add `list_files`, `read_file`, or `search_code` to the batch tools list.

---

## Advisor cache misses on every call despite `caching` enabled

**Cause 1:** `clear_thinking` is set with `keep` other than `"all"`, shifting the transcript each turn.

**Fix:** Set `keep: "all"` in your `clear_thinking` config to preserve prefix stability.

**Cause 2:** The `caching` field was toggled on or off mid-conversation.

**Fix:** Set `caching` once and leave it unchanged for the full conversation.

**Cause 3:** The conversation has fewer than three advisor calls, so no cache write has occurred yet.

**What to expect:** `cache_read_input_tokens` on `advisor_message` iterations will be 0 until the second call. This is normal.

---

## `error_code: "too_many_requests"` in `advisor_tool_result`

**Cause:** The advisor sub-inference hit the Opus rate limit. Advisor calls draw from the same per-model bucket as direct Opus API calls.

**Fix:** Reduce advisor call frequency or increase your Opus rate limit tier. The executor continues without advice — the overall request does not fail.

See also: [Advisor tool reference](../reference/advisor-tool-reference.md) · [Advisor API shapes](../concepts/advisor-api-shapes.md)
