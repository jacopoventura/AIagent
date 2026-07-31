# AIagent

An AI agent built on the Claude API. The current form is an interactive command-line
chat agent with automatic conversation summarization; the goal is to extend it into
an agent that interprets results produced by a financial planning tool.

## Requirements

- Python 3.14+
- An [Anthropic API key](https://console.anthropic.com/) (Settings → API Keys)

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
main.py              # entrypoint: loads .env, creates the agent, runs the chat loop
src/
  agent.py            # AiAgent class: Claude API calls, memory, summarization
tests/
  test_check_memory_for_summary.py   # unit tests for the summarization logic
```

## Running tests

```bash
pip install -r requirements-dev.txt
pytest tests/
```

Tests mock all Claude API calls, so they run without a real API key or network access
and incur no cost.

## License

[PolyForm Strict License 1.0.0](LICENSE.md). The source is viewable, but use,
modification, and redistribution require the licensor's permission.
