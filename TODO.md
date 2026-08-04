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
| Simulator server location | `portfolio-lifecycle-simulator` repo | The server wraps that engine and versions with it; anyone cloning the simulator can drive it conversationally. |
| Sheets server location | This repo | No other consumer; it is a capability of this agent, not of the engine. |
| Transport | stdio, both servers | Local, single user, client spawns each as a subprocess. No ports, no auth layer. HTTP only earns its complexity when something remote must reach the server. |
| Sheets tool granularity | Two named tools, not `read_sheet(name)` | Named tools carry their own descriptions; the model selects correctly instead of guessing a string argument. |
| Cross-repo coupling | One path in `config.toml` | Machine-specific, gitignored; `config.example.toml` committed. |

---

## Phase 1 — Tool loop in the client (no MCP yet)

Blocked by: nothing. This is the risky part and it is testable in isolation.

- [ ] `ToolExecutor` protocol — `(name, arguments) -> str`. The loop depends on
      this, not on MCP, so the transport can be swapped and tested with a plain
      function.
- [ ] Agentic loop: call → if `stop_reason == "tool_use"`, execute each requested
      tool, append a `tool_result` message, call again → repeat until `end_turn`.
- [ ] `max_tool_iterations` guard so a confused model cannot loop forever.
- [ ] **Memory rollback.** Current code calls `self.__memory.pop()` in each
      `except` branch, which works only because append and call are adjacent. A
      tool turn appends several messages (assistant `tool_use`, user
      `tool_result`, …); one `pop()` leaves a `tool_use` without its matching
      `tool_result`, which the API rejects. Fix: snapshot `len(memory)` at the
      start of the turn, truncate back to it on any failure.
- [ ] **Summarization must handle block content.** `_check_memory_for_summary`
      concatenates `message["content"]` as a string; once tool blocks are in
      memory that content is a *list* and this raises `TypeError`. Add a
      flattening helper.
- [ ] Summarization calls must not offer tools (`use_tools=False`), or the
      summarizer may try to call one.
- [ ] Tests: mock executor, multi-round tool turn, rollback on mid-loop failure,
      iteration cap.

## Phase 2 — Static context

Blocked by: nothing.

- [x] CV and career plan: parsed directly from `.docx` files in `data/personal/`
      (gitignored) on every agent startup — `src/context.py::load_personal_context`
      globs the directory, walks paragraphs *and tables* in document order, maps
      Heading 1/2/3 styles to markdown, renders tables as markdown pipe tables,
      concatenates one section per file. Superseded the original
      `context/profile.md` plan below: the source documents get edited often,
      and a hand-curated copy would drift out of sync with them. Parsing costs
      milliseconds, once, at process start, so "static context" still holds —
      it just gets read fresh each run instead of hand-copied once. A missing
      or unreadable file is skipped with a warning, never crashes startup.
      `context.example.md` now documents how to structure the source documents
      (use real Heading styles, put tabular data in actual tables) instead of
      being a markdown template to fill in.
      Fixed post-launch: the first version read `document.paragraphs` only —
      python-docx keeps table content in a separate `document.tables`
      collection, so every table (salary bands, timelines) was silently
      dropped with no error. Table-aware, order-preserving extraction fixed it.
- [ ] Financial position and goals, public GitHub portfolio, how I want to be
      advised — are not covered by the CV/career-plan docx files and still need
      a source. Decide whether that's a small curated file or folds into the
      system prompt directly.
- [ ] Public portfolio section is a dated **snapshot**, not live data. A GitHub
      API tool would add a dependency and a round-trip to fetch something that
      changes monthly; what matters for career conversations is what each repo
      *demonstrates*, not its star count. Revisit only if staleness bites.
- [x] Load at startup into the system prompt.
- [ ] Prompt caching (`cache_control: ephemeral`) — the block is resent every
      turn otherwise.
- [x] Tests: agent runs with the context file absent (falls back cleanly).

## Phase 3 — Sheets MCP server (first real server)

Blocked by: nothing — deliberately built before the simulator server, which has a
dependency.

- [ ] stdio MCP server in this repo.
- [ ] Auth: **service account, not OAuth user flow**. Create the service account,
      download the JSON key, share the spreadsheet with the service account's
      email. No browser consent, no refresh-token handling, no expiry.
      Key in `.secrets/` (gitignored).
- [ ] Tools: `read_portfolio_development()`, `read_dashboard()`. Both sheets are
      already summary-level, so each returns its sheet whole.
- [ ] Tests: mocked Sheets client; server starts and lists tools; no network.

## Phase 4 — Simulator MCP server

Blocked by: the engine must expose structured output — `--json`, `--fast`,
section selection and `--record` gating (see that repo's `CLAUDE.md` §3).

- [ ] Tools: `run_plan_check(overrides)`, `get_last_summary()`,
      `get_run_history()`.
- [ ] Whitelist the ~12 overridable parameters at the tool boundary, not in the
      CLI.
- [ ] Return *why* a result came out as it did — binding constraint, chosen
      allocation, whether the search hit its bounds — so the model can reason
      about the next call instead of guessing.

### Open question — latency may change the tool contract

A full run takes minutes; a chat loop tolerates seconds. Measure `--fast` first.
If reduced runs still exceed ~30s, `run_plan_check` cannot stay synchronous and
becomes `start_run()` → `get_result(job_id)` polling, which is a materially
different contract. Decide with a measurement, not a guess.

## Phase 5 — Packaging

- [ ] CI workflow (`pytest` on push) — this repo has none yet.
- [ ] README: architecture, quickstart, example transcript showing the agent
      calling tools.
- [ ] Demo path: one command answering a real question end to end.

---

## Non-goals

- HTTP transport, multi-user support, or hosting anything.
- MCP Resources for the CV/career plan (see decision table).
- Write access to the spreadsheet — read-only, deliberately.
