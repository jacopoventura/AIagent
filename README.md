# AIagent

[![CI](https://github.com/jacopoventura/AIagent/actions/workflows/ci.yml/badge.svg)](https://github.com/jacopoventura/AIagent/actions/workflows/ci.yml)
[![python](https://img.shields.io/badge/python-3.14-blue.svg)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-2.0-663399.svg)](https://modelcontextprotocol.io/)
[![license](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE.md)

A personal Claude-based agent for thinking through **career and financial
planning together** — because they are one problem. Earnings trajectory
determines what the portfolio can become; the portfolio target determines how
much the career has to deliver, and by when.

The agent reasons over the user's own situation: career history and plan as
standing context, live portfolio data and forward-looking simulations through
MCP tools.

Today it is an interactive command-line chat agent with conversation memory and
automatic summarization. The MCP layer described below is in progress; see
[`TODO.md`](TODO.md) for the build plan.

## Architecture

```
AIagent (MCP client)                      portfolio-lifecycle-simulator
│                                         (separate repository)
├── system prompt
│     └── context/profile.md  ─ goals, constraints, plan (local, gitignored)
│
├── tool loop
│     ├── stdio ──→ sheets MCP server ──→ Google Sheets (read-only)
│     └── stdio ──────────────────────→ simulator MCP server
│                                              └──→ Monte Carlo engine
└── memory + summarization
```

**Three kinds of knowledge, deliberately handled differently.**

| | Mechanism | Why |
|---|---|---|
| Career history, plan and targets; financial goals and constraints | Static context in the system prompt | Both the *subject* of the conversation and the frame for every answer — small, always relevant, unchanging within a session. As a tool the model would have to *decide* to fetch it, and often wouldn't. |
| Current portfolio and net-worth development | MCP tool (Google Sheets) | Changes over time; fetched on demand so answers reflect today's figures. |
| Forward-looking scenarios | MCP tool (simulation engine) | Parameterised and computationally expensive — exactly what tools are for. |

Questions it is built to answer span both domains: *what salary do I need by when
to retire at the target age?*, *does taking a lower-paid but faster-growing role
still reach the target?*, *how much does another year of work change the
required portfolio?*

**Why two servers rather than one.** The simulator server ships with the engine
it wraps, in that engine's repository, so it versions alongside the model it
exposes and anyone cloning that project can drive it conversationally. The sheets
server has no other consumer and lives here. Both speak stdio: the client spawns
each as a subprocess, which keeps the whole system local, single-user, and free
of ports and network auth.

**Privacy by design.** The CV, career plan, spreadsheet identifiers and service
account credentials live only in gitignored local files, with committed
`*.example` templates. The public repository demonstrates the architecture;
personal data never leaves the machine. A pre-commit hook (`hooks/`) blocks
personal files and credential patterns from entering history.

## Client and server

MCP (Model Context Protocol) splits "the agent" and "the tools it can call" into
two separate processes that talk to each other:

- **The client** is this repository. It runs the chat loop, talks to Claude, and
  — when Claude decides a tool is needed — routes that request to whichever
  server owns it. It holds no domain logic of its own.
- **A server** is a small, focused process that owns one capability and nothing
  else: the sheets server (`src/sheets_server.py`) knows how to read the
  portfolio spreadsheet; the simulator server (in `portfolio-lifecycle-simulator`)
  knows how to run the Monte Carlo engine. Neither knows Claude exists — each
  only speaks MCP.

The client spawns each server as a subprocess and exchanges requests and
responses with it over stdio (its stdin/stdout — no ports, no network). At
startup the client asks every server "what tools do you have?" and hands that
combined list to Claude; when Claude picks one, the client routes the call to
the server that owns it and returns the result as plain text. That separation
is what lets the simulator evolve independently in its own repository,
versioned with the engine it wraps, while this repository only needs to speak
MCP — never how Monte Carlo simulation works.

**Who speaks first.** In this project, the client always asks and the server
only answers — a server never sends anything the client did not request first.
MCP itself does allow a server to speak first in some cases (a feature called
"sampling," where a server can ask the client's Claude to do something for it),
but this project does not use that feature. Here, it is always: client asks,
server answers.

**Talking to more than one server.** This project can connect to more than one
server — for example, one for the spreadsheet and, later, one for the
simulator. Instead of the rest of the code having to keep track of which
server does what, there is one helper that connects to all of them and then
behaves like a single server from the outside. If one server fails to start,
the helper just skips it and keeps working with the others, so one broken
server does not stop the whole app. If two servers happen to offer the same
tool by mistake, the helper stops and says so clearly, instead of quietly
guessing which one to use.

**One connection, one server.** Each individual connection inside that helper
talks to exactly one server — it is a dedicated link, not a shared one. It
never talks to any other server, and no other connection talks to its server
either. The helper (see "Talking to more than one server" above) is just
several of these one-to-one, dedicated connections held side by side, one per
server, so it can pick the right one for each request.

**When this happens.** All of it happens once, right when the app starts, in
two simple steps: first, try to connect to each server, one after another
(not all at once — connecting each one ties it to a bit of internal state
that breaks if it is closed from somewhere else later, so this project plays
it safe and connects them in turn); then, for every server that connected,
ask it "what can you do?" and write down which server can do what. After
that, whenever the agent wants to use a tool, the app just looks up who owns
it and sends the request straight there.

**The shape every request follows.** Every time the app asks a server
something — "what tools do you have," "run this tool," "what prompts do you
have," "give me this prompt" — it follows the same three steps: make sure the
connection is open, send the request to the server and wait for its reply,
then turn that reply into plain text (or a plain list) so the rest of the app
never has to know anything about MCP itself. Two of these (running a tool,
asking for a prompt) also check whether the server died since connecting,
since those can happen many times during one conversation, not just once at
the very start.

## Requirements

- Python 3.14+
- An [Anthropic API key](https://console.anthropic.com/) (Settings → API Keys)
- To check the remaining credit: [console.anthropic.com/settings/billing](https://console.anthropic.com/settings/billing)

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create a `.env` file in the project root with your API key:

```
ANTHROPIC_API_KEY=sk-ant-your-key-here
```

## Usage

```bash
python main.py
```

Type a message and press enter to chat. Type `exit`, `quit`, or `bye` to end the session.

The agent uses `claude-haiku-4-5` (the cheapest current Claude model) and keeps the
full conversation in memory. Once the conversation grows past a configured number of
answers, the agent automatically summarizes the older part of the conversation and
keeps only the summary plus the most recent answers, so the conversation can continue
indefinitely without the context growing unbounded.

## Project structure

```
main.py                              entrypoint: loads .env, builds the agent, runs the chat loop
src/
  agent.py                           AiAgent: Claude API calls, memory, summarization
tests/
  conftest.py                        shared fixtures
  test_agent.py                      extract_text, and memory integrity when an API call fails
  test_check_memory_for_summary.py   summarization trigger, partitioning and repeat cycles
hooks/
  pre-commit                         privacy guard, syntax check, test suite
  privacy-guard.sh                   blocks personal files and credential patterns from git history
TODO.md                              MCP integration plan and the decisions behind it
```

## Running tests

```bash
pip install -r requirements-dev.txt
pytest tests/
```

Tests mock all Claude API calls, so they run without a real API key or network access
and incur no cost.

Optionally enable the pre-commit hook (privacy guard, syntax check, tests):

```bash
git config core.hooksPath hooks
```

## Lessons learned

Things that were not obvious from the documentation, and cost real debugging
time. Recorded because the reasoning is more transferable than the code.

### Tool descriptions are prompt engineering, not documentation

Asked *"what portfolio covers €2,000/month?"*, the agent replied with four
clarifying questions and then quoted the 4% rule — arriving at €600,000 against
a correct figure nearer €857,000, because the user's configured withdrawal rate
is 2.8%. It had the tool. It didn't call it.

Two causes, both in what the model could see:

- The tool's parameters were all `float | None = None`, meaning *"omit to use
  the user's saved plan"* — but nothing said so. Ten optional nulls look like
  missing information, so it asked.
- The parameters reached the model as bare types: `{"anyOf": [number, null],
  "title": "Bonds Percentage"}`. No units, no ranges. Is `bonds_percentage`
  `50` or `0.5`?

The fix was entirely in text the model reads, not in logic:

1. State the fallback explicitly in the docstring — *"EVERY PARAMETER IS
   OPTIONAL. Omitted parameters use the user's saved plan. Do not ask."*
2. Give every parameter a `Field(description=...)`, including units and the
   `0-100 (not 0-1)` traps.
3. **Interpolate the live config values into the description at server
   startup**, so the model sees `withdrawal rate 2.8%/yr, 15y horizon` before
   deciding whether it needs to ask.
4. Return `assumptions_used` in the result, so the answer can state which
   values were applied rather than applying them silently.

The general lesson: when an agent behaves badly, the bug is usually in what it
was told, not in what it can do. Behavioural rules that apply to every tool
("prefer calling a tool over asking") belong in the system prompt — putting
them in a tool description duplicates them across every tool and re-sends them
on every request.

### MCP servers are not always safe to connect concurrently

Connecting several servers with `asyncio.gather` fails: `stdio_client` opens an
anyio task group that is permanently bound to the task that opened it, and
`gather` runs each `__aenter__` in its own short-lived task. Closing later from
the owning `McpClientGroup` task then raises *"cancel scope in a different
task"*. Verified against the real subprocess, not guessed. Connecting
sequentially is the correct fix for two local servers; genuine concurrency
would require each server to own a long-lived task for its whole connection
lifetime.

### stdout belongs to the protocol

A stdio MCP server speaks JSON-RPC over stdout. Any `print()` from wrapped code
corrupts the stream, and the failure is silent and confusing. Every call into
the simulation engine is wrapped in `contextlib.redirect_stdout`.

### "Connection closed" never explains itself

When a stdio server dies during startup, the client only ever reports
*"Connection closed"*. The real cause — a missing file, a failed import, a
wrong interpreter — is on the subprocess's stderr, printed immediately *above*
that message. Always read the line before.

### Cross-repo servers need their own interpreter and working directory

The simulator server lives in a sibling repository and imports a scientific
stack this repo doesn't have. Three separate failures follow from launching it
naively: the wrong Python (`sys.executable` is this repo's venv), the wrong
`sys.path` (Python adds the *script's* directory, not the repo root — so the
server must sit at that repo's root to import its siblings), and the wrong
working directory (the engine resolves `config.toml` relatively). All three are
solved in configuration — `StdioServerParameters` accepts `command`, `cwd` and
`env` — not in code.

### The error taxonomy a tool call needs

Two failures look alike and must be handled differently. A tool that raised,
got bad arguments, or doesn't exist is a *successful* MCP exchange reporting an
error: it becomes `tool_result` text the model can read and retry from. A dead
connection is not something the model can reason its way out of, so it fails
the whole turn. Conflating them produces an agent that loops forever retrying a
server that no longer exists.

## License

[MIT](LICENSE.md)
