# security_audit_advisor.py

A complete, runnable example of the Anthropic **Advisor Tool** (beta) — pairing a fast executor model (Haiku 4.5) with a high-intelligence advisor model (Opus 4.6) to perform an agentic security audit at a fraction of the cost of running Opus alone.

---

## Documentation

| Section | What's inside |
|---------|--------------|
| [What is the advisor tool?](overview/what-is-the-advisor-tool.md) | Mental model, how the pattern works, when to use it |
| [Key concepts](overview/key-concepts.md) | Glossary of every term used across the docs |
| [Quickstart](getting-started/quickstart.md) | Install, set API key, run the example in under 5 minutes |
| [Advisor API shapes](concepts/advisor-api-shapes.md) | Every content block type, annotated against the code |
| [Agentic loop](concepts/agentic-loop.md) | How `run_audit()` drives the multi-turn loop |
| [Usage and cost tracking](concepts/usage-and-cost.md) | `usage.iterations`, per-model billing, `UsageAccumulator` |
| [Streaming variant](guides/streaming-variant.md) | How `run_audit_streaming()` works and what changes |
| [Batch variant](guides/batch-variant.md) | Why the batch design is different and how to use it |
| [Advisor tool reference](reference/advisor-tool-reference.md) | Complete field reference, error codes, valid model pairs |
| [Troubleshooting](troubleshooting/common-issues.md) | The most common errors and how to fix them |
| [Code walkthrough](walkthrough.md) | Line-by-line tour of the file + cost estimate for `run_audit()` |

> **New here?** Start at [What is the advisor tool?](overview/what-is-the-advisor-tool.md), then follow the [Quickstart](getting-started/quickstart.md).
