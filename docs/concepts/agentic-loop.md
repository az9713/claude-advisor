# Agentic loop

How `run_audit()` drives a multi-turn conversation with the advisor pattern.

---

## Overview

An agentic loop sends repeated API requests, each time appending the previous response and any tool results to the growing message history. The loop terminates when `stop_reason == "end_turn"`.

With the advisor tool, the loop has four possible `stop_reason` values to handle:

| `stop_reason` | Meaning | Action |
|---|---|---|
| `"end_turn"` | Executor is done | Print the final report and exit |
| `"tool_use"` | Executor called one or more client-side tools | Execute tools, append results, loop |
| `"pause_turn"` | Dangling advisor call — request paused mid-turn | Append nothing new; loop to resume |
| anything else | Unexpected | Log and exit |

---

## Step-by-step walkthrough

### Step 1 — Initial request

The user message asks for a security audit. The executor (Haiku) receives the system prompt and the initial message.

```python
response = client.beta.messages.create(
    model="claude-haiku-4-5",
    max_tokens=4096,
    system=SYSTEM_PROMPT,
    tools=TOOLS,            # includes ADVISOR_TOOL
    messages=messages,
    betas=["advisor-tool-2026-03-01"],
)
```

Haiku calls `list_files`. `stop_reason == "tool_use"`.

### Step 2 — Execute client-side tools

For each `tool_use` block in `response.content`:

```python
elif btype == "tool_use":
    result = execute_tool(block.name, block.input)
    tool_results.append({
        "type": "tool_result",
        "tool_use_id": block.id,
        "content": result,
    })
```

Append the assistant turn and the tool results to history:

```python
messages.append({"role": "assistant", "content": response.content})
messages.append({"role": "user", "content": tool_results})
```

### Step 3 — Advisor is called

Several turns in, Haiku has read all three files and found multiple vulnerabilities. Before drafting the final report, it calls the advisor.

The response content contains:

```
server_tool_use  (name="advisor", input={})
advisor_tool_result  (content.type="advisor_result", content.text="...")
```

The `server_tool_use` block signals the advisor call. The `advisor_tool_result` block contains Opus's guidance. Your code logs the advice but **does not append any `tool_result`** — the API injects the result automatically.

```python
elif btype == "server_tool_use" and block.name == "advisor":
    print("Opus is reviewing the transcript …")
    # No tool_result needed.

elif btype == "advisor_tool_result":
    if content.type == "advisor_result":
        print(content.text)
    # Handled; will be included in messages.append(response.content) below.
```

`stop_reason` may be `"tool_use"` (if there were also client-side calls in the same turn) or `"end_turn"` (if the advisor call was the last thing before the final answer). Either way, the full content goes into history:

```python
messages.append({"role": "assistant", "content": response.content})
```

### Step 4 — `pause_turn`

If a network interruption or timeout occurs while the advisor sub-inference is running, the API returns `stop_reason: "pause_turn"` with the `server_tool_use` block as the last content block. The advisor has not yet completed.

Resume by calling the API again with the current history unchanged:

```python
if response.stop_reason == "pause_turn":
    # The advisor will run on this next call.
    # Do not append anything new to messages.
    print("[pause_turn] Resuming …")
    continue
```

### Step 5 — `end_turn`

Haiku produces the final Markdown report. `stop_reason == "end_turn"`. Print the report and break.

```python
if response.stop_reason == "end_turn":
    for block in response.content:
        if getattr(block, "type", None) == "text":
            print(block.text)
    break
```

---

## Message history growth

After a full audit (3 tool calls + 1 advisor call + final report), the message history looks like:

```
messages = [
  {role: "user",      content: "Please perform a security audit..."},
  {role: "assistant", content: [tool_use(list_files)]},
  {role: "user",      content: [tool_result(list_files → "Files: app.py...")]},
  {role: "assistant", content: [tool_use(read_file, app.py)]},
  {role: "user",      content: [tool_result(read_file → source code)]},
  {role: "assistant", content: [tool_use(read_file, auth.py), tool_use(read_file, config.py)]},
  {role: "user",      content: [tool_result(auth.py), tool_result(config.py)]},
  {role: "assistant", content: [
    text("I've reviewed all files. Consulting advisor..."),
    server_tool_use(advisor, input={}),
    advisor_tool_result(advisor_result, text="SQL injection is critical..."),
    text("## Security Audit Report\n...")
  ]},
]
```

The `advisor_tool_result` block lives in the assistant message alongside the surrounding text. It must stay there on subsequent turns.

---

## System prompt guidance

The system prompt in the example instructs Haiku to use the advisor for specific decision types:

```python
SYSTEM_PROMPT = """
...
3. When you need expert judgment — prioritising findings, confirming a true
   vulnerability, or crafting remediation advice — call the advisor tool.
   The advisor is Claude Opus and has your full conversation history.
   Use it at most for the 3 most critical decisions.
...
"""
```

The `max_uses: 3` field on the tool definition enforces the cap. Beyond 3 calls per request, the API returns `advisor_tool_result_error` with `error_code: "max_uses_exceeded"` and Haiku continues without further advice.

See also: [Advisor API shapes](advisor-api-shapes.md) · [Usage and cost tracking](usage-and-cost.md)
