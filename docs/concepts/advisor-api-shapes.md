# Advisor API shapes

Every content block type involved in an advisor call, with exact JSON shapes and annotations pointing to the code.

---

## The tool definition

Declare the advisor in your `tools` array alongside any client-side tools:

```python
ADVISOR_TOOL = {
    "type": "advisor_20260301",   # versioned type, like code_execution
    "name": "advisor",            # must be exactly "advisor"
    "model": "claude-opus-4-6",   # the advisor model — billed at Opus rates
    "max_uses": 3,                # optional: cap per API request
    "caching": {                  # optional: advisor-side prompt caching
        "type": "ephemeral",
        "ttl": "5m",              # or "1h"
    },
}
```

`max_uses` is a per-request cap, not a per-conversation cap. See [Cost control](../reference/advisor-tool-reference.md#cost-control).

`caching` is an on/off switch for the advisor's own transcript cache. It is not a `cache_control` breakpoint — the server decides where cache boundaries go. Worth enabling when you expect three or more advisor calls per conversation.

---

## The beta API call

The advisor tool requires `client.beta.messages.create()` with `betas=["advisor-tool-2026-03-01"]`.

```python
response = client.beta.messages.create(   # ← beta namespace required
    model="claude-haiku-4-5",
    max_tokens=4096,
    system=SYSTEM_PROMPT,
    tools=TOOLS,
    messages=messages,
    betas=["advisor-tool-2026-03-01"],    # ← activates the advisor beta
)
```

> **Warning:** `client.messages.create()` with `extra_headers={"anthropic-beta": "..."}` does NOT activate the advisor tool. The API will reject the `advisor_20260301` tool type. Always use `client.beta.messages.create()`.

---

## Content blocks in a response

A single response can contain a mix of block types. A response that includes an advisor call looks like this:

```json
{
  "role": "assistant",
  "content": [
    {
      "type": "text",
      "text": "Let me examine app.py more closely before consulting the advisor."
    },
    {
      "type": "tool_use",
      "id": "toolu_01XYZ",
      "name": "read_file",
      "input": {"filename": "app.py"}
    },
    {
      "type": "server_tool_use",
      "id": "srvtoolu_abc123",
      "name": "advisor",
      "input": {}
    },
    {
      "type": "advisor_tool_result",
      "tool_use_id": "srvtoolu_abc123",
      "content": {
        "type": "advisor_result",
        "text": "The SQL injection in login() is the critical finding ..."
      }
    },
    {
      "type": "text",
      "text": "Based on the advisor's guidance, here is the security report..."
    }
  ]
}
```

### `text` block

Plain output from the executor. Detected with `btype == "text"`. Handle normally.

### `tool_use` block — client-side tool

The executor is requesting a client-side tool call. `btype == "tool_use"`. Your code must execute the tool and return a `tool_result`.

```python
elif btype == "tool_use":
    result = execute_tool(block.name, block.input)
    tool_results.append({
        "type": "tool_result",
        "tool_use_id": block.id,
        "content": result,
    })
```

### `server_tool_use` block — advisor call signal

The executor is signalling an advisor call. `btype == "server_tool_use"` and `block.name == "advisor"`.

```python
elif btype == "server_tool_use" and block.name == "advisor":
    # The API handles the Opus sub-inference server-side.
    # Your code does nothing here — no tool_result to append.
    # block.input is always {} — do not try to read a query from it.
    print(f"[Executor → Advisor] (id={block.id[:12]}…)")
```

**Critical distinctions from `tool_use`:**
- Type is `server_tool_use`, not `tool_use`
- `input` is always `{}` — the server constructs context automatically
- You do NOT add a `tool_result` for this block
- The API handles the Opus sub-inference and appends the result automatically

### `advisor_tool_result` block — advisor response

The Opus sub-inference has completed. `btype == "advisor_tool_result"`. The `content` field is a **single object** (not a list) with three possible types:

```python
elif btype == "advisor_tool_result":
    content = block.content
    content_type = getattr(content, "type", None)

    if content_type == "advisor_result":
        # Normal case — Opus returned plaintext advice
        adv_text = getattr(content, "text", "")
        print(adv_text)

    elif content_type == "advisor_redacted_result":
        # Encrypted blob — do not read or log encrypted_content.
        # Pass through verbatim in message history.
        # The server decrypts it on the next turn.
        pass

    elif content_type == "advisor_tool_result_error":
        # The advisor sub-inference failed.
        # The executor sees this and continues without advice.
        # The overall request does NOT fail.
        error_code = getattr(content, "error_code", "unknown")
        print(f"Advisor error: {error_code}")
```

> **Note:** `block.content` is a single object, not a list. Earlier versions of example code that iterate `for inner in block.content` are wrong.

#### `advisor_result`

| Field | Type | Description |
|-------|------|-------------|
| `type` | `"advisor_result"` | Discriminator |
| `text` | string | The advisor's response in plaintext |

#### `advisor_redacted_result`

| Field | Type | Description |
|-------|------|-------------|
| `type` | `"advisor_redacted_result"` | Discriminator |
| `encrypted_content` | opaque | Cannot be read by the client; server decrypts on next turn |

#### `advisor_tool_result_error`

| Field | Type | Description |
|-------|------|-------------|
| `type` | `"advisor_tool_result_error"` | Discriminator |
| `error_code` | string | See [error codes table](../reference/advisor-tool-reference.md#error-codes) |

---

## Multi-turn: what to pass back

Pass the full `response.content` — including `server_tool_use` and `advisor_tool_result` blocks — back on subsequent turns:

```python
# Correct: include the entire content list
messages.append({"role": "assistant", "content": response.content})
```

If you strip out `advisor_tool_result` blocks but leave the advisor tool in `tools`, the API returns:

```
400 invalid_request_error
```

To remove the advisor tool mid-conversation, you must **both** remove it from `tools` **and** strip all `advisor_tool_result` blocks from the message history.

---

## Block type decision tree

```
for block in response.content:
    btype = block.type

    "text"                     → executor text output; display it
    "tool_use"                 → client-side tool; execute and return tool_result
    "server_tool_use"
      block.name == "advisor"  → advisor call; do nothing; API handled it
    "advisor_tool_result"
      content.type ==
        "advisor_result"       → read content.text
        "advisor_redacted_result" → pass through; do not read
        "advisor_tool_result_error" → log error_code; executor continues
```

See also: [Agentic loop](agentic-loop.md) · [Advisor tool reference](../reference/advisor-tool-reference.md)
