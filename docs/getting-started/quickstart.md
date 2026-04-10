# Quickstart

Run the security audit example in under 5 minutes.

## Prerequisites

- Python 3.9+
- An Anthropic API key with access to the advisor tool beta

Verify Python:

```bash
python --version
# Python 3.9.x or higher
```

## 1. Create a virtual environment

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
```

## 2. Install dependencies

```bash
pip install anthropic python-dotenv
```

Verify the install:

```bash
python -c "import anthropic; print(anthropic.__version__)"
# 0.x.x
```

## 3. Set your API key

Create a `.env` file in the project directory:

```bash
echo 'ANTHROPIC_API_KEY="sk-ant-..."' > .env
```

## 4. Run the audit

```bash
python security_audit_advisor.py
```

Expected output:

```
============================================================
  SECURITY AUDIT — Advisor Pattern Demo
  Executor: claude-haiku-4-5 | Advisor: claude-opus-4-6
============================================================

[Step 1] Calling API …

[Tool] list_files({})
  → Files in codebase: …

[Step 2] Calling API …

[Tool] read_file({"filename": "app.py"})
  → import sqlite3 …

...

[Executor → Advisor] (id=srvtoolu_ab…)
  (Opus is reviewing the full conversation history …)

[Advisor → Executor] (id=srvtoolu_ab…)
  The SQL injection in login() is critical — parameterised queries fix it.
  The pickle.loads() deserialisation is also critical ...

...

────────────────────────────────────────────────────────────
AUDIT COMPLETE
────────────────────────────────────────────────────────────
## Security Audit Report
...

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

Token counts and cost will vary. The cost savings percentage reflects the advisor pattern vs running Opus for the whole task.

## What happened

Haiku ran the agentic loop: it listed files, read each one, searched for patterns. When it needed expert judgment on severity or remediation, it called the advisor tool. Opus reviewed the full audit transcript server-side and returned guidance. Haiku used that guidance to write the final report.

See [Agentic loop](../concepts/agentic-loop.md) for a detailed walkthrough of what happens at each step.

## Next steps

- [What is the advisor tool?](../overview/what-is-the-advisor-tool.md) — understand the mental model
- [Advisor API shapes](../concepts/advisor-api-shapes.md) — understand every content block type
- [Streaming variant](../guides/streaming-variant.md) — run with streaming output
- [Batch variant](../guides/batch-variant.md) — submit as a background job
