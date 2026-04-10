"""
security_audit_advisor.py
─────────────────────────────────────────────────────────────────────────────
Comprehensive showcase of the Anthropic Advisor Tool API (beta).

Pattern: Haiku 4.5 (executor) + Opus 4.6 (advisor)
  • Haiku handles the agentic loop and tool execution — cheap and fast.
  • Opus is consulted as a server-side advisor at key decision points.
  • The API runs Opus automatically; the client never calls Opus directly.

What this example demonstrates
  ✓ Advisor tool definition     — type, model, max_uses, caching
  ✓ Beta header via betas=[]    — client.beta.messages.create() required
  ✓ Mixed tool surface          — client-side tools + server-side advisor
  ✓ Full agentic loop           — all stop_reason variants, message history
  ✓ server_tool_use blocks      — how executor signals an advisor call
  ✓ advisor_tool_result blocks  — three content variants (result/redacted/error)
  ✓ pause_turn stop_reason      — dangling advisor call handling
  ✓ usage.iterations[]          — per-model token accounting via type field
  ✓ Cost analysis               — advisor pattern vs Opus-only baseline
  ✓ Streaming variant           — client.beta.messages.stream()
  ✓ Batch variant               — valid single-turn batch with advisor only

Requirements
  pip install anthropic
  export ANTHROPIC_API_KEY="sk-ant-..."

Beta: anthropic-beta: advisor-tool-2026-03-01
  → passed via betas=["advisor-tool-2026-03-01"] on client.beta.messages.*

Valid executor/advisor pairs:
  claude-haiku-4-5   + claude-opus-4-6   ← used here (best cost savings)
  claude-sonnet-4-6  + claude-opus-4-6
  claude-opus-4-6    + claude-opus-4-6
"""

from __future__ import annotations

import json
import textwrap

from dotenv import load_dotenv
load_dotenv()
from dataclasses import dataclass, field
from typing import Any

import anthropic

# ──────────────────────────────────────────────────────────────────────────────
# Sample codebase: intentionally vulnerable Python — the agent will audit this.
# ──────────────────────────────────────────────────────────────────────────────

CODEBASE: dict[str, str] = {
    "app.py": textwrap.dedent("""\
        import sqlite3
        import subprocess
        import pickle
        import hashlib

        def login(username, password):
            conn = sqlite3.connect("users.db")
            cursor = conn.cursor()
            # Concatenate directly — no parameterisation
            query = f"SELECT * FROM users WHERE username='{username}' AND password='{password}'"
            cursor.execute(query)
            return cursor.fetchone()

        def run_report(report_name):
            # shell=True with user-controlled input
            result = subprocess.run(
                f"python reports/{report_name}.py", shell=True, capture_output=True
            )
            return result.stdout.decode()

        def load_user_settings(settings_bytes: bytes):
            # Deserialising arbitrary bytes
            return pickle.loads(settings_bytes)

        def hash_password(password: str) -> str:
            return hashlib.md5(password.encode()).hexdigest()

        # Hardcoded secrets
        SECRET_KEY = "hardcoded_secret_key_12345"
        DATABASE_URL = "postgresql://admin:password123@prod-db.internal/app"
    """),
    "config.py": textwrap.dedent("""\
        import os

        DEBUG = True
        ALLOWED_HOSTS = ["*"]
        SECRET_KEY = os.environ.get("SECRET_KEY", "fallback-secret-do-not-use-in-prod")
        MAX_UPLOAD_SIZE = 100 * 1024 * 1024   # 100 MB, no content-type validation
        CORS_ORIGIN_ALLOW_ALL = True
        SESSION_COOKIE_SECURE = False
        CSRF_COOKIE_HTTPONLY = False
    """),
    "auth.py": textwrap.dedent("""\
        import jwt
        import time

        JWT_SECRET = "jwt_secret_123"   # same secret in every environment

        def create_token(user_id: int) -> str:
            payload = {"user_id": user_id, "exp": time.time() + 86400}
            return jwt.encode(payload, JWT_SECRET, algorithm="HS256")

        def verify_token(token: str) -> dict:
            # algorithm not validated — accepts any algorithm the token claims
            return jwt.decode(token, JWT_SECRET, options={"verify_signature": False})
    """),
}


# ──────────────────────────────────────────────────────────────────────────────
# Client-side tool implementations
# ──────────────────────────────────────────────────────────────────────────────

def _tool_list_files() -> str:
    lines = [f"  {name}  ({len(src)} chars)" for name, src in CODEBASE.items()]
    return "Files in codebase:\n" + "\n".join(lines)


