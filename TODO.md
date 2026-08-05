# TODO — MCP integration

Plan for turning the CLI chat agent into a personal advisor for **career and
financial planning as one problem**: an MCP client that drives a Monte Carlo
retirement engine and reads live portfolio data, grounded in the user's career
history, plan and targets.

---

## Architecture (decided)

```
AIagent repo                              portfolio-lifecycle-simulator repo
├── MCP client (agent + tool loop)
├── data/personal/*.docx → system prompt
├── sheets MCP server ────── stdio ──┐
└── config.toml (gitignored) ────────┴── stdio ──→ simulator MCP server
```

**Decisions and their reasons**

| Decision | Choice | Why |
|---|---|---|
| CV + career plan | Static context in the system prompt | It is both the subject of the conversation and the frame for every answer — small, always relevant, never changes mid-session. As a tool the model must *decide* to fetch it, often won't, and burns a round-trip. Not MCP Resources either — indirection with no payoff for local files read by a single client. |
| Financial position and goals | Not static context, not a document — a live tool call | The target portfolio value and the assumptions behind it (withdrawal rate, confidence, tax treatment) are computed by the simulator from hypotheses that already live as config in `portfolio-lifecycle-simulator`. A docx here would duplicate that and go stale the moment an assumption changes there — see Phase 3. |
| Simulator server location | `portfolio-lifecycle-simulator` repo | The server wraps that engine and versions with it; anyone cloning the simulator can drive it conversationally. |
| Sheets server location | This repo | No other consumer; it is a capability of this agent, not of the engine. |
| Transport | stdio, both servers | Local, single user, client spawns each as a subprocess. No ports, no auth layer. HTTP only earns its complexity when something remote must reach the server. |
| Sheets tool granularity | Two named tools, not `read_sheet(name)` | Named tools carry their own descriptions; the model selects correctly instead of guessing a string argument. |
| Cross-repo coupling | One path in `config.toml` | Machine-specific, gitignored; `config.example.toml` committed. |

---

## Phase 1 — Tool loop in the client (no MCP yet)

Blocked by: nothing. This is the risky part and it is testable in isolation.

- [x] `ToolExecutor` protocol — `(name, arguments) -> str`. The loop depends on
      this, not on MCP, so the transport can be swapped and tested with a plain
      function. `src/agent.py::ToolExecutor`.
- [x] Agentic loop: call → if `stop_reason == "tool_use"`, execute each requested
      tool, append a `tool_result` message, call again → repeat until `end_turn`.
      `AiAgent._check_tool_calls`.
- [x] `max_tool_iterations` guard so a confused model cannot loop forever.
- [x] **Memory rollback.** Fixed: `run()` now snapshots `len(memory)` at the start
      of each turn and truncates back to it on any failure, instead of the old
      `self.__memory.pop()` which only removed the last message — broken the
      moment a tool turn appends several (assistant `tool_use`, user
      `tool_result`, …).
- [x] **Summarization must handle block content.** Fixed: `AiAgent._flatten_content`
      renders a message's content as plain text whether it's a string, a list of
      raw SDK content blocks (an assistant tool_use turn), or a list of the plain
      dicts this class builds itself (a tool_result turn) — `_check_memory_for_summary`
      previously concatenated `message["content"]` directly and raised `TypeError`
      the moment a tool turn survived long enough to be summarized.
- [x] Summarization calls must not offer tools (`use_tools=False`) — `ask_claude`
      takes a `use_tools` flag; `_check_memory_for_summary` sets it False.
- [x] Tests: mock executor, multi-round tool turn, rollback on mid-loop failure,
      iteration cap, two distinctly-named mock tools proving the loop selects
      between them rather than just calling the one tool available, and a memory
      containing tool_use/tool_result blocks summarizing without raising —
      `tests/test_agent.py::TestRunToolLoop`,
      `tests/test_check_memory_for_summary.py::TestFlattenContent` /
      `TestSummarizationWithToolBlocksInMemory`.

