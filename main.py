"""Entrypoint: loads the API key from .env and runs the interactive AiAgent chat loop."""
import asyncio
import os
import tomllib
from pathlib import Path

from dotenv import load_dotenv

from src.agent import AiAgent, ToolExecutorError
from src.context import generate_personal_career_and_finance_plan
from src.mcp_client import McpClient, McpClientGroup

load_dotenv()
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
if not ANTHROPIC_API_KEY:
    raise SystemExit("API key not provided")

CONFIG_PATH = Path(__file__).resolve().parent / "config.toml"


async def main() -> None:
    """Connect to every MCP server listed in config.toml and run the interactive chat loop for one session."""
    with CONFIG_PATH.open("rb") as f:
        server_config = tomllib.load(f)["mcp_servers"]  # raw config data - not connected to anything yet
    # Each McpClient is dedicated to exactly one server - a one-to-one link, not
    # a shared connection - which is why this is called `clients`, not
    # `mcp_servers`: it holds client-side handles, not the servers themselves.
    # No subprocess is spawned yet; that only happens inside McpClientGroup.__aenter__.
    clients = {
        entry["name"]: McpClient(CONFIG_PATH.parent / entry["script"])
        for entry in server_config
    }
    try:
        async with McpClientGroup(clients) as group:
            agent = AiAgent(api_key=ANTHROPIC_API_KEY,
                            system_prompt=generate_personal_career_and_finance_plan(),
                            tools=await group.list_tools(),
                            tool_executor=group.call_tool,
                            prompt_resolver=group.get_prompt)
            await agent.run()
    except ToolExecutorError as e:
        raise SystemExit(f"Could not connect to MCP servers: {e}") from e


if __name__ == "__main__":
    asyncio.run(main())
