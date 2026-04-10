# Use the batch variant

`submit_batch_audit()` submits the audit as a batch job — 50% cheaper than real-time requests, processed within 24 hours. Use it when latency is not a concern.

## Run it

Uncomment the calls in the entry point:

```python
if __name__ == "__main__":
    batch_id = submit_batch_audit()
    print(f"Poll for results: retrieve_batch_results('{batch_id}')")
```

Call `retrieve_batch_results(batch_id)` later to fetch the report:

```python
from security_audit_advisor import submit_batch_audit, retrieve_batch_results

batch_id = submit_batch_audit()
# ... wait ...
retrieve_batch_results(batch_id)
```

## Why the batch design is different from the agentic loop

The batch API is **single-turn**. It submits one request and returns one response. There is no mechanism to send tool results back for a second turn.

Client-side tools (`list_files`, `read_file`, `search_code`) require exactly that round-trip: the executor calls the tool, your code runs it, you send back a `tool_result`, the executor continues. This multi-turn exchange cannot happen inside a batch request.

The advisor tool **is** a server-side tool. Opus runs inside the same request, needing no client round-trip. A batch request can include the advisor.

The solution: provide the codebase content **inline** in the user message, and include only the advisor tool — no client-side tools.

```
Agentic loop (run_audit):              Batch (submit_batch_audit):
  turn 1: executor calls list_files      single turn: codebase inline in message
  turn 2: return file list               advisor runs server-side
  turn 3: executor calls read_file       executor writes final report
  turn 4: return app.py content          ← no tool round-trips needed
  turn 5: executor calls advisor
  turn 6: (advisor runs server-side)
  turn 7: executor writes final report
```

## The batch request

```python
codebase_text = "\n\n".join(
    f"### {filename}\n```python\n{src}```"
    for filename, src in CODEBASE.items()
)

batch_tools = [ADVISOR_TOOL]   # advisor only — no client-side tools

batch = client.beta.messages.batches.create(
    requests=[
        {
            "custom_id": "security-audit-001",
            "params": {
                "model": "claude-haiku-4-5",
                "max_tokens": 4096,
                "system": "You are a security code auditor...",
                "tools": batch_tools,
                "messages": [
                    {
                        "role": "user",
                        "content": "Please audit the following codebase:\n\n" + codebase_text,
                    }
                ],
            },
        }
    ],
    betas=["advisor-tool-2026-03-01"],
)
```

Note that `betas=["advisor-tool-2026-03-01"]` is passed to `client.beta.messages.batches.create()`, not as `extra_headers`.

## Retrieve results

```python
def retrieve_batch_results(batch_id: str) -> None:
    client = anthropic.Anthropic()
    batch = client.beta.messages.batches.retrieve(
        batch_id,
        betas=["advisor-tool-2026-03-01"],
    )
    print(f"Batch {batch_id}: status={batch.processing_status}")

    if batch.processing_status != "ended":
        print("Not finished yet. Try again later.")
        return

    for result in client.beta.messages.batches.results(
        batch_id,
        betas=["advisor-tool-2026-03-01"],
    ):
        print(f"\n── custom_id={result.custom_id}  type={result.result.type}")
        if result.result.type == "succeeded":
            for block in result.result.message.content:
                if getattr(block, "type", None) == "text":
                    print(block.text)
```

`processing_status` transitions: `in_progress` → `ended`. Poll until `ended`. The batch API does not push notifications.

## Trade-offs

| | Agentic loop | Batch |
|---|---|---|
| Latency | Real-time (seconds to minutes) | Up to 24 hours |
| Price | Full rate | 50% discount |
| Client-side tools | Supported | Not supported |
| Advisor tool | Supported | Supported |
| Usage | `usage.iterations` per turn | `usage.iterations` per item |

## Limitations

The single-turn design means the executor has all the information it needs in the initial message. For large codebases, the inline content may approach context limits. The agentic loop variant handles this better because it fetches files on demand.

See also: [Agentic loop](../concepts/agentic-loop.md) · [Advisor tool reference](../reference/advisor-tool-reference.md)