def _tool_read_file(filename: str) -> str:
    if filename not in CODEBASE:
        available = ", ".join(CODEBASE)
        return f"File '{filename}' not found. Available: {available}"
    return CODEBASE[filename]


def _tool_search_code(pattern: str) -> str:
    hits: list[str] = []
    for filename, src in CODEBASE.items():
        for lineno, line in enumerate(src.splitlines(), 1):
            if pattern.lower() in line.lower():
                hits.append(f"{filename}:{lineno}: {line.rstrip()}")
    return "\n".join(hits) if hits else f"No matches for '{pattern}'"


def execute_tool(name: str, tool_input: dict[str, Any]) -> str:
    """Dispatch a tool_use block to its implementation."""
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


# ──────────────────────────────────────────────────────────────────────────────
# Tool definitions sent to the API
# ──────────────────────────────────────────────────────────────────────────────

#  ┌─ Advisor tool ──────────────────────────────────────────────────────────┐
#  │ type      "advisor_20260301"   versioned server-tool type               │
#  │ name      "advisor"            how the executor refers to it            │
#  │ model     "claude-opus-4-6"    the stronger reviewer model              │
#  │ max_uses  3                    cap per API request (cost control)       │
#  │ caching   ephemeral / 5 min    reuse the advisor's context window       │
#  └─────────────────────────────────────────────────────────────────────────┘
#
#  Important API shapes:
#   • Executor signals an advisor call via a "server_tool_use" block (NOT "tool_use")
#     with name="advisor" and input={} — the input is ALWAYS an empty object;
#     the server constructs the full context automatically.
#   • The advisor result comes back as an "advisor_tool_result" block whose
#     .content field is a single object (not a list) with three possible types:
#       - advisor_result          → .text contains the advisor's response
#       - advisor_redacted_result → opaque blob; pass through verbatim
#       - advisor_tool_result_error → .error_code describes what went wrong

ADVISOR_TOOL: dict[str, Any] = {
    "type": "advisor_20260301",
    "name": "advisor",
    "model": "claude-opus-4-6",
    "max_uses": 3,
    "caching": {"type": "ephemeral", "ttl": "5m"},
}

TOOLS: list[dict[str, Any]] = [
    # ── Client-side tools (executed by this script) ──────────────────────────
    {
        "name": "list_files",
        "description": "List every source file in the codebase.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "read_file",
        "description": "Read a source file to inspect its contents.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "The filename to read (e.g. 'app.py').",
                }
            },
            "required": ["filename"],
        },
    },
    {
        "name": "search_code",
        "description": "Search for a text pattern across all source files.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Case-insensitive text to search for.",
                }
            },
            "required": ["pattern"],
        },
    },
    # ── Server-side advisor tool (executed by Anthropic infrastructure) ──────
    ADVISOR_TOOL,
]


# ──────────────────────────────────────────────────────────────────────────────
# Usage & cost tracking
# ──────────────────────────────────────────────────────────────────────────────

# Pricing per 1 M tokens (as of April 2026)
_PRICING: dict[str, dict[str, float]] = {
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00},
    "claude-opus-4-6":  {"input": 5.00, "output": 25.00},
}


