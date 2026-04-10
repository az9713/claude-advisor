# Usage and cost tracking

How advisor tokens are reported, how to read `usage.iterations`, and how `UsageAccumulator` computes the cost comparison.

---

## The `usage.iterations` array

The top-level `usage` object in every response includes an `iterations` array when the advisor tool is active. Each entry is one model invocation:

```json
{
  "usage": {
    "input_tokens": 412,
    "output_tokens": 531,
    "iterations": [
      {
        "type": "message",
        "input_tokens": 412,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "output_tokens": 89
      },
      {
        "type": "advisor_message",
        "model": "claude-opus-4-6",
        "input_tokens": 823,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "output_tokens": 1612
      },
      {
        "type": "message",
        "input_tokens": 1348,
        "cache_read_input_tokens": 412,
        "cache_creation_input_tokens": 0,
        "output_tokens": 442
      }
    ]
  }
}
```

### Reading the `type` field

The field that distinguishes executor iterations from advisor iterations is **`type`**, not `model`:

| `type` | What it is | Billed at |
|--------|-----------|-----------|
| `"message"` | An executor inference (Haiku) | Executor model rates |
| `"advisor_message"` | An advisor sub-inference (Opus) | Advisor model rates |

> **Common mistake:** Checking `iteration.model == "claude-opus-4-6"` to detect advisor iterations. The `model` field is present on `advisor_message` entries, but it is absent on `message` entries — using `model` as the discriminator breaks when the executor and advisor happen to be the same model. Always use `type`.

### Top-level totals are executor-only

The top-level `usage.input_tokens` and `usage.output_tokens` reflect **executor tokens only**. Advisor tokens are not rolled in:

```
top-level output_tokens  = sum of all "message" iteration output_tokens
top-level input_tokens   = first "message" iteration input_tokens only
                           (not summed because later iterations include prior output)
```

Use `usage.iterations` when building cost-tracking logic, not the top-level totals.

---

## `UsageAccumulator`

The example accumulates usage across multiple API calls and prints a cost comparison at the end.

### Ingestion

```python
def ingest_response(self, response) -> None:
    usage = getattr(response, "usage", None)
    iterations = getattr(usage, "iterations", None) if usage else None

    if iterations:
        for it in iterations:
            it_type = getattr(it, "type", None)   # "message" or "advisor_message"
            ...
            if it_type == "advisor_message":
                self._adv_in  += in_t
                self._adv_out += out_t
                self._adv_calls += 1
            else:
                self._exec_in  += in_t
                self._exec_out += out_t
    elif usage:
        # SDK version doesn't expose iterations yet — attribute all to executor
        self._exec_in  += getattr(usage, "input_tokens",  0)
        self._exec_out += getattr(usage, "output_tokens", 0)
```

`ingest_response()` is called once per API response, inside the agentic loop.

### Cost calculation

```python
def _cost(self, model: str, in_t: int, out_t: int) -> float:
    p = _PRICING.get(model, {"input": 0.0, "output": 0.0})
    return (in_t * p["input"] + out_t * p["output"]) / 1_000_000
```

Prices (April 2026, per 1 M tokens):

| Model | Input | Output |
|-------|-------|--------|
| `claude-haiku-4-5` | $1.00 | $5.00 |
| `claude-opus-4-6` | $5.00 | $25.00 |

### Savings estimate

The report computes what the audit would have cost if Opus had generated all tokens:

```python
opus_in_t  = self._exec_in  + self._adv_in
opus_out_t = self._exec_out + self._adv_out
opus_only  = self._cost("claude-opus-4-6", opus_in_t, opus_out_t)
savings_pct = (opus_only - total) / opus_only * 100
```

This is an approximation. An Opus-only agent would likely generate different token counts (fewer iterations, more confident decisions, shorter tool sequences). Use it as a directional guide, not a precise measurement.

---

## Advisor prompt caching

When `caching: {"type": "ephemeral", "ttl": "5m"}` is set on the tool definition, the advisor's own transcript is cached across calls within the same conversation. Each advisor call writes a cache entry; the next call reads up to that point and only pays for the delta.

You see this reflected as nonzero `cache_read_input_tokens` on the second and later `advisor_message` iterations:

```json
{
  "type": "advisor_message",
  "model": "claude-opus-4-6",
  "input_tokens": 0,
  "cache_read_input_tokens": 823,   ← cache hit
  "cache_creation_input_tokens": 0,
  "output_tokens": 614
}
```

