# Code walkthrough: security_audit_advisor.py

Line-by-line tour of every section of the file, plus a cost estimate for a typical `run_audit()` run.

---

## File map

```
Lines   1–35    Module docstring — what the file demonstrates
Lines  37–44    Imports
Lines  50–108   CODEBASE — the intentionally vulnerable target
Lines 115–149   Tool implementations — list_files, read_file, search_code, execute_tool
Lines 157–219   Tool definitions — ADVISOR_TOOL, TOOLS array
Lines 227–313   UsageAccumulator — token and cost tracking
Lines 320–336   SYSTEM_PROMPT
Lines 339–341   _fmt helper
Lines 344–492   run_audit() — the main agentic loop
Lines 499–571   run_audit_streaming() — streaming variant
Lines 578–635   submit_batch_audit() — batch variant
Lines 638–654   retrieve_batch_results()
Lines 660–670   Entry point
```

---

## Lines 1–35 — Module docstring

The docstring is functional documentation, not just a description. It lists every API feature demonstrated (13 items) and the valid executor/advisor model pairs. If you add a new feature to the file, it belongs here.

The "valid pairs" table matters: the API enforces it. An invalid pair (e.g. Haiku + Haiku) returns `400 invalid_request_error`.

---

## Lines 37–44 — Imports

```python
from __future__ import annotations   # line 37

import json          # line 39  — serialise tool inputs for display
import textwrap      # line 40  — dedent CODEBASE strings, wrap console output
from dataclasses import dataclass, field   # line 41
from typing import Any                     # line 42

import anthropic     # line 44
```

`from __future__ import annotations` defers annotation evaluation — it lets you write `dict[str, str]` (lowercase) without `from __future__` only being available on Python 3.10+. This keeps the file compatible with Python 3.9.

`json` is only used to format tool input arguments for the console (`json.dumps(block.input)[:80]`). It is not used to construct API payloads.

---

## Lines 50–108 — `CODEBASE`

```python
CODEBASE: dict[str, str] = {
    "app.py":    textwrap.dedent("""\..."""),   # lines 51–75
    "config.py": textwrap.dedent("""\..."""),   # lines 76–86
    "auth.py":   textwrap.dedent("""\..."""),   # lines 87–100
}
```

`CODEBASE` is a stand-in for a real filesystem. It holds three files with deliberate vulnerabilities:

| File | Vulnerability |
|------|--------------|
| `app.py` | SQL injection in `login()` (f-string query, no parameterisation) |
| `app.py` | Shell injection in `run_report()` (`shell=True` with user input) |
| `app.py` | Unsafe deserialisation in `load_user_settings()` (`pickle.loads`) |
| `app.py` | Weak hashing in `hash_password()` (MD5) |
| `app.py` | Hardcoded credentials (`SECRET_KEY`, `DATABASE_URL`) |
| `config.py` | Debug mode on, wildcard hosts, insecure cookies |
| `auth.py` | Hardcoded JWT secret, signature verification disabled |

`textwrap.dedent` strips the leading indentation so the Python source looks correctly indented when the model reads it — without it, every line would have 8 spaces of padding from the heredoc.

The client-side tools (`read_file`, `search_code`) work directly against this dict rather than the filesystem. That makes the example self-contained: no real files needed, no path issues on any OS.

---

## Lines 115–149 — Tool implementations

### `_tool_list_files()` (line 115)

```python
def _tool_list_files() -> str:
    lines = [f"  {name}  ({len(src)} chars)" for name, src in CODEBASE.items()]
    return "Files in codebase:\n" + "\n".join(lines)
```

Returns a directory listing with character counts. The character count gives the executor a sense of file size before it decides which files to read first.

### `_tool_read_file(filename)` (line 120)

```python
def _tool_read_file(filename: str) -> str:
    if filename not in CODEBASE:
        available = ", ".join(CODEBASE)
        return f"File '{filename}' not found. Available: {available}"
    return CODEBASE[filename]
```

Returns raw file content. The error message includes available filenames — this prevents the executor from looping on a hallucinated filename.