@dataclass
class UsageAccumulator:
    executor_model: str
    advisor_model: str
    # per-model token counts
    _exec_in:   int = field(default=0, repr=False)
    _exec_out:  int = field(default=0, repr=False)
    _adv_in:    int = field(default=0, repr=False)
    _adv_out:   int = field(default=0, repr=False)
    _adv_calls: int = field(default=0, repr=False)

    def ingest_response(self, response: anthropic.types.Message) -> None:
        """
        Parse usage.iterations[] — each entry is one model invocation.

        The field that distinguishes executor from advisor is the "type" field
        on the iteration object:
          type == "message"          → executor (Haiku) — billed at executor rate
          type == "advisor_message"  → advisor  (Opus)  — billed at advisor rate

        The top-level usage object reflects only the executor's token counts;
        per-model breakdowns live in usage.iterations[].

        Falls back to attributing all tokens to the executor if the SDK version
        does not yet expose iterations.
        """
        usage = getattr(response, "usage", None)
        iterations = getattr(usage, "iterations", None) if usage else None

        if iterations:
            for it in iterations:
                # Identify the iteration type via its "type" field, NOT by model name
                it_type = getattr(it, "type", None) or (it.get("type") if isinstance(it, dict) else None)
                it_usage = getattr(it, "usage", None) or (it.get("usage") if isinstance(it, dict) else None)
                if it_usage is None:
                    continue
                in_t  = getattr(it_usage, "input_tokens",  0) or (it_usage.get("input_tokens",  0) if isinstance(it_usage, dict) else 0)
                out_t = getattr(it_usage, "output_tokens", 0) or (it_usage.get("output_tokens", 0) if isinstance(it_usage, dict) else 0)

                if it_type == "advisor_message":
                    self._adv_in   += in_t
                    self._adv_out  += out_t
                    self._adv_calls += 1
                else:  # it_type == "message" or unknown
                    self._exec_in  += in_t
                    self._exec_out += out_t
        elif usage:
            # Fallback: attribute everything to the executor
            self._exec_in  += getattr(usage, "input_tokens",  0)
            self._exec_out += getattr(usage, "output_tokens", 0)

    def _cost(self, model: str, in_t: int, out_t: int) -> float:
        p = _PRICING.get(model, {"input": 0.0, "output": 0.0})
        return (in_t * p["input"] + out_t * p["output"]) / 1_000_000

    def print_report(self) -> None:
        exec_cost = self._cost(self.executor_model, self._exec_in,  self._exec_out)
        adv_cost  = self._cost(self.advisor_model,  self._adv_in,   self._adv_out)
        total     = exec_cost + adv_cost

        # Hypothetical cost if Opus had handled everything
        opus_in_t  = self._exec_in  + self._adv_in
        opus_out_t = self._exec_out + self._adv_out
        opus_only  = self._cost(self.advisor_model, opus_in_t, opus_out_t)
        savings_pct = (opus_only - total) / opus_only * 100 if opus_only > 0 else 0.0

        sep = "═" * 58
        print(f"\n{sep}")
        print("  TOKEN USAGE & COST REPORT")
        print(sep)
        print(f"  Executor  : {self.executor_model}")
        print(f"    Input   : {self._exec_in:>10,} tokens")
        print(f"    Output  : {self._exec_out:>10,} tokens   ${exec_cost:.5f}")
        print(f"\n  Advisor   : {self.advisor_model}")
        print(f"    Calls   : {self._adv_calls}")
        print(f"    Input   : {self._adv_in:>10,} tokens")
        print(f"    Output  : {self._adv_out:>10,} tokens   ${adv_cost:.5f}")
        print(f"\n  Combined total     : ${total:.5f}")
        print(f"  Opus-only estimate : ${opus_only:.5f}")
        print(f"  Cost savings       : {savings_pct:.1f}%")
        print(sep)


# ──────────────────────────────────────────────────────────────────────────────
# Agentic loop
# ──────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = textwrap.dedent("""\
    You are a security code auditor. Your workflow:

    1. Call list_files to discover the codebase.
    2. Call read_file on each file and search_code for known vulnerability patterns
       (SQL injection, shell injection, pickle, hardcoded secrets, weak crypto, etc.).
    3. When you need expert judgment — prioritising findings, confirming a true
       vulnerability, or crafting remediation advice — call the advisor tool.
       The advisor is Claude Opus and has your full conversation history.
       Use it at most for the 3 most critical decisions.
    4. Produce a final Markdown security report with:
         • An executive summary
         • Findings table (ID | Severity | File | Issue | Line)
         • Remediation steps for each finding

    Be concise in your tool calls; be comprehensive in the final report.
