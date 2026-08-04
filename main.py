"""Entrypoint: loads the API key from .env and runs the interactive AiAgent chat loop."""
import asyncio
import os

from dotenv import load_dotenv

from src.agent import AiAgent
from src.context import generate_personal_career_and_finance_plan

load_dotenv()
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
if not ANTHROPIC_API_KEY:
    raise SystemExit("API key not provided")


if __name__ == '__main__':
    # No MCP tools wired in yet - see TODO.md Phase 2 "client exercises the full
    # lifecycle". This just keeps the entrypoint runnable after AiAgent went async.
    agent = AiAgent(api_key=ANTHROPIC_API_KEY,
                    system_prompt=generate_personal_career_and_finance_plan())
    asyncio.run(agent.run())