### `_tool_search_code(pattern)` (line 127)

```python
def _tool_search_code(pattern: str) -> str:
    hits: list[str] = []
    for filename, src in CODEBASE.items():
        for lineno, line in enumerate(src.splitlines(), 1):
            if pattern.lower() in line.lower():
                hits.append(f"{filename}:{lineno}: {line.rstrip()}")
    return "\n".join(hits) if hits else f"No matches for '{pattern}'"
```

Case-insensitive substring search across all files. Returns matches in `filename:lineno: content` format — the same format most editors and tools use, so the model can reference exact locations in its report.

### `execute_tool(name, tool_input)` (line 136)

```python
def execute_tool(name: str, tool_input: dict[str, Any]) -> str:
    dispatch = {
        "list_files":  lambda: _tool_list_files(),
        "read_file":   lambda: _tool_read_file(**tool_input),
        "search_code": lambda: _tool_search_code(**tool_input),
    }
    fn = dispatch.get(name)
    if fn is None:
        return f"Unknown tool: {name}"
    try:
        return fn()
    except Exception as exc:
        return f"Tool error: {exc}"
```

Central dispatcher for all client-side tool calls. The pattern:

- `**tool_input` unpacks the API's `input` dict directly into the function's keyword arguments. This works as long as the JSON schema field names match the Python parameter names.
- Returns a string in all cases — including errors. Returning `"Tool error: ..."` instead of raising means the executor sees what went wrong and can adapt, rather than crashing the loop.
- Called only for `tool_use` blocks (client-side). It is never called for `server_tool_use` (advisor) blocks.

---

## Lines 157–219 — Tool definitions

### `ADVISOR_TOOL` (lines 157–180)

```python
ADVISOR_TOOL: dict[str, Any] = {
    "type": "advisor_20260301",      # line 158  versioned type
    "name": "advisor",               # line 159  must be exactly "advisor"
    "model": "claude-opus-4-6",      # line 160  advisor model
    "max_uses": 3,                   # line 161  per-request cap
    "caching": {                     # lines 162–164
        "type": "ephemeral",
        "ttl": "5m",
    },
}
```

Four design decisions baked into this dict:

**`type: "advisor_20260301"`** — Versioned, like `computer_use_20250124` or `code_execution_20250522`. If Anthropic revises the advisor protocol, they'll issue a new version string. Pinning the version prevents surprise breakage.

**`max_uses: 3`** — Matches the system prompt's "Use it at most for the 3 most critical decisions." The API enforces the cap; the system prompt guides the model to use calls strategically. Using both creates defence-in-depth: the model tries to spend calls wisely, and if it doesn't, the API stops it.

**`caching: {"type": "ephemeral", "ttl": "5m"}`** — Enables advisor-side prompt caching. The advisor's transcript on the Nth call is the (N-1)th call's transcript with one segment appended — a stable prefix. With caching on, calls 2 and 3 each read from cache rather than re-paying full input token cost. This is worthwhile here because `max_uses: 3` means we expect up to three advisor calls per request; caching breaks even at roughly three calls.

**Why `ADVISOR_TOOL` is a separate variable** — It is reused in `submit_batch_audit()` where only the advisor tool (not client-side tools) is included in the batch request. Extracting it avoids duplication.

### `TOOLS` array (lines 165–219)

```python
TOOLS: list[dict[str, Any]] = [
    # list_files definition   — lines 167–171
    # read_file definition    — lines 172–185
    # search_code definition  — lines 186–199
    ADVISOR_TOOL,              # line 201
]
```

The array mixes two tool categories:

| Tool | Category | Who executes |
|------|----------|-------------|
| `list_files` | Client-side | Your Python code (`execute_tool`) |
| `read_file` | Client-side | Your Python code (`execute_tool`) |
| `search_code` | Client-side | Your Python code (`execute_tool`) |
| `advisor` | Server-side | Anthropic infrastructure |

The API can distinguish them because client-side tools have `input_schema` (JSON Schema) and produce `tool_use` blocks, while the advisor tool has `type: "advisor_20260301"` and produces `server_tool_use` blocks. Your loop must handle them differently.

