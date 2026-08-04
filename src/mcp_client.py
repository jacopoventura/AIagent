"""MCP client: spawns one MCP server over stdio and adapts its session to plain
data - an Anthropic-format tool list and a string-returning call - so the tool
loop in agent.py never has to know MCP exists. Mirrors the reason `ToolExecutor`
in agent.py depends only on a `(name, arguments) -> str` signature: transport
stays swappable and testable in isolation.
NOTE: this is just a connection interface between agent, which must be asynchronous,
and any of the mcp_servers where the tools are defined.
That's why mcp_client.py must be generalistic to be able to run with any mcp_server.
"""
import asyncio
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from types import TracebackType

from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import MCPError

from src.agent import ToolExecutorError

SHEETS_SERVER_SCRIPT = Path(__file__).resolve().parent / "sheets_server.py"

# Async server: it can listen while waiting for the answer
class McpClient:
    """
    One live connection to one MCP server subprocess. An async context manager:
    the subprocess and session exist only between __aenter__ and __aexit__.

    Usage:
        async with McpClient(Path("src/sheets_server.py")) as client:
            tools = await client.list_tools()
            result = await client.call_tool("read_portfolio_overview", {})
    """

    def __init__(self, server_script: Path, args: list[str] | None = None) -> None:
        self._server_params = StdioServerParameters(command=sys.executable, args=[str(server_script), *(args or [])])
        self._exit_stack = AsyncExitStack()
        self._session: ClientSession | None = None

    async def __aenter__(self) -> "McpClient":
        read_stream, write_stream = await self._exit_stack.enter_async_context(stdio_client(self._server_params))
        self._session = await self._exit_stack.enter_async_context(ClientSession(read_stream, write_stream))
        await self._session.initialize()
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        await self._exit_stack.aclose()
        self._session = None

    def _require_session(self) -> ClientSession:
        if self._session is None:
            raise RuntimeError("McpClient is not connected - use it as `async with McpClient(...) as client:`.")
        return self._session

    async def list_tools(self) -> list[dict]:
        """
        :return: this server's tools in Anthropic's tool-schema format
                 (name, description, input_schema), ready for `AiAgent(tools=...)`.
        """
        result = await self._require_session().list_tools()
        return [
            {"name": tool.name, "description": tool.description, "input_schema": tool.input_schema}
            for tool in result.tools
        ]

    async def call_tool(self, name: str, arguments: dict) -> str:
        """
        Execute one tool call and flatten its result to text - the shape
        AiAgent's `ToolExecutor` protocol expects.

        Two distinct failure shapes reach here, handled differently. A tool that
        raised, was passed malformed arguments, or doesn't exist is still a normal
        MCP response - the server reports it via CallToolResult.is_error, and it
        becomes tool_result text the model can reason about. A dead connection
        (server crashed, process killed) is not something the model can reason
        about, so it's raised as ToolExecutorError instead - a whole-turn failure
        for AiAgent.run() to handle, same as an Anthropic API error.
        :param name: tool name, as returned by list_tools().
        :param arguments: tool arguments, as decided by the model.
        :return: concatenated text content; prefixed with "[tool error]" if the
                 server reported failure rather than raising a transport error.
        :raises ToolExecutorError: if the connection itself fails (e.g. the server
                 process died mid-session) rather than the tool call completing.
        """
        try:
            result = await self._require_session().call_tool(name, arguments)
        except MCPError as e:
            raise ToolExecutorError(f"MCP connection failed calling '{name}': {e}") from e
        text = "\n".join(block.text for block in result.content if isinstance(block, types.TextContent))
        return f"[tool error] {text}" if result.is_error else text

# For test only
async def main() -> None:
    """Manual smoke test against the real sheets_server.py subprocess - connect,
    list tools, call each one - so the lifecycle can be eyeballed end to end
    without a mocked ClientSession. Needs a real config.toml; not run by pytest."""
    async with McpClient(SHEETS_SERVER_SCRIPT) as client:
        tools = await client.list_tools()
        print(f"Connected. {len(tools)} tool(s) available:")
        for tool in tools:
            print(f"  - {tool['name']}: {tool['description']}")

        for tool in tools:
            print(f"\nCalling {tool['name']}()...")
            result = await client.call_tool(tool["name"], {})
            preview = result if len(result) <= 200 else result[:200] + "..."
            print(preview)


if __name__ == "__main__":
    asyncio.run(main())
