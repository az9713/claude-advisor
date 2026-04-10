# What is the advisor tool?

The advisor tool is an Anthropic API feature that pairs a fast, cheap **executor model** with a high-intelligence **advisor model** that steps in at key moments to provide strategic guidance — all within a single API request.

## The problem it solves

Long-horizon agentic tasks (code audits, multi-step research, computer use) have two phases that require different things:

- **Mechanical execution**: reading files, running searches, executing tools. Fast models do this well.
- **Strategic judgment**: prioritising findings, choosing an approach, catching a subtle vulnerability. This is where Opus earns its cost.

Running Opus for the entire task pays Opus rates for mechanical work that Haiku could handle. Running Haiku alone sacrifices quality on the decisions that matter.

The advisor tool splits the two: Haiku runs the loop, Opus consults when judgment is needed.

## How it works

You declare the advisor as a tool in your `tools` array. The executor model (Haiku or Sonnet) runs your agentic loop normally. When it faces a decision beyond its capability, it calls the advisor tool — just as it would call any other tool.

When that call fires:

1. The executor emits a `server_tool_use` block with `name: "advisor"` and an empty `input: {}`.
2. Anthropic runs a separate Opus inference **server-side**, passing the executor's complete transcript: system prompt, all tool definitions, all prior turns, all tool results.
3. Opus returns a plan or course-correction (typically 400–700 text tokens).
4. That advice arrives in the executor's context as an `advisor_tool_result` block.
5. The executor continues, informed by the advice.

All of this happens inside **one `/v1/messages` request**. No extra round-trips on your side.

```
Your code                       Anthropic API
────────────────                ─────────────────────────────────
client.beta                     ┌─ executor inference (Haiku) ──────┐
  .messages.create()  ───────►  │  reads files, searches code...    │
                                │  calls advisor tool               │
                                │                                    │
                                │     ┌─ advisor inference (Opus) ─┐ │
                                │     │  sees full transcript      │ │
                                │     │  returns plan/correction   │ │
                                │     └───────────────────────────┘ │
                                │  continues with advice in context  │
                                └───────────────────────────────────┘
◄──────────────────  single response with full content[]
```

## What the example demonstrates

`security_audit_advisor.py` puts this pattern through its paces on a deliberately vulnerable Python codebase. Haiku discovers files, reads source, and searches for vulnerability patterns using client-side tools. When it needs expert judgment — confirming a severity, prioritising findings, crafting remediation — it calls the advisor. Opus reviews the full audit transcript and guides Haiku's final report.

The example covers every part of the API surface:

| What | Where in the code |
|------|-------------------|
| Advisor tool definition | `ADVISOR_TOOL` dict, line 174 |
| Correct beta API call | `client.beta.messages.create()`, line 382 |
| `server_tool_use` block detection | `btype == "server_tool_use"`, line 412 |
| `advisor_tool_result` variants | Three-way content dispatch, lines 436–452 |
| `usage.iterations` parsing | `UsageAccumulator.ingest_response()`, lines 244–282 |
| `pause_turn` handling | Line 479 |
| Multi-turn history | `messages.append(response.content)`, line 459 |
| Streaming | `run_audit_streaming()`, line 499 |
| Batch (advisor-only) | `submit_batch_audit()`, line 578 |

## Measured results

From Anthropic's benchmarks (April 2026):

| Configuration | BrowseComp | Cost vs baseline |
|---|---|---|
| Haiku alone | 19.7% | baseline |
| Haiku + Opus advisor | 41.2% | higher than Haiku alone, but 85% cheaper than Sonnet alone |
| Sonnet alone | ~70% | baseline |
| Sonnet + Opus advisor | +2.7pp over Sonnet | −11.9% per agentic task |

Results are task-dependent. Evaluate on your own workload before committing.

## What it is not

The advisor tool is not suitable for:

- **Single-turn Q&A** — there is no agentic loop to inject advice into.
- **Every turn genuinely requires Opus** — if all decisions are hard, use Opus as the executor directly. The `claude-opus-4-6 + claude-opus-4-6` pair is valid but provides no cost savings.
- **Third-party providers** (Bedrock, Vertex, Foundry) — the advisor tool is available on the Anthropic API only.

See also: [Key concepts](key-concepts.md) · [Quickstart](../getting-started/quickstart.md)
