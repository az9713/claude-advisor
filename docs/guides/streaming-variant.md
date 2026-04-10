# Use the streaming variant

`run_audit_streaming()` runs the same agentic audit as `run_audit()` but with streaming output — useful when you want executor text to appear in the terminal as it generates rather than in chunks at the end of each turn.

## Run it

Uncomment the call in the entry point:

```python
if __name__ == "__main__":
    run_audit_streaming()
```

Or call it directly:

```python
from security_audit_advisor import run_audit_streaming
run_audit_streaming()
```

## What changes from the non-streaming variant

### API call: `client.beta.messages.stream()`

```python
with client.beta.messages.stream(
    model="claude-haiku-4-5",
    max_tokens=4096,
    system=SYSTEM_PROMPT,
    tools=TOOLS,
    messages=messages,
    betas=["advisor-tool-2026-03-01"],   # ← same beta, same namespace
) as stream:
    for text in stream.text_stream:
        print(text, end="", flush=True)  # ← executor text streams live

    response = stream.get_final_message()  # ← complete response for loop logic
```

`client.beta.messages.stream()` is the streaming equivalent of `client.beta.messages.create()`. It uses the same beta namespace — `client.messages.stream()` does not work.

### The advisor does not stream

When the executor calls the advisor, the stream pauses. Opus runs server-side as a blocking sub-inference. Once it finishes, the complete `advisor_tool_result` arrives in a single `content_block_start` event (no deltas). The stream then resumes.

During the pause you may see SSE `ping` keepalives roughly every 30 seconds. Short advisor calls may produce no pings at all. This is normal.

```
[Streaming executor output …]
Examining app.py for injection patterns...
                                          ← stream pauses here (advisor running)
[Advisor called — result in next block]
[Advisor]: The SQL injection in login() is the critical finding ...
                                          ← stream resumes
Based on the advisor's guidance, here is the full report...
```

### Block processing after `get_final_message()`

`stream.text_stream` handles text blocks during streaming, but other block types are only accessible after the stream closes. Post-stream processing follows the same logic as the non-streaming variant:

```python
response = stream.get_final_message()

for block in response.content:
    btype = getattr(block, "type", None)

    if btype == "server_tool_use" and block.name == "advisor":
        print("[Advisor called — result in next block]")

    elif btype == "advisor_tool_result":
        content = block.content
        if getattr(content, "type", None) == "advisor_result":
            print(f"\n[Advisor]: {getattr(content, 'text', '')[:300]}")

    elif btype == "tool_use":
        result = execute_tool(block.name, block.input)
        tool_results.append({
            "type": "tool_result",
            "tool_use_id": block.id,
            "content": result,
        })
```

### `pause_turn` still applies

The streaming variant handles `pause_turn` identically:

```python
if response.stop_reason == "pause_turn":
    print("\n[pause_turn] Resuming …")
    continue
```

## What the streaming variant does not demonstrate

- Cost tracking — `UsageAccumulator` is not wired into `run_audit_streaming()`. Add it the same way as in `run_audit()` if you need cost data.
- Full `advisor_tool_result` variant handling — the streaming variant only handles `advisor_result`. Add the `advisor_redacted_result` and `advisor_tool_result_error` branches if needed.

See also: [Agentic loop](../concepts/agentic-loop.md) · [Advisor API shapes](../concepts/advisor-api-shapes.md)