---

## Lines 227–313 — `UsageAccumulator`

```python
@dataclass
class UsageAccumulator:
    executor_model: str
    advisor_model: str
    _exec_in:   int = field(default=0, repr=False)   # line 238
    _exec_out:  int = field(default=0, repr=False)   # line 239
    _adv_in:    int = field(default=0, repr=False)   # line 240
    _adv_out:   int = field(default=0, repr=False)   # line 241
    _adv_calls: int = field(default=0, repr=False)   # line 242
```

A `@dataclass` with private accumulator fields. `repr=False` keeps the auto-generated `__repr__` readable (it won't print `_exec_in=4270, _exec_out=...` in debug output).

### `ingest_response()` (lines 244–282)

Called once per API response, inside the agentic loop.

```python
for it in iterations:
    it_type = getattr(it, "type", None) or (it.get("type") if isinstance(it, dict) else None)
```

The dual `getattr`/`dict.get` pattern handles two SDK states: newer SDK versions return typed objects with `.type` attributes; older versions may return raw dicts. Both are handled without branching on SDK version.

**The critical line (272):**

```python
if it_type == "advisor_message":
```

Not `if it.model == self.advisor_model`. The `model` field is absent on `type: "message"` iterations — using `model` as the discriminator silently mis-attributes tokens when the executor and advisor are the same model (`claude-opus-4-6` + `claude-opus-4-6`). `type` is always present and is the correct discriminator.

**Fallback (lines 279–282):**

```python
elif usage:
    self._exec_in  += getattr(usage, "input_tokens",  0)
    self._exec_out += getattr(usage, "output_tokens", 0)
```

If the SDK doesn't expose `iterations` yet, attribute all tokens to the executor. The cost report will still print; it just won't split by model.

### `print_report()` (lines 288–313)

```python
opus_in_t  = self._exec_in  + self._adv_in
opus_out_t = self._exec_out + self._adv_out
opus_only  = self._cost(self.advisor_model, opus_in_t, opus_out_t)
savings_pct = (opus_only - total) / opus_only * 100
```

The "Opus-only estimate" sums all tokens at Opus rates. This answers "what would this have cost if Opus had run the entire conversation?" It is an approximation — an Opus-only agent would likely make fewer, more confident tool calls, producing a different token count. Treat it as a directional upper bound.

---

## Lines 320–336 — `SYSTEM_PROMPT`

```python
SYSTEM_PROMPT = textwrap.dedent("""\
    You are a security code auditor. Your workflow:

    1. Call list_files to discover the codebase.
    2. Call read_file on each file and search_code for known vulnerability patterns
       (SQL injection, shell injection, pickle, hardcoded secrets, weak crypto, etc.).
    3. When you need expert judgment — prioritising findings, confirming a true
       vulnerability, or crafting remediation advice — call the advisor tool.
       The advisor is Claude Opus and has your full conversation history.
       Use it at most for the 3 most critical decisions.
    4. Produce a final Markdown security report with: ...
""")
```

Three decisions in this prompt:

**Numbered steps** — Give the executor a deterministic workflow so it doesn't jump straight to the report without gathering evidence. Without step 2, Haiku might attempt to write the report from memory.

**"The advisor is Claude Opus and has your full conversation history"** — Telling the executor what the advisor is makes it use the advisor more appropriately. Without this, the executor may treat the advisor like a generic tool and call it for trivial questions.

**"Use it at most for the 3 most critical decisions"** — Paired with `max_uses: 3` on the tool definition. The system prompt shapes the *when*; `max_uses` enforces the *how many*. Together they produce 2–3 strategically-timed advisor calls rather than a call every turn.

---

## Lines 344–492 — `run_audit()` in detail

This is the core of the file. Every line serves a purpose.

### Setup (lines 352–367)

```python
client = anthropic.Anthropic()          # line 352
usage  = UsageAccumulator(              # lines 353–356
    executor_model="claude-haiku-4-5",
    advisor_model="claude-opus-4-6",
)

messages: list[dict[str, Any]] = [      # lines 358–367
    {
        "role": "user",
        "content": (
            "Please perform a thorough security audit of this codebase. "
            "Consult the advisor when you need expert judgment on severity "
            "or remediation guidance."
        ),
    }
]
```

`messages` starts with just the user's request. The system prompt is passed separately on every API call (not prepended to `messages`).

### The API call (lines 382–389)

```python
response = client.beta.messages.create(   # ← beta namespace
    model="claude-haiku-4-5",
    max_tokens=4096,
    system=SYSTEM_PROMPT,
    tools=TOOLS,
    messages=messages,
    betas=["advisor-tool-2026-03-01"],    # ← activates the beta
)
```

`max_tokens=4096` bounds the executor's output. It does **not** bound advisor output — the advisor's tokens are a separate sub-inference with their own limits. The advisor typically produces 1,400–1,800 tokens (400–700 text plus thinking) regardless of the executor's `max_tokens`.

### Content block dispatch (lines 396–452)

```python
for block in response.content:
    btype = getattr(block, "type", None)
```

`getattr(block, "type", None)` rather than `block.type` because beta SDK objects may have slightly different attribute presence depending on block type. `getattr` with a default is defensive.

**`btype == "text"` (lines 401–405)**

Executor text — display it. The `[:300]` preview + `…` prevents the console from flooding with the full final report mid-loop.

**`btype == "server_tool_use" and block.name == "advisor"` (lines 412–415)**

```python
elif btype == "server_tool_use" and block.name == "advisor":
    print(f"\n[Executor → Advisor] (id={block.id[:12]}…)")
    print("  (Opus is reviewing the full conversation history …)")
    # block.input is always {} — do not attempt to read a query from it
```

Nothing to do here except log it. The API already ran Opus and appended the result in the same response. `block.input` is always `{}` — the comment is a warning against a natural mistake (trying to extract a "query" from the input, as you would with a normal tool call).

**`btype == "tool_use"` (lines 418–429)**

```python
elif btype == "tool_use":
    result = execute_tool(block.name, block.input)
    tool_results.append({
        "type": "tool_result",
        "tool_use_id": block.id,
        "content": result,
    })
```

Client-side tool: run it, collect the result. Results are accumulated in `tool_results` rather than appended immediately — the assistant turn must be appended first (line 459), then tool results go in as a user turn (lines 471–474).

**`btype == "advisor_tool_result"` (lines 436–452)**

```python
elif btype == "advisor_tool_result":
    content = block.content          # single object, NOT a list
    content_type = getattr(content, "type", None)

    if content_type == "advisor_result":
        adv_text = getattr(content, "text", "")
        print(f"\n[Advisor → Executor] (id={block.tool_use_id[:12]}…)")
        print(_fmt(adv_text[:500]))

    elif content_type == "advisor_redacted_result":
        print(f"\n[Advisor → Executor] result redacted (id=...)")

    elif content_type == "advisor_tool_result_error":
        error_code = getattr(content, "error_code", "unknown")
        print(f"\n[Advisor error] error_code={error_code!r} (id=...)")
```

`block.content` is a **single object** — not a list. This is the most common source of bugs when adapting this code. The `advisor_redacted_result` branch does nothing because the content is an opaque encrypted blob — it is passed through in `messages.append(response.content)` and the server handles decryption on the next turn.

### History append (line 459)

```python
messages.append({"role": "assistant", "content": response.content})
```

The **full** `response.content` — including `server_tool_use` and `advisor_tool_result` blocks. Stripping those blocks while the advisor is still in `tools` causes a `400 invalid_request_error` on the next request.

### Loop control (lines 462–490)

```python
if response.stop_reason == "end_turn":
    # print report and break

if response.stop_reason == "tool_use":
    if tool_results:
        messages.append({"role": "user", "content": tool_results})
    continue

if response.stop_reason == "pause_turn":
    print(f"\n[pause_turn] Resuming after advisor sub-inference …")
    continue

print(f"\n[Warning] Unexpected stop_reason={response.stop_reason!r}. Stopping.")
break
```

The `tool_use` branch only appends tool results if there are any. If the response contained only a `server_tool_use` advisor call (no client-side tool calls), `tool_results` is empty and the loop continues without appending a user turn — the executor resumes with the advisor result already in the assistant message.

`pause_turn` means the request was interrupted mid-turn while the advisor was running. Looping without appending anything resumes the advisor on the next call.

---

## Lines 499–571 — `run_audit_streaming()`

The streaming variant differs from `run_audit()` in three ways:

**1. `client.beta.messages.stream()` instead of `client.beta.messages.create()`** (line 526)

Same beta namespace, same `betas=` parameter. The stream yields executor text tokens live via `stream.text_stream`. `stream.get_final_message()` blocks until the full response is available, including any advisor results.

**2. Advisor calls pause the stream** (documented in line 503–510)

When the executor calls the advisor, `stream.text_stream` goes quiet. The advisor runs as a blocking sub-inference. When it finishes, `advisor_tool_result` arrives as a single `content_block_start` event (no deltas). The stream then resumes.

**3. Post-stream block processing** (lines 540–558)

`stream.text_stream` only surfaces text. All other block types — `server_tool_use`, `advisor_tool_result`, `tool_use` — are accessed after the stream closes via `response.content`. The post-stream processing logic is identical to `run_audit()`.

**What the streaming variant omits:**

- `UsageAccumulator` — not wired in. Add `usage.ingest_response(response)` inside the loop if you need cost tracking.
- Full `advisor_tool_result` variant handling — only `advisor_result` is handled. Add `advisor_redacted_result` and `advisor_tool_result_error` branches if needed.

---

## Lines 578–654 — Batch functions

### `submit_batch_audit()` (lines 578–635)

The batch API is single-turn: one request, one response, no mechanism to send tool results back. Client-side tools require exactly that round-trip, so they cannot be used.

The design change: provide the codebase content **inline** in the user message, and include only `ADVISOR_TOOL` (no client-side tools).

```python
codebase_text = "\n\n".join(             # lines 598–601
    f"### {filename}\n```python\n{src}```"
    for filename, src in CODEBASE.items()
)

batch_tools = [ADVISOR_TOOL]             # line 604 — advisor only
```

The batch request itself:

```python
batch = client.beta.messages.batches.create(   # line 606
    requests=[{
        "custom_id": "security-audit-001",     # unique ID for result retrieval
        "params": {
            "model": "claude-haiku-4-5",
            "max_tokens": 4096,
            "tools": batch_tools,
            "messages": [{"role": "user", "content": "... " + codebase_text}],
        },
    }],
    betas=["advisor-tool-2026-03-01"],         # same beta activation
)
```

`custom_id` is your label for the request. It comes back in the results so you can match batch items to their outputs.

### `retrieve_batch_results()` (lines 638–654)

```python
batch = client.beta.messages.batches.retrieve(batch_id, betas=["advisor-tool-2026-03-01"])

if batch.processing_status != "ended":
    print("Not finished yet. Try again later.")
    return

for result in client.beta.messages.batches.results(batch_id, betas=["advisor-tool-2026-03-01"]):
    if result.result.type == "succeeded":
        for block in result.result.message.content:
            if getattr(block, "type", None) == "text":
                print(block.text)
```

`processing_status` moves from `in_progress` to `ended`. There's no webhook — you poll. `client.beta.messages.batches.results()` streams the results as an iterator; for large batches this avoids loading all results into memory at once.

---

## Lines 660–670 — Entry point

```python
if __name__ == "__main__":
    run_audit()

    # run_audit_streaming()
    # batch_id = submit_batch_audit()
```

Only `run_audit()` is active by default. The others are commented out to avoid accidentally incurring costs from all three on a single run.

---

See also: [Usage and cost tracking](concepts/usage-and-cost.md#cost-estimate-for-run_audit-on-this-codebase) — includes the full cost estimate for `run_audit()` with this specific codebase, sensitivity table, and scaling notes.
