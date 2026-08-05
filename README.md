# AIagent

[![CI](https://github.com/jacopoventura/AIagent/actions/workflows/ci.yml/badge.svg)](https://github.com/jacopoventura/AIagent/actions/workflows/ci.yml)
[![python](https://img.shields.io/badge/python-3.14-blue.svg)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-2.0-663399.svg)](https://modelcontextprotocol.io/)
[![license](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE.md)

A command-line Claude agent that connects to **several MCP servers at once**,
merges their tools, routes each call to the server that owns it, and keeps a
conversation going indefinitely through automatic summarization.

It exists to answer one kind of question: **career and financial planning
together**, because they are one problem. Earnings trajectory determines what a
portfolio can become; the portfolio target determines what the career must
deliver, and by when.

Two servers are wired in today — a Google Sheets reader (in this repository)
and a 15,000-line Monte Carlo simulation engine (in
[portfolio-lifecycle-simulator](https://github.com/jacopoventura/portfolio-lifecycle-simulator)).

---

## What it looks like

```text
You: what portfolio do I need to cover €2,000/month?

  → financial_planning.accumulation_projection()
    using saved plan: 15y horizon, 2.8% withdrawal rate, 50% bonds, 2.3% inflation

Covering €2,000/month (today's money) from 2041 needs €1.32M at retirement
under Italian tax residence, against €1.41M under German.

You're currently tracking to €1.19M at P50, so the income target is reached
with 88% confidence rather than the 95% you've set as the bar. Raising monthly
contributions to €5,200 closes it.

Assumptions applied: withdrawal 2.8%/yr, inflation 2.3%, stocks/bonds 50/50,
tax residence IT. None were overridden in this call.
```

Figures are illustrative. The point is the shape: the agent knows the user's
saved assumptions, calls the tool rather than reaching for a rule of thumb, and
reports which assumptions it applied.

---

## Architecture

```
AIagent (MCP client)                      portfolio-lifecycle-simulator
│                                         (separate repository)
├── system prompt
│     └── data/personal/*.docx  ─ CV, career plan (local, gitignored)
│
├── tool loop
│     ├── stdio ──→ sheets server ─────→ Google Sheets (read-only)
│     └── stdio ──────────────────────→ financial planning server
│                                              └──→ Monte Carlo engine
│
└── memory + summarization
```

**Three kinds of knowledge, deliberately handled differently.**

| | Mechanism | Why |
|---|---|---|
| Career history, plan, targets | Static context in the system prompt | Both the *subject* of the conversation and the frame for every answer — small, always relevant, unchanging within a session. As a tool the model would have to *decide* to fetch it, and often wouldn't. |
| Current portfolio and net worth | MCP tool (Google Sheets) | Changes over time; fetched on demand so answers reflect today's figures. |
| Forward-looking scenarios | MCP tool (simulation engine) | Parameterised and computationally expensive — exactly what tools are for. |

**Why two servers rather than one.** The simulator server ships inside the
repository of the engine it wraps, so it versions with that engine and anyone
cloning it can drive the model conversationally. The sheets server has no other
consumer and lives here. Both speak stdio: the client spawns each as a
subprocess, which keeps the system local, single-user, and free of ports and
network auth.

**Privacy by design.** CV, career plan, spreadsheet IDs and service-account
credentials live only in gitignored local files, with committed `*.example`
templates. The public repository demonstrates the architecture; personal data
never leaves the machine. A pre-commit hook (`hooks/`) blocks personal files
and credential patterns from entering history.

---

## How the client works

**One client, many servers.** `McpClient` owns exactly one connection to one
server — a dedicated link, never shared. `McpClientGroup` holds several of them
and presents the same three methods (`list_tools`, `call_tool`, `get_prompt`),
so the agent treats a group of servers exactly as it treats one. The agent
imports nothing from MCP; it depends only on a `(name, arguments) -> str`
protocol, which keeps the transport swappable and the tool loop testable
without a subprocess.

**At startup**, the group connects to each server in turn, asks what tools and
prompts it offers, and records who owns what. A server that fails to start is
skipped with a warning rather than killing the session — a dead simulator
shouldn't block spreadsheet questions. Two servers offering the same tool name
is a configuration error, and it raises rather than silently guessing.

**Per turn**, the loop is: call Claude → if the response requests tools,
execute each and append the results → call again → repeat until the model
stops, with an iteration cap so a confused model can't loop forever. Memory is
snapshotted at the start of each turn and truncated back on failure, because a
tool turn appends several messages and a partial one is invalid.

**Errors are split by what the model can do about them.** A tool that raised,
got bad arguments, or doesn't exist is a successful MCP exchange reporting a
failure: it becomes `tool_result` text the model can read and retry from. A
dead connection is not something the model can reason its way out of, so it
fails the whole turn.

**Prompts** are user-controlled. Typing `/portfolio` resolves the server-defined
prompt and sends its text as the turn's content — the model still decides which
tools to call.

---

## Requirements

- Python 3.14+
- An [Anthropic API key](https://console.anthropic.com/) — the agent runs on
  `claude-haiku-4-5`
- For the sheets server: a Google service-account key and a spreadsheet shared
  with it

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp config.example.toml config.toml     # server list, sheet ID, tab names
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env
```

Personal context is optional: drop your CV and career-plan `.docx` files into
`data/personal/` (gitignored) and the agent loads them into its system prompt
at startup — headings and tables included. See `context.example.md` for how to
write them so extraction captures them well.

## Usage

```bash
python main.py
```

Chat normally. `/portfolio` invokes a server-defined prompt. `exit`, `quit` or
`bye` ends the session.

## Tests

```bash
pip install -r requirements-dev.txt
pytest tests/
```

All Claude API calls and MCP sessions are mocked, so the suite runs without an
API key, network access, or a live server — and costs nothing.

Optionally enable the pre-commit hook (privacy guard → syntax → tests):

```bash
git config core.hooksPath hooks
```

## Project structure

```
main.py                              entrypoint: config, servers, agent, chat loop
src/
  agent.py                           AiAgent: Claude calls, tool loop, memory, summarization
  context.py                         loads CV / career plan from .docx into the system prompt
  mcp_client.py                      McpClient (one server) and McpClientGroup (many)
  sheets_server.py                   MCP server: read-only Google Sheets access
tests/                               7 modules, ~1,400 lines; all external calls mocked
hooks/
  pre-commit                         privacy guard, syntax check, test suite
  privacy-guard.sh                   blocks personal files and credential patterns from git
config.example.toml                  server list and sheet configuration template
context.example.md                   how to write the CV / career-plan documents
TODO.md                              build plan and the reasoning behind each decision
```

---

## Lessons learned

Things that were not obvious from the documentation, and cost real debugging
time. Recorded because the reasoning transfers better than the code.
The server-side counterparts are in
[portfolio-lifecycle-simulator](https://github.com/jacopoventura/portfolio-lifecycle-simulator#lessons-learned--serving-an-engine-over-mcp)'s
README.

### Tool descriptions are prompt engineering, not documentation

Asked *"what portfolio covers €2,000/month?"*, the agent replied with four
clarifying questions and then quoted the 4% rule — landing ~30% below what the
engine computes at the user's configured 2.8% withdrawal rate. It had the tool.
It didn't call it.

Two causes, both in what the model could see:

- Parameters were all `float | None = None`, meaning *"omit to use the saved
  plan"* — but nothing said so. Ten optional nulls look like missing
  information, so it asked.
- They reached the model as bare types: `{"anyOf": [number, null], "title":
  "Bonds Percentage"}`. No units, no ranges. Is `bonds_percentage` `50` or
  `0.5`?

The fix was entirely in text the model reads, not in logic: state the fallback
explicitly in the docstring; give every parameter a `Field(description=...)`
with units and the `0-100 (not 0-1)` traps; **interpolate the live config
values into the description at server startup** so the model sees
`withdrawal rate 2.8%/yr, 15y horizon` before deciding whether to ask; and
return `assumptions_used` in the result so the answer can state what was
applied rather than applying it silently.

The general lesson: when an agent behaves badly, the bug is usually in what it
was told, not in what it can do. Rules that apply to *every* tool ("prefer
calling a tool over asking") belong in the system prompt — in a tool
description they get duplicated across every tool and re-sent on every request.

### Don't make the model derive what a tool can hand it

A related failure: the agent computed the user's age from a sentence in the
career plan that read "…by 2036, age 49", took 49 as the *current* age, and was
wrong by a decade. Nothing in the context stated the current age plainly. Any
quantity a tool already knows — ages, horizons, derived dates — should be
returned as data rather than inferred from prose.

### MCP servers are not always safe to connect concurrently

Connecting several with `asyncio.gather` fails: `stdio_client` opens an anyio
task group permanently bound to the task that opened it, and `gather` runs each
`__aenter__` in its own short-lived task. Closing later from the owning
`McpClientGroup` task raises *"cancel scope in a different task"*. Verified
against the real subprocess, not guessed. Sequential connection is the right
answer for two local servers; genuine concurrency would need each server to own
a long-lived task for its whole connection lifetime.

### "Connection closed" never explains itself

When a stdio server dies during startup, the client reports only *"Connection
closed"*. The real cause — missing file, failed import, wrong interpreter — is
on the subprocess's stderr, printed immediately *above* that message. Always
read the line before.

### Cross-repo servers need their own interpreter and working directory

The simulator server lives in a sibling repository and imports a scientific
stack this one doesn't have. Launching it naively fails three ways: wrong
Python (`sys.executable` is this repo's venv), wrong `sys.path` (Python adds
the *script's* directory, not the repo root — so that server must sit at its
repo root to import its siblings), and wrong working directory (the engine
resolves `config.toml` relatively). All three are solved in configuration —
`StdioServerParameters` accepts `command`, `cwd` and `env` — not in code.

### stdout belongs to the protocol

A stdio server speaks JSON-RPC over stdout. Any `print()` from wrapped code
corrupts the stream, silently and confusingly. Every call into the simulation
engine is wrapped in `contextlib.redirect_stdout`.

---

## License

[MIT](LICENSE.md)
