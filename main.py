"""Entrypoint: loads the API key from .env and runs the interactive AiAgent chat loop."""
import asyncio
import os

from dotenv import load_dotenv

from src.agent import AiAgent, ToolExecutorError
from src.context import generate_personal_career_and_finance_plan
from src.mcp_client import McpClient, SHEETS_SERVER_SCRIPT

load_dotenv()
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
if not ANTHROPIC_API_KEY:
    raise SystemExit("API key not provided")


async def main() -> None:
    """Connect to the sheets MCP server and run the interactive chat loop for one session."""
    try:
        async with McpClient(SHEETS_SERVER_SCRIPT) as client:
            agent = AiAgent(api_key=ANTHROPIC_API_KEY,
                            system_prompt=generate_personal_career_and_finance_plan(),
                            tools=await client.list_tools(),
                            tool_executor=client.call_tool,
                            prompt_resolver=client.get_prompt)
            await agent.run()
    except ToolExecutorError as e:
        raise SystemExit(f"Could not connect to the sheets MCP server: {e}") from e


if __name__ == "__main__":
    asyncio.run(main())
