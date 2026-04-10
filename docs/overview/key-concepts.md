# Key concepts

Definitions for every term used across this documentation.

---

**Advisor model** — The higher-intelligence model (always `claude-opus-4-6` in the current beta) that runs as a server-side sub-inference when the executor calls the advisor tool. The advisor sees the executor's full transcript but runs without tools and without streaming. Its output is typically 400–700 text tokens.

**Advisor prompt caching** — An optional caching layer specific to the advisor's own transcript. Enabled by setting `caching: {"type": "ephemeral", "ttl": "5m"}` on the tool definition. Different from executor-side prompt caching. Breaks even at roughly three advisor calls per conversation. See [Usage and cost tracking](../concepts/usage-and-cost.md).

**`advisor_tool_result` block** — A content block that appears in the assistant's response after a `server_tool_use` advisor call. Contains the advisor's response as a discriminated union: `advisor_result` (plaintext), `advisor_redacted_result` (encrypted), or `advisor_tool_result_error`. Must be passed back verbatim on subsequent turns. See [Advisor API shapes](../concepts/advisor-api-shapes.md).

**`advisor_tool_result_error`** — A variant of `advisor_tool_result` returned when the advisor sub-inference fails. The executor sees the error and continues without advice; the overall request does not fail. Contains an `error_code` field. See the [error codes table](../reference/advisor-tool-reference.md#error-codes).

**Beta header** — The HTTP header `anthropic-beta: advisor-tool-2026-03-01` required to activate the advisor tool. In the Python SDK, passed as `betas=["advisor-tool-2026-03-01"]` to `client.beta.messages.create()`. Using `client.messages.create()` with `extra_headers` does not activate the beta.

**Client-side tool** — A tool whose implementation runs in your own code. In this example: `list_files`, `read_file`, `search_code`. The executor emits a `tool_use` block; your code runs the function and sends back a `tool_result`. Contrast with server-side tool.

**Executor model** — The fast, lower-cost model that runs the agentic loop. In this example: `claude-haiku-4-5`. Handles all mechanical work (tool calls, iteration) and calls the advisor at key decision points.

**`max_uses`** — Optional integer field on the advisor tool definition capping advisor calls per API request. Once reached, further calls return `advisor_tool_result_error` with `error_code: "max_uses_exceeded"` and the executor continues. This is a per-request cap, not a per-conversation cap. To cap across a full conversation, count client-side and remove the advisor tool when the ceiling is hit.

**Multi-turn conversation** — A conversation that spans more than one API call, with message history passed back on each request. The advisor tool requires that `advisor_tool_result` blocks be included in the history on every subsequent turn. Omitting them while the advisor tool is still in `tools` causes a `400 invalid_request_error`.

**`pause_turn`** — A `stop_reason` value indicating the request stopped mid-turn due to a dangling advisor call. The advisor runs on the next API call with the same history. Handled by looping without appending anything new to messages. See [Agentic loop](../concepts/agentic-loop.md).

**Server-side tool** — A tool whose implementation runs on Anthropic's infrastructure, requiring no client round-trip. The advisor tool is server-side. Contrast with client-side tool.

**`server_tool_use` block** — The content block type the executor emits when calling a server-side tool. For advisor calls, `name` is `"advisor"` and `input` is always `{}` — the server constructs the full context automatically. Not the same as `tool_use` (which is for client-side tools).

**Sub-inference** — The separate Opus inference that runs server-side when the advisor is called. Billed at Opus rates, tracked separately in `usage.iterations` with `type: "advisor_message"`.

**`usage.iterations`** — An array on the response `usage` object listing every model invocation in the request. Each entry has `type: "message"` (executor) or `type: "advisor_message"` (advisor). The top-level `usage.input_tokens` and `usage.output_tokens` reflect only the executor; advisor tokens are in the iterations array only. See [Usage and cost tracking](../concepts/usage-and-cost.md).

**`UsageAccumulator`** — The class in `security_audit_advisor.py` that parses `usage.iterations` across multiple API calls and produces a cost comparison report at the end of the audit.
