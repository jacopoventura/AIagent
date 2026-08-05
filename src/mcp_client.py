"""MCP client: spawns one MCP server over stdio and adapts its session to plain
data - an Anthropic-format tool list and a string-returning call - so the tool
loop in agent.py never has to know MCP exists. Mirrors the reason `ToolExecutor`
in agent.py depends only on a `(name, arguments) -> str` signature: transport
stays swappable and testable in isolation.
NOTE: this is just a connection interface between agent, which must be asynchronous,
and any of the mcp_servers where the tools are defined.
That's why mcp_client.py must be generalistic to be able to run with any mcp_server.

Client connection lifecycle:
- __aenter__ doesn't send communication itself — it does the one-time setup that makes communication
  possible afterward: spawn the subprocess, open the stdio pipes, run the handshake. It runs exactly
  once, when you enter the async with block.

- After that, you can call call_tool(), list_tools(), get_prompt() as many times as you want — each
  of those is one request/response round trip over the already-open connection. None of them touch
  __aenter__/__aexit__ at all.

- __aexit__ doesn't terminate a request — it terminates the connection (closes the pipes, lets the
  subprocess exit). It also runs exactly once, when the whole async with block ends — not after each
  call.

  The object is only usable *between* those two calls - that IS the "scope":
  outside an `async with` block, there is no session (or, for the group, no live
  clients) to talk to, which is why `_require_session()` raises a clear
  RuntimeError instead of silently touching `None`.

    MAIN PROCESS (this file)                  SERVER SUBPROCESS (e.g. sheets_server.py)
    McpClient(script)
      __aenter__() -- spawn + stdio pipes -->  process starts
                    -- initialize() ------->   handshake
      [connected: list_tools / call_tool / get_prompt, each a stdio round trip]
      __aexit__()   -- close pipes -------->   process sees EOF on stdin, exits

McpClientGroup - what it is, why it exists, and how it differs from McpClient:
  This project talks to more than one server (today: sheets; later: also a
  simulator, in another repo). McpClientGroup holds several McpClient
  connections at once and makes them *look like one* to the rest of the code -
  it exposes the exact same list_tools()/call_tool()/get_prompt() shape as a
  single McpClient, so nothing outside this file (agent.py, main.py) needs to
  know or care how many servers are actually running.

  Differences from McpClient:
  - McpClient talks to exactly one server. McpClientGroup talks to several,
    and for every tool/prompt name it remembers which server actually owns
    it (a routing table built once, at connect time), so a call for that
    name is sent to the right subprocess.
  - McpClientGroup decides what to do when a server can't connect: warn and
    carry on with the rest, rather than stopping everything - a dead
    simulator should not block a portfolio question - unless *no* server
    connects at all, in which case it gives up (ToolExecutorError).
  - McpClientGroup also catches a configuration mistake McpClient has no way
    to know about on its own: two servers defining the same tool or prompt
    name. That is a bug to fix, not something to guess at silently, so it
    raises ServerCollisionError naming both servers.

  In simple words - where does the group actually do its work? All of it
  happens inside McpClientGroup.__aenter__, in two steps, one time only:
  1. First, try to connect to each server, one after another. If a server
     fails to start, that is fine - just skip it and keep going with the
     others. (Not done "all at once": each server's connection ties an
     internal resource to the task that opened it, and closing it later from
     a different task breaks - verified against the real sheets_server.py
     subprocess, not guessed. So this is sequential on purpose.)
  2. Then, for every server that did connect, ask it "what tools and
     prompts do you have?" and write down which server has which one. That
     written-down list is what call_tool() and get_prompt() use later to
     send each request to the right server.
  Nothing else does this collecting. It all happens once, right there.
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
        """
        Enables the connection to the MCP server and starts it.
        :raises ToolExecutorError: if the server fails to start or the handshake
                 fails (e.g. it crashes before completing `initialize()`) - the
                 same transport-failure signal call_tool/get_prompt raise once
                 connected, kept consistent so callers only ever handle one type.
        """
        try:
            read_stream, write_stream = await self._exit_stack.enter_async_context(stdio_client(self._server_params))
            self._session = await self._exit_stack.enter_async_context(ClientSession(read_stream, write_stream))
            await self._session.initialize()
        except MCPError as e:
            # __aenter__ raising means the `async with` protocol will never call
            # __aexit__, so anything already entered onto the stack (e.g. the
            # subprocess, if stdio_client succeeded but initialize() then failed)
            # is our own responsibility to tear down here - left alone, it's
            # orphaned for the garbage collector to find later, in whatever task
            # happens to be running by then, which is what produced the disorderly
            # "cancel scope in a different task" noise this fixes.
            await self._exit_stack.aclose()
            raise ToolExecutorError(f"MCP server failed to start: {e}") from e
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        """Ends the connection to the MCP server."""
        await self._exit_stack.aclose()
        self._session = None

    def _require_session(self) -> ClientSession:
        if self._session is None:
            raise RuntimeError("McpClient is not connected - use it as `async with McpClient(...) as client:`.")
        return self._session

    # list_tools / call_tool / list_prompts / get_prompt (below) all share the same
    # three-step shape - that is why they look so simple and repetitive. This class
    # is a thin *adapter*, not business logic, so that is expected:
    #   1. Guard    - _require_session() gets the live session, or raises a clear
    #                 RuntimeError if not connected.
    #   2. Delegate - await the matching call on the SDK's ClientSession. This is
    #                 the real work: one request/response round trip over stdio to
    #                 the server subprocess. The MCP SDK already implements the
    #                 wire protocol; there is nothing custom to do here.
    #   3. Adapt    - convert the SDK's own typed result into the plain shape the
    #                 rest of this codebase expects (a list[dict], or a str), so
    #                 agent.py never has to import or know anything about MCP.
    #
    #   method         calls (step 2)            SDK returns          adapted to (step 3)
    #   list_tools     session.list_tools()       ListToolsResult      list[dict]: name/description/input_schema
    #   list_prompts   session.list_prompts()     ListPromptsResult    list[dict]: name/description
    #   call_tool      session.call_tool(...)     CallToolResult       str, "[tool error] ..." if is_error
    #   get_prompt     session.get_prompt(...)    GetPromptResult      str: joined text of the message(s)
    #
    # call_tool and get_prompt also wrap step 2 in try/except MCPError, re-raising
    # as ToolExecutorError; list_tools/list_prompts do not. Not an oversight: those
    # two are (today) only ever called once, right after connecting, while
    # call_tool/get_prompt get called throughout a whole conversation - so "did the
    # server die since I connected?" is a real question every single call has to
    # answer, not just the first one.
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

    async def list_prompts(self) -> list[dict]:
        """
        List of all available prompts.
        :return: this server's prompts as name/description pairs, for building a prompt routing table.
        """
        result = await self._require_session().list_prompts()
        return [{"name": p.name, "description": p.description} for p in result.prompts]

    async def get_prompt(self, name: str) -> str:
        """
        Fetch a named prompt and flatten its message(s) to text - the shape
        AiAgent's `PromptResolver` protocol expects, and the turn content that
        replaces the literal "/name" slash command in run().

        Unlike call_tool, there's no soft "is_error" outcome here: an unknown
        prompt name is itself a transport-level failure (MCPError), same as a
        dead connection, so both raise ToolExecutorError - there's no tool_result
        equivalent for "you asked for a prompt that doesn't exist".
        :param name: prompt name, as registered via @server.prompt() (defaults to
                     the function name), without the leading "/".
        :return: concatenated text content of the prompt's message(s).
        :raises ToolExecutorError: if the prompt doesn't exist or the connection fails.
        """
        try:
            result = await self._require_session().get_prompt(name)
        except MCPError as e:
            raise ToolExecutorError(f"MCP connection failed fetching prompt '{name}': {e}") from e
        return "\n".join(
            message.content.text for message in result.messages if isinstance(message.content, types.TextContent)
        )

class McpClientGroup:
    """
    Owns several McpClient connections and presents the same list_tools/call_tool/
    get_prompt interface as a single McpClient - the agent depends only on that
    shape, so a group of servers is a drop-in replacement for one. An async
    context manager, like McpClient: the subprocesses and sessions exist only
    between __aenter__ and __aexit__.

    Usage:
        clients = {"sheets": McpClient(SHEETS_SERVER_SCRIPT)}
        async with McpClientGroup(clients) as group:
            tools = await group.list_tools()
            result = await group.call_tool("read_portfolio_overview", {})
    """

    def __init__(self, clients: dict[str, McpClient]) -> None:
        self._clients = clients  # name -> McpClient, connect order
        self._exit_stack = AsyncExitStack()
        self._tool_owner: dict[str, tuple[str, McpClient]] = {}
        self._prompt_owner: dict[str, tuple[str, McpClient]] = {}
        self._tools: list[dict] = []

    async def __aenter__(self) -> "McpClientGroup":
        """
        Enables the connection to the MCP server and starts it.
        Connects to every configured server, one after another. A server that
        fails to connect is warned about and skipped rather than treated as
        fatal - a dead simulator should not block portfolio questions. Only if
        *none* connect is that fatal.

        Deliberately sequential, not concurrent via asyncio.gather: gather runs
        each client.__aenter__() in its own asyncio Task, but McpClient.__aenter__
        opens an anyio task group (inside stdio_client) that is permanently tied
        to whichever task it was opened in. This group's __aexit__ later closes
        that same McpClient from *this* task, not the short-lived gather task -
        and anyio refuses to close a task group from a different task than the
        one that opened it ("cancel scope in a different task"), verified
        empirically against the real sheets_server.py subprocess. Real concurrent
        connecting would need each server to own a long-lived task for its whole
        connection lifetime, not just its connect step - meaningfully more
        machinery than a couple of local subprocess servers are worth.

        Once connected, builds the tool/prompt routing tables from each live
        server's list_tools()/list_prompts(). Two servers defining the same tool
        or prompt name is a configuration error, not something to resolve
        silently - raised as ServerCollisionError, naming both servers.
        :raises ToolExecutorError: if no server could be connected.
        :raises ServerCollisionError: if two connected servers define the same
                 tool or prompt name.
        """
        # Step 1: connect to each server in turn. A failure is warned about and
        # skipped, not fatal - see the "deliberately sequential" note above for
        # why this isn't asyncio.gather.
        live: dict[str, McpClient] = {}
        for name, client in self._clients.items():
            try:
                await client.__aenter__()
            except Exception as e:  # pylint: disable=broad-except
                print(f"[warning] could not connect to MCP server '{name}': {e}")
                continue
            self._exit_stack.push_async_exit(client.__aexit__)
            live[name] = client

        if not live:
            raise ToolExecutorError("No MCP servers could be connected.")

        # Step 2: ask each connected server what it can do, and write down who has what.
        try:
            for name, client in live.items():
                for tool in await client.list_tools():
                    self._register(self._tool_owner, tool["name"], name, client)
                    self._tools.append(tool)
                for prompt in await client.list_prompts():
                    self._register(self._prompt_owner, prompt["name"], name, client)
        except Exception:
            # __aenter__ raising means the `async with` protocol will never call
            # __aexit__, so the servers already connected above - tracked only on
            # self._exit_stack at this point - would otherwise leak. Broad on
            # purpose: both our own ServerCollisionError and an MCPError from a
            # server dying between connect and here must still trigger cleanup.
            await self._exit_stack.aclose()
            raise
        return self

    @staticmethod
    def _register(owner: dict[str, tuple[str, McpClient]], item_name: str, server_name: str, client: McpClient) -> None:
        """
        Record `item_name` (a tool or prompt name) as belonging to `server_name`
        in `owner` (one of self._tool_owner / self._prompt_owner).
        :raises ServerCollisionError: if item_name is already owned by a different server.
        """
        if item_name in owner and owner[item_name][0] != server_name:
            other_name, _ = owner[item_name]
            raise ServerCollisionError(f"'{item_name}' is defined by both '{other_name}' and '{server_name}' - rename one.")
        owner[item_name] = (server_name, client)

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        """Ends the connection to the MCP server."""
        await self._exit_stack.aclose()

    async def list_tools(self) -> list[dict]:
        """
        :return: the merged tool list from every connected server, in Anthropic's
                 tool-schema format (name, description, input_schema), ready for
                 `AiAgent(tools=...)`. Cached from __aenter__ - no I/O here.
        """
        return self._tools

    async def call_tool(self, name: str, arguments: dict) -> str:
        """
        Route a tool call to whichever connected server owns `name`.
        :param name: tool name, as returned by list_tools().
        :param arguments: tool arguments, as decided by the model.
        :return: the owning server's result text (see McpClient.call_tool).
        :raises ToolExecutorError: if `name` isn't owned by any connected server,
                 or if the owning server's connection has since failed.
        """
        if name not in self._tool_owner:
            raise ToolExecutorError(f"Unknown tool '{name}' - not offered by any connected server.")
        _, client = self._tool_owner[name]
        return await client.call_tool(name, arguments)

    async def get_prompt(self, name: str) -> str:
        """
        Route a prompt resolution to whichever connected server owns `name`.
        :param name: prompt name, without the leading "/" - the slash-command
                     check itself happens in agent.py's run(), not here.
        :return: the owning server's resolved prompt text (see McpClient.get_prompt).
        :raises ToolExecutorError: if `name` isn't owned by any connected server,
                 or if the owning server's connection has since failed.
        """
        if name not in self._prompt_owner:
            raise ToolExecutorError(f"Unknown prompt '{name}' - not offered by any connected server.")
        _, client = self._prompt_owner[name]
        return await client.get_prompt(name)

class ServerCollisionError(ToolExecutorError):
    """Two connected servers define the same tool or prompt name - a configuration
    error (you own both servers, don't collide), not a transport failure. Subclassed
    from ToolExecutorError so main.py's existing except clause catches it unchanged."""



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
