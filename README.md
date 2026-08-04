# AIagent

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

## License

[MIT](LICENSE.md)