**When to enable it:** Caching costs more than it saves when the advisor is called two or fewer times per conversation. It breaks even at roughly three calls. The example sets `caching` to show the feature; for a real workload, benchmark whether your advisor call frequency justifies it.

**Keep it consistent:** Do not toggle `caching` on and off mid-conversation. Cache misses negate the savings.

> **Warning:** Using `clear_thinking` with `keep` set to anything other than `"all"` causes advisor-side cache misses by shifting the quoted transcript each turn. If extended thinking is enabled without explicit `clear_thinking` config, the API defaults to `keep: {type: "thinking_turns", value: 1}`, which triggers this. Set `keep: "all"` to preserve advisor cache stability.

---

## Cost estimate for `run_audit()` on this codebase

Estimates for the three-file codebase in `security_audit_advisor.py`, with two advisor calls (typical given the system prompt's guidance to "use it at most for the 3 most critical decisions").

### Assumed execution flow

| Turn | What happens | Executor input | Executor output |
|------|-------------|----------------|-----------------|
| 1 | `list_files` call | ~385 | ~50 |
| 2 | `read_file` × 3 | ~485 | ~120 |
| 3 | `search_code` × 2 | ~945 | ~80 |
| 4 | First advisor call + interim reasoning | ~1,085 | ~200 |
| 5 | Second advisor call + final report | ~1,650 | ~750 |
| **Total** | | **~4,550** | **~1,200** |

Each turn's input is the full accumulated message history — system prompt, all prior turns, all tool results — which is why input tokens grow with each turn.

### Advisor sub-inferences

| Call | Advisor input | Advisor output | Notes |
|------|--------------|----------------|-------|
| 1 | ~1,400 | ~1,600 | ~600 text + ~1,000 thinking |
| 2 | ~2,100 | ~1,600 | larger context; caching reduces effective cost |
| **Total** | **~3,500** | **~3,200** | |

Advisor output (1,400–1,800 tokens total including thinking) is the same regardless of how large the executor's `max_tokens` is — it is a separate sub-inference.

### Cost breakdown

**Executor (Haiku):**

| | Tokens | Rate (per 1M) | Cost |
|--|--------|--------------|------|
| Input | 4,550 | $1.00 | $0.00455 |
| Output | 1,200 | $5.00 | $0.00600 |
| **Subtotal** | | | **$0.011** |

**Advisor (Opus, 2 calls):**

| | Tokens | Rate (per 1M) | Cost |
|--|--------|--------------|------|
| Input | 3,500 | $5.00 | $0.01750 |
| Output | 3,200 | $25.00 | $0.08000 |
| **Subtotal** | | | **$0.098** |

**Combined: ~$0.109**

Opus-only baseline (same token counts, all at Opus rates): ~$0.150

**Cost savings vs Opus-only: ~27%**

### Where the money goes

Advisor output dominates. Two advisor calls produce ~3,200 output tokens at $25/M = $0.080 — 73% of the total bill. The executor handles the bulk of the work (5 turns, 7 tool calls) but costs only $0.011 because Haiku's output rate is 5× cheaper than Opus's.

### Sensitivity to advisor call count

| Advisor calls | Total cost | Notes |
|---------------|-----------|-------|
| 1 | ~$0.062 | |
| 2 | ~$0.109 | base case above |
| 3 (`max_uses` reached) | ~$0.155 | |
| 2 with caching (2nd call hits cache) | ~$0.099 | saves ~$0.010 on advisor input |

### Scaling to a real codebase

This example audits three tiny files (~340 tokens of source). A 50-file Python codebase at ~200 tokens per file adds roughly ~10,000 tokens of `read_file` results and a proportionally larger advisor transcript. Expect **$0.40–$0.55 per full audit** at that scale — still well below an equivalent Opus-only run.

---

## Sample output

```
══════════════════════════════════════════════════════════
  TOKEN USAGE & COST REPORT
══════════════════════════════════════════════════════════
  Executor  : claude-haiku-4-5
    Input   :      4,123 tokens
    Output  :        891 tokens   $0.00445
  Advisor   : claude-opus-4-6
    Calls   : 2
    Input   :      6,211 tokens
    Output  :      1,043 tokens   $0.05709
  Combined total     : $0.06154
  Opus-only estimate : $0.18417
  Cost savings       : 66.6%
══════════════════════════════════════════════════════════
```

See also: [Advisor API shapes](advisor-api-shapes.md) · [Advisor tool reference](../reference/advisor-tool-reference.md)
