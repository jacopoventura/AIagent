"""Entrypoint: loads the API key from .env and runs the interactive AiAgent chat loop."""
import os

from dotenv import load_dotenv

from src.agent import AiAgent

load_dotenv()
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
if not ANTHROPIC_API_KEY:
    raise SystemExit("API key not provided")


if __name__ == '__main__':
    agent = AiAgent(api_key=ANTHROPIC_API_KEY)
    agent.run()