""")


def _fmt(text: str, width: int = 100) -> str:
    """Wrap text for readable console output."""
    return textwrap.fill(text, width=width, subsequent_indent="         ")


def run_audit() -> None:
    """
    Main agentic audit loop using the advisor pattern.

    Key SDK note: the advisor tool requires client.beta.messages.create()
    with betas=["advisor-tool-2026-03-01"].  Using client.messages.create()
    with extra_headers will NOT work.
    """
    client = anthropic.Anthropic()
    usage  = UsageAccumulator(
        executor_model="claude-haiku-4-5",
        advisor_model="claude-opus-4-6",
    )

    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": (
                "Please perform a thorough security audit of this codebase. "
                "Consult the advisor when you need expert judgment on severity "
                "or remediation guidance."
            ),
        }
    ]

    print("=" * 60)
    print("  SECURITY AUDIT — Advisor Pattern Demo")
    print("  Executor: claude-haiku-4-5 | Advisor: claude-opus-4-6")
    print("=" * 60)

    step = 0
    while True:
        step += 1
        print(f"\n[Step {step}] Calling API …")

        # ── CRITICAL: must use client.beta.messages.create() + betas= ─────────
        # client.messages.create() with extra_headers does NOT activate the
        # advisor tool beta; the API will reject the advisor tool definition.
        response = client.beta.messages.create(
            model="claude-haiku-4-5",
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
            betas=["advisor-tool-2026-03-01"],
        )

        # Accumulate token usage from all model iterations in this response
        usage.ingest_response(response)

        # ── Process content blocks ─────────────────────────────────────────
        tool_results: list[dict[str, Any]] = []

        for block in response.content:
            btype = getattr(block, "type", None)

            # ── Plain text from the executor ──────────────────────────────
            if btype == "text" and block.text.strip():
                preview = block.text[:300]
                if len(block.text) > 300:
                    preview += " …"
                print(f"\n[Executor text]\n{_fmt(preview)}")

            # ── Executor is requesting an advisor consultation ─────────────
            # The executor emits a "server_tool_use" block (NOT "tool_use").
            # The input is always {} — the server builds context automatically.
            # We do NOT add a tool_result for this; the API handles the
            # Opus sub-inference internally and appends advisor_tool_result.
            elif btype == "server_tool_use" and block.name == "advisor":
                print(f"\n[Executor → Advisor] (id={block.id[:12]}…)")
                print("  (Opus is reviewing the full conversation history …)")
                # block.input is always {} — do not attempt to read a query from it

            # ── Client-side tool call ──────────────────────────────────────
            elif btype == "tool_use":
                args_str = json.dumps(block.input, ensure_ascii=False)[:80]
                print(f"\n[Tool] {block.name}({args_str})")
                result = execute_tool(block.name, block.input)
                first_line = result.splitlines()[0] if result else "(empty)"
                print(f"  → {first_line[:120]}" + (" …" if len(result) > 120 else ""))

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })

            # ── Advisor result returned by the server ──────────────────────
            # block.content is a SINGLE object (not a list) with three variants:
            #   advisor_result          → .text  contains the response
            #   advisor_redacted_result → opaque; pass through without reading
            #   advisor_tool_result_error → .error_code describes the failure
            elif btype == "advisor_tool_result":
                content = block.content
                content_type = getattr(content, "type", None)

                if content_type == "advisor_result":
                    adv_text = getattr(content, "text", "")
                    print(f"\n[Advisor → Executor] (id={block.tool_use_id[:12]}…)")
                    print(_fmt(adv_text[:500]))

                elif content_type == "advisor_redacted_result":
                    # Opaque blob — pass through verbatim in message history;
                    # do not attempt to read or log the contents.
                    print(f"\n[Advisor → Executor] result redacted (id={block.tool_use_id[:12]}…)")

                elif content_type == "advisor_tool_result_error":
                    error_code = getattr(content, "error_code", "unknown")
                    print(f"\n[Advisor error] error_code={error_code!r} (id={block.tool_use_id[:12]}…)")

        # ── Append assistant turn to history ───────────────────────────────
        # IMPORTANT: pass the full response.content — including any
        # server_tool_use and advisor_tool_result blocks.  If you later want
        # to remove the advisor tool from the tools array you must also strip
        # advisor_tool_result blocks from history, or the API returns 400.
        messages.append({"role": "assistant", "content": response.content})

        # ── Decide what to do next ─────────────────────────────────────────
        if response.stop_reason == "end_turn":
            print("\n" + "─" * 60)
            print("AUDIT COMPLETE")
            print("─" * 60)
            for block in response.content:
                if getattr(block, "type", None) == "text":
                    print(block.text)
            break

        if response.stop_reason == "tool_use":
            if tool_results:
                # Feed client-side tool results back to the executor
                messages.append({"role": "user", "content": tool_results})
            # If there were only server_tool_use (advisor) blocks, no client
            # tool results are needed; loop again and the executor continues.
            continue

        if response.stop_reason == "pause_turn":
            # A dangling advisor call: the request was paused mid-turn while
            # the advisor was running.  Resume by calling the API again with
            # the current history — the advisor result will be delivered and
            # the executor will continue from where it left off.
            print(f"\n[pause_turn] Resuming after advisor sub-inference …")
            continue

        # Unexpected stop reason — surface it and stop
        print(f"\n[Warning] Unexpected stop_reason={response.stop_reason!r}. Stopping.")
        break

    # ── Print cost breakdown ───────────────────────────────────────────────────
    usage.print_report()


# ──────────────────────────────────────────────────────────────────────────────
# Streaming variant
# ──────────────────────────────────────────────────────────────────────────────

def run_audit_streaming() -> None:
    """
    Same audit loop but with streaming — useful for long responses.

    Note on advisor + streaming: the advisor sub-inference does NOT stream.
    When the executor calls the advisor, the stream pauses, Opus runs
    server-side, and the advisor_tool_result arrives as a single fully-formed
    content_block_start event once Opus finishes.  Using stream.text_stream
    will simply show nothing during that pause, which is fine.

    Uses client.beta.messages.stream() — the beta namespace is required
    for the same reason as client.beta.messages.create().
    """
    client = anthropic.Anthropic()

    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": "Audit this codebase. Use the advisor for key security judgements.",
        }
    ]

    print("\n" + "=" * 60)
    print("  STREAMING VARIANT")
    print("=" * 60)

    while True:
        with client.beta.messages.stream(
            model="claude-haiku-4-5",
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
            betas=["advisor-tool-2026-03-01"],
        ) as stream:
            print("\n[Streaming executor output …]")
            for text in stream.text_stream:
                print(text, end="", flush=True)

            response = stream.get_final_message()

        tool_results: list[dict[str, Any]] = []
        for block in response.content:
            btype = getattr(block, "type", None)

            if btype == "server_tool_use" and block.name == "advisor":
                print(f"\n[Advisor called — result in next block]")

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

        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            print("\n[Done]")
            break

        if response.stop_reason == "pause_turn":
            print("\n[pause_turn] Resuming …")
            continue

        if tool_results:
            messages.append({"role": "user", "content": tool_results})


# ──────────────────────────────────────────────────────────────────────────────
# Batch variant — valid single-turn request with advisor only
# ──────────────────────────────────────────────────────────────────────────────

def submit_batch_audit() -> str:
    """
    Submit the audit as a batch job (50 % cheaper, processes within 24 hours).
    Returns the batch ID.

    WHY no client-side tools here
    ─────────────────────────────
    The batch API is single-turn: it submits one request and returns one
    response.  There is no mechanism to send tool results back for a second
    turn.  Client-side tools (list_files, read_file, search_code) require
    exactly that round-trip, so they cannot be used in a batch request.

    The advisor tool IS a server-side tool — Opus runs inside the same
    request, needing no client round-trip.  So a valid batch request can
    include the advisor but must provide all codebase content inline in
    the user message instead of relying on tool calls to fetch it.
    """
    client = anthropic.Anthropic()

    # Build an inline codebase summary for the model to analyse without tools
    codebase_text = "\n\n".join(
        f"### {filename}\n```python\n{src}```"
        for filename, src in CODEBASE.items()
    )

    # Only the server-side advisor tool — no client-side tools
    batch_tools = [ADVISOR_TOOL]

    batch = client.beta.messages.batches.create(
        requests=[
            {
                "custom_id": "security-audit-001",
                "params": {
                    "model": "claude-haiku-4-5",
                    "max_tokens": 4096,
                    "system": (
                        "You are a security code auditor. Analyse the provided source "
                        "files for vulnerabilities. Use the advisor tool to validate "
                        "your most critical findings. Produce a Markdown report."
                    ),
                    "tools": batch_tools,
                    "messages": [
                        {
                            "role": "user",
                            "content": (
                                "Please audit the following codebase:\n\n"
                                + codebase_text
                            ),
                        }
                    ],
                },
            }
        ],
        betas=["advisor-tool-2026-03-01"],
    )

    print(f"Batch submitted: {batch.id}  status={batch.processing_status}")
    return batch.id


def retrieve_batch_results(batch_id: str) -> None:
    """Poll and print results for a previously submitted batch."""
    client = anthropic.Anthropic()
    batch = client.beta.messages.batches.retrieve(batch_id, betas=["advisor-tool-2026-03-01"])
    print(f"Batch {batch_id}: status={batch.processing_status}")

    if batch.processing_status != "ended":
        print("Not finished yet. Try again later.")
        return

    for result in client.beta.messages.batches.results(batch_id, betas=["advisor-tool-2026-03-01"]):
        print(f"\n── custom_id={result.custom_id}  type={result.result.type}")
        if result.result.type == "succeeded":
            for block in result.result.message.content:
                if getattr(block, "type", None) == "text":
                    print(block.text)


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Non-streaming audit with full cost breakdown
    run_audit()

    # Uncomment to try the streaming variant:
    # run_audit_streaming()

    # Uncomment to submit a batch job (50% cheaper, no latency requirement):
    # batch_id = submit_batch_audit()
    # print(f"Poll for results: retrieve_batch_results('{batch_id}')")
