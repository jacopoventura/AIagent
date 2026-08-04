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

- [ ] stdio MCP server in this repo.
- [ ] Auth: **service account, not OAuth user flow**. Create the service account,
      download the JSON key, share the spreadsheet with the service account's
      email. No browser consent, no refresh-token handling, no expiry.
      Key in `.secrets/` (gitignored).
- [ ] Tools: `read_portfolio_development()`, `read_dashboard()`. Both sheets are
      already summary-level, so each returns its sheet whole.
- [ ] **Resource**: sheet metadata (last updated, column schema). Resources are
      *application*-controlled where tools are *model*-controlled — implementing
      both is the only way to internalise that distinction.
- [ ] **Prompt**: a "monthly review" template. User-controlled, the third
      primitive.
- [ ] Client exercises the full lifecycle, not just `call_tool`: initialize
      handshake, capability negotiation, `list_tools` / `list_resources` /
      `list_prompts`, then invocation.
- [ ] Error paths, deliberately: tool raises, server dies mid-session,
      malformed arguments from the model. This is where the agent will actually
      break, and where understanding shows.

*Why more than the minimum:* a client plus one tool-serving server demonstrates
roughly 40% of MCP's surface. Covering all three primitives, the lifecycle and
the failure modes costs about a day more and is the difference between "I made a
tool work" and knowing the protocol — which matters for the certification this
work doubles as preparation for.
- [ ] Tests: mocked Sheets client; server starts and lists tools; no network.

## Phase 3 — Simulator MCP server

Blocked by: nothing, for a **minimal version** — shell out to whatever the
engine already prints today (even unstructured) and return it as-is via a
single tool, e.g. `get_last_summary()`. Claude can work with loosely-structured
text; it just costs more tokens and can't reliably explain *why* a result came
out as it did until the structured version lands. The **full version**
(`--json`, `--fast`, section selection, `--record` gating — see that repo's
`CLAUDE.md` §3) is blocked on that refactor in the sibling repo; upgrade to it
once available rather than waiting for it to start.

This is also where financial position and goals comes from — not a document.
The target portfolio value and the assumptions behind it (withdrawal rate,
confidence, tax treatment) are computed here from hypotheses that already live
as config in `portfolio-lifecycle-simulator`; see the decision table above.

- [ ] Minimal version: shell out to the simulator's current CLI output, return
      it as-is via one tool.
- [ ] Full version — tools: `run_plan_check(overrides)`, `get_last_summary()`,
      `get_run_history()`.
- [ ] Whitelist the ~12 overridable parameters at the tool boundary, not in the
      CLI.
- [ ] Return *why* a result came out as it did — binding constraint, chosen
      allocation, whether the search hit its bounds — so the model can reason
      about the next call instead of guessing. Needs the structured output.

### Open question — latency may change the tool contract

A full run takes minutes; a chat loop tolerates seconds. Measure `--fast` first.
If reduced runs still exceed ~30s, `run_plan_check` cannot stay synchronous and
becomes `start_run()` → `get_result(job_id)` polling, which is a materially
different contract. Decide with a measurement, not a guess.

## Phase 4 — Packaging

- [ ] CI workflow (`pytest` on push) — this repo has none yet.
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