**Done.** A plain Python function — no MCP, no transport — can be passed as
`tool_executor` and the agent completes a multi-round, multi-tool conversation
with it, tests green.

## Phase 2 — Sheets MCP server (first real server)

Blocked by: nothing. Built first because it has no cross-repo dependency at
all — even Phase 3's minimal version needs to shell out to another repo's CLI.

- [x] Auth: **service account, not OAuth user flow**. Service account created,
      JSON key in `.secrets/` (gitignored), spreadsheet shared with it, Sheet ID
      and both tab names in `config.toml` (gitignored; `config.example.toml`
      committed as the template).
- [x] stdio MCP server in this repo — `src/sheets_server.py`. Config load +
      auth deferred into `_init()`, called only from `__main__`, so importing
      the module needs no `config.toml`, credentials, or network.
- [x] Tools: `read_portfolio_overview()`, `read_networth_overview()` — one
      spreadsheet, two tabs (`portfolio_overview_tab` and `networth_overview_tab`,
      per `config.toml`), not two separate spreadsheets. Both already
      summary-level, so each returns its tab whole, rendered as a markdown table.
- [x] ~~**Resource**: column schema per tab~~ — built, then dropped. Nothing
      in this repo's client is application code that would ever decide to
      attach a resource on its own (it's a plain CLI chat loop), so the only
      payoff was ticking a protocol-coverage box, not demonstrating anything
      real. Not worth the surface area; MCP has three primitives and this repo
      will cover two (tools, prompts) meaningfully instead of three thinly.
- [x] **Prompt**: `sheets_server.py::portfolio()` — a canned question ("current
      total portfolio value, composition, and unrealized gain... don't bring in
      career planning unless I ask") the user invokes as `/portfolio`, not a
      hardcoded query: the resolved text is sent to Claude like any other
      message, and the model still decides which tools to call. User-controlled,
      the third primitive. Client side needed building too, not just the
      decorator: `McpClient.get_prompt()` (mirrors `call_tool`'s error handling -
      an unknown prompt name is itself an `MCPError`, no soft `is_error` outcome
      the way tools have, so it raises `ToolExecutorError` same as a dead
      connection) and a `PromptResolver` protocol in `agent.py` (mirrors
      `ToolExecutor`, same reason: `agent.py` must not import anything
      MCP-specific). `run()` now special-cases `/name` input before it touches
      memory, resolving it and substituting the resolved text as the turn's
      content - the model never sees the literal slash command. Verified
      end-to-end against the real subprocess and a real Claude call.
- [x] Client exercises the lifecycle, not just `call_tool`: `main.py` opens
      `McpClient` as `async with` (spawn + `initialize` handshake), calls
      `list_tools()`, wires the result straight into `AiAgent(tools=...,
      tool_executor=client.call_tool, prompt_resolver=client.get_prompt)`.
      Verified end-to-end: real subprocess, real tool call, real prompt
      resolution, real data back. `list_resources` no longer applies
      (Resources were dropped, see above).
- [x] Error paths, deliberately: tool raises, server dies mid-session,
      malformed arguments from the model. Probed the SDK empirically (not
      guessed) to find both are already the *same* shape at the transport
      level: a tool that raised, got bad arguments, or doesn't exist is a
      normal MCP response - `CallToolResult.is_error` - and `McpClient.call_tool`
      already turned it into `[tool error] ...` text for the model to reason
      about. A dead connection (server process killed mid-session, or fails to
      start) is different: it raises `mcp.shared.exceptions.MCPError`, which the
      model can't reason about, so `McpClient.call_tool` now catches it and
      re-raises the transport-agnostic `agent.py::ToolExecutorError` - kept in
      `agent.py`, not `mcp_client.py`, for the same reason `ToolExecutor` lives
      there: the agent must not import anything MCP-specific. `AiAgent.run()`
      catches it exactly like an Anthropic API error - roll back the turn,
      inform the user, keep the session alive. Note: no reconnect - a dead
      connection stays dead for the rest of the session, since `main.py` opens
      it once for the whole run; acceptable for a single-user CLI, would need
      revisiting for anything longer-lived.
      Tests: `tests/test_mcp_client.py` (mocked ClientSession - is_error vs.
      ToolExecutorError), `tests/test_agent.py::TestRunToolLoop::
      test_dead_mcp_connection_rolls_back_the_failed_turn`,
      `tests/test_sheets_server.py::TestToolRaisesPropagatesNotSwallowed`.
      **Follow-up from review feedback**: "kill the server mid-call and see
      what actually happens" - the earlier verification only had the tool kill
      *its own* process; an externally-sent `SIGKILL` (real subprocess PID,
      timed at 7 different offsets mid-call, plus idle-then-call, plus
      mid-handshake) was untested. Result: `MCPError` was the type in every
      case - the SDK's stdio transport already normalizes broken-pipe/closed-
      resource races internally - so the earlier fix's *type* was right, but
      one *site* was missing it: `McpClient.__aenter__()` had no MCPError
      handling at all, so a death during the handshake propagated as a raw
      `MCPError` instead of `ToolExecutorError`. Fixed, and `main.py` now
      catches `ToolExecutorError` around the connection and exits cleanly
      (`SystemExit`) instead of a raw traceback. Also found and fixed a real
      resource leak while at it: `__aenter__` raising means `__aexit__` is
      never called (context-manager protocol), so anything already entered
      onto `self._exit_stack` (e.g. a successfully spawned subprocess, if
      `stdio_client` succeeded but `initialize()` failed after) was being
      abandoned for the garbage collector - which is what produced disorderly
      "cancel scope in a different task" noise on this one path. Fixed by
      explicitly `await self._exit_stack.aclose()` in the except block before
      re-raising. Test: `tests/test_mcp_client.py::TestConnectionFailure`.

*Why more than the minimum:* a client plus one tool-serving server demonstrates
roughly 40% of MCP's surface. Covering tools and prompts properly, the full
lifecycle and the failure modes costs about a day more and is the difference
between "I made a tool work" and knowing the protocol — which matters for the
certification this work doubles as preparation for. Resources were tried and
cut (see above): thin coverage of all three primitives was worse than solid
coverage of two.
- [x] Tests: mocked Sheets client; server starts and lists tools and prompts;
      no network — `tests/test_sheets_server.py`, `tests/test_mcp_client.py`,
      `tests/test_agent.py::TestSlashCommands`.

**Done.** Tools, Prompt, full client lifecycle, and error paths are all built,
tested, and verified end-to-end against a real subprocess and a real Claude
call - not just unit tests in isolation. 79 tests green, pylint clean.

## Phase 3 — Many servers, not one

Blocked by: nothing. Do this **before** writing the second server, or the
second server's wiring gets built twice.

Today `main.py` holds one `McpClient` and hands the agent that client's three
methods. The agent needs no change: it depends on `list_tools()`,
`call_tool(name, args)` and `get_prompt(name)`, so anything exposing the same
three methods can take the client's place.

- [x] `McpClientGroup` — owns several `McpClient`s, presents the same interface:
      - `list_tools()` → merged list across servers, cached at connect time
      - `call_tool(name, args)` → routed to the owning server
      - `get_prompt(name)` → routed to the owning server
      Built as an async context manager like `McpClient` — `main.py` now opens
      `async with McpClientGroup(clients) as group:` over a `dict[str, McpClient]`
      instead of a single `McpClient`. `src/mcp_client.py::McpClientGroup`.
      Needed one small prerequisite on `McpClient` itself: `list_prompts()`
      (mirrors `list_tools()`), since routing `get_prompt()` requires knowing
      *which* server owns a given prompt name before calling it — something the
      single-server client never had to answer.
- [x] Routing table, built once at connect time from each live server's
      `list_tools()`/`list_prompts()` — `McpClientGroup._register()`, called for
      both tools and prompts (`self._tool_owner`, `self._prompt_owner`).
- [x] **Name collisions**: two servers exposing the same tool *or prompt* name
      (extended from the original tools-only bullet — prompts get the identical
      treatment for the identical reason) is a configuration error, not
      something to resolve silently. Detected in `_register()` at connect time,
      raised as `ServerCollisionError` naming both servers. Subclasses
      `ToolExecutorError` rather than living in `agent.py` — it's an MCP-wiring
      concern the agent never needs to know exists, since a collision aborts
      startup before `AiAgent` is even constructed. (Prefixing — `sheets.read_x`
      — remains the rejected alternative, same reasoning as before: you own
      both servers, don't collide.)
- [x] **Partial failure**: a server that fails to connect is warned about and
      skipped, not fatal — a dead simulator should not block portfolio
      questions. Only fatal (`ToolExecutorError`) if *no* server connects.
      `main.py` no longer exits on one dead server, only on total failure.
- [x] ~~Connect servers concurrently (`asyncio.gather`)~~ — attempted, reverted
      after it broke against the real `sheets_server.py` subprocess, not a
      hypothetical: `asyncio.gather` runs each `client.__aenter__()` in its own
      asyncio Task; `McpClient.__aenter__` opens an `anyio` task group (inside
      `stdio_client`) that is permanently tied to whichever task opened it.
      Teardown later happens from `McpClientGroup`'s own task, not that
      short-lived gather task, and anyio refuses to close a task group from a
      different task than the one that opened it —
      `RuntimeError: Attempted to exit cancel scope in a different task than it
      was entered in`. Real concurrent connecting would need each server to own
      a long-lived task for its whole connection lifetime, not just its connect
      step — meaningfully more machinery than a couple of local subprocess
      servers justify. Connects sequentially instead; reasoning kept in
      `McpClientGroup.__aenter__`'s docstring so this isn't quietly "fixed" back.
      Caught by actually running `main.py` end to end, not by the unit tests —
      the fake-client test double has no `anyio` task group underneath it to
      break, which is exactly why this was run for real before calling the
      phase done, same lesson as Phase 2's mid-call-kill follow-up.
- [x] Server list comes from `config.toml`: `[[mcp_servers]]` array of tables
      (`name`, `script`), one entry today (sheets); `config.example.toml`
      updated to match. `main.py` reads it and builds `{name: McpClient(...)}`.
- [x] Tests: `tests/test_mcp_client_group.py`, 13 tests against a fake-client
      double (not a real subprocess — see the concurrency note above for why
      that boundary matters) — merged tool list, full entered/exited lifecycle,
      `call_tool`/`get_prompt` routing plus unknown-name errors, tool *and*
      prompt collisions (including a regression test that already-connected
      clients get torn down when a later collision aborts `__aenter__`),
      partial failure, total failure.

**Done.** `McpClientGroup` is wired into `main.py` end to end and verified
against the real `sheets_server.py` subprocess (clean connect, clean exit, no
traceback) as well as 13 unit tests against a fake double. 93 tests green,
pylint 10.00/10 on `main.py`/`src/`. One real bug found only by the real-
subprocess run, not the unit tests — see the concurrency bullet above — which
is the reason that verification step stays mandatory before calling a phase
done, not just a nice-to-have.

## Phase 4 — Simulator MCP server

Blocked by: nothing. **The earlier plan assumed subprocess + `--json`; that was
wrong.** This server lives *inside* `portfolio-lifecycle-simulator`, so it can
import the engine directly — which removes the whole blocker list:

| Assumed blocker | Why it disappears |
|---|---|
| `--json` structured output | Direct calls return typed objects; nothing to parse. |
| `--fast` flag | Just pass `n_simulations=5_000` to the function. |
| `--record` side-effect gating | Verified: `_append_history`, `_append_drift_snapshot` and `last_run_*` writes live **only** in that repo's `main.py`. The `compute_*` functions in `monte_carlo.py` are pure — calling them writes nothing. |
| `--config` isolation | Build a `Config` in memory per call; never touch `config.toml`. |

- [ ] FastMCP server in the simulator repo, importing `monte_carlo` directly.
- [ ] Start with the pure compute functions, which need no refactor:
      `compute_canonical_fi_required_scan`, `simulate_decumulation`,
      `compute_two_bucket_required_wealth`, `compute_survival_matrix`.
- [ ] Whitelist the ~12 overridable parameters at the tool boundary; keep tax
      rates, `basiszins`, `teilfreistellung` and simulation counts internal.
- [ ] Return *why* a result came out as it did — binding constraint, chosen
      allocation, whether the search hit its bounds — so the model can reason
      about the next call instead of guessing.
- [ ] Annotate honestly: `readOnlyHint: true` on all of these (and retrofit the
      sheets tools, which are also read-only).
- [ ] Latency: control it with `n_simulations`, and **measure before choosing a
      contract**. If a useful call still exceeds ~30s, the tool must become
      `start_run()` → `get_result(job_id)` polling — a materially different
      shape. Decide with a measurement, not a guess.
- [ ] *Later*: a full `run_plan_check(overrides)` covering accumulation +
      decumulation needs that repo's `main.py` orchestration extracted into a
      callable `run_plan()`. Not required for the first useful tools.

This is also where financial position and goals comes from — not a document.
The target portfolio value and the assumptions behind it (withdrawal rate,
confidence, tax treatment) are computed here from hypotheses that already live
as config in `portfolio-lifecycle-simulator`; see the decision table above.

## Phase 5 — Writing documents (later)

The first tool with side effects. Everything before it is read-only.

- [ ] **Move the career plan's source of truth to markdown.** Editing an
      existing `.docx` in place means walking XML and preserving styles; editing
      markdown is plain text and the change is reviewable as a diff. Generate
      `.docx` from markdown when a formatted copy is wanted — one-way, boring.
- [ ] **Propose, don't overwrite.** The tool returns a revised section, or
      writes `career_plan_<date>.md` beside the original. The original is never
      modified without an explicit human step. This document is the most
      valuable artifact in `data/personal/`.
- [ ] Annotate the tool truthfully: not `readOnlyHint`; set `destructiveHint`
      according to what it actually does.
- [ ] Tests: original file untouched; proposal written where expected; refuses
      to write outside the allowed directory.

## Phase 6 — Packaging

- [x] CI workflow (`pytest` on push) — `.github/workflows/ci.yml`. Runs on push
      and PR to `main`: checkout, `setup-python` 3.14, `pip install -r
      requirements-dev.txt`, `pytest tests/`. No secrets needed - verified by
      cloning to a scratch dir with a fully clean environment (`env -i`, no
      `.env`, no `config.toml`, no `.secrets/`) and a fresh venv: all 79 tests
      passed, exactly what CI will see.
- [ ] README: architecture, quickstart, example transcript showing the agent
      calling tools.
- [ ] Demo path: one command answering a real question end to end.
- [ ] Public GitHub portfolio: `data/personal/Public_Portfolio_*.docx` with a
      `Repo | What it is | What it demonstrates` table — same
      `load_personal_context()` mechanism as the CV/career plan, no code
      change needed. A dated **snapshot**, not live data, deliberately: a
      GitHub API tool would add a dependency and a round-trip for something
      that changes monthly, when what matters for career conversations is what
      each repo *demonstrates*, not its star count.

---

## Non-goals

- HTTP transport, multi-user support, or hosting anything.
- MCP Resources for the CV/career plan (see decision table).
- Write access to the spreadsheet — read-only, deliberately.
