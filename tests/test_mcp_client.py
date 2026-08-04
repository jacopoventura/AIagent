"""Tests for McpClient.

ClientSession is always mocked - these tests never spawn a real subprocess.
Focus is the error-path contract: a tool that raised, got bad arguments, or
doesn't exist is a normal MCP response (CallToolResult.is_error) and must come
back as text for the model to reason about; a dead connection is not something
the model can reason about, so it must come back as ToolExecutorError instead -
the transport-agnostic type agent.py's run() catches as a whole-turn failure.
"""
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from mcp import types
from mcp.shared.exceptions import MCPError

from src import mcp_client as mcp_client_module
from src.agent import ToolExecutorError
from src.mcp_client import McpClient


def make_client(session) -> McpClient:
    """Build an McpClient with a pre-connected mock session, bypassing the real
    subprocess/handshake in __aenter__ - these tests exercise the list_tools/
    call_tool adapter logic, not the connection lifecycle itself."""
    client = McpClient(Path("unused_server.py"))
    client._session = session
    return client


class TestNotConnected:
    """_require_session() is what __aenter__ exists to satisfy; using the client
    outside `async with` must fail clearly instead of hitting `None.list_tools()`."""

    def test_list_tools_before_connecting_raises_runtime_error(self):
        client = McpClient(Path("unused_server.py"))

        with pytest.raises(RuntimeError):
            asyncio.run(client.list_tools())

    def test_call_tool_before_connecting_raises_runtime_error(self):
        client = McpClient(Path("unused_server.py"))

        with pytest.raises(RuntimeError):
            asyncio.run(client.call_tool("get_answer", {}))

    def test_get_prompt_before_connecting_raises_runtime_error(self):
        client = McpClient(Path("unused_server.py"))

        with pytest.raises(RuntimeError):
            asyncio.run(client.get_prompt("portfolio"))


class TestConnectionFailure:
    """A server that dies before or during the handshake is a different failure
    window than a dead mid-call connection (TestCallTool below) - found empirically
    by killing a real subprocess at every stage: __aenter__ had no MCPError handling
    at all, so a handshake failure propagated as a raw MCPError instead of the
    ToolExecutorError every other failure path already produces."""

    @staticmethod
    def _patch_failing_handshake(monkeypatch, exception: Exception) -> None:
        @asynccontextmanager
        async def fake_stdio_client(_params):
            yield (AsyncMock(), AsyncMock())

        class FakeSession:
            def __init__(self, _read_stream, _write_stream):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_exc_info):
                return False

            async def initialize(self):
                raise exception

        monkeypatch.setattr(mcp_client_module, "stdio_client", fake_stdio_client)
        monkeypatch.setattr(mcp_client_module, "ClientSession", FakeSession)

    def test_handshake_failure_raises_tool_executor_error_not_mcp_error(self, monkeypatch):
        self._patch_failing_handshake(monkeypatch, MCPError(code=-1, message="Connection closed"))
        client = McpClient(Path("unused_server.py"))

        with pytest.raises(ToolExecutorError):
            asyncio.run(client.__aenter__())


class TestListTools:
    def test_converts_tools_to_anthropic_schema(self):
        session = AsyncMock()
        session.list_tools.return_value = types.ListToolsResult(
            tools=[types.Tool(name="read_x", description="reads x", input_schema={"type": "object", "properties": {}})]
        )
        client = make_client(session)

        result = asyncio.run(client.list_tools())

        assert result == [{"name": "read_x", "description": "reads x", "input_schema": {"type": "object", "properties": {}}}]

    def test_multiple_tools_preserve_order(self):
        session = AsyncMock()
        session.list_tools.return_value = types.ListToolsResult(
            tools=[
                types.Tool(name="tool_a", description="a", input_schema={}),
                types.Tool(name="tool_b", description="b", input_schema={}),
            ]
        )
        client = make_client(session)

        result = asyncio.run(client.list_tools())

        assert [t["name"] for t in result] == ["tool_a", "tool_b"]


class TestCallTool:
    def test_successful_call_returns_text(self):
        session = AsyncMock()
        session.call_tool.return_value = types.CallToolResult(
            content=[types.TextContent(type="text", text="42")], is_error=False
        )
        client = make_client(session)

        result = asyncio.run(client.call_tool("get_answer", {}))

        assert result == "42"

    def test_tool_reported_error_is_returned_as_text_not_raised(self):
        session = AsyncMock()
        session.call_tool.return_value = types.CallToolResult(
            content=[types.TextContent(type="text", text="boom")], is_error=True
        )
        client = make_client(session)

        result = asyncio.run(client.call_tool("broken_tool", {}))

        assert result == "[tool error] boom"

    def test_multiple_text_blocks_are_joined_with_newlines(self):
        session = AsyncMock()
        session.call_tool.return_value = types.CallToolResult(
            content=[types.TextContent(type="text", text="line1"), types.TextContent(type="text", text="line2")],
            is_error=False,
        )
        client = make_client(session)

        result = asyncio.run(client.call_tool("get_answer", {}))

        assert result == "line1\nline2"

    def test_dead_connection_raises_tool_executor_error_not_mcp_error(self):
        session = AsyncMock()
        session.call_tool.side_effect = MCPError(code=-1, message="Connection closed")
        client = make_client(session)

        with pytest.raises(ToolExecutorError):
            asyncio.run(client.call_tool("get_answer", {}))

    def test_tool_executor_error_message_names_the_failed_tool(self):
        session = AsyncMock()
        session.call_tool.side_effect = MCPError(code=-1, message="Connection closed")
        client = make_client(session)

        with pytest.raises(ToolExecutorError, match="get_answer"):
            asyncio.run(client.call_tool("get_answer", {}))

    def test_tool_executor_error_chains_the_original_mcp_error(self):
        session = AsyncMock()
        original = MCPError(code=-1, message="Connection closed")
        session.call_tool.side_effect = original
        client = make_client(session)

        try:
            asyncio.run(client.call_tool("get_answer", {}))
            assert False, "expected ToolExecutorError"
        except ToolExecutorError as e:
            assert e.__cause__ is original


class TestGetPrompt:
    def test_successful_resolution_returns_text(self):
        session = AsyncMock()
        session.get_prompt.return_value = types.GetPromptResult(
            messages=[types.PromptMessage(role="user", content=types.TextContent(type="text", text="Give me a portfolio review."))]
        )
        client = make_client(session)

        result = asyncio.run(client.get_prompt("portfolio"))

        assert result == "Give me a portfolio review."

    def test_multiple_messages_are_joined_with_newlines(self):
        session = AsyncMock()
        session.get_prompt.return_value = types.GetPromptResult(
            messages=[
                types.PromptMessage(role="user", content=types.TextContent(type="text", text="line1")),
                types.PromptMessage(role="user", content=types.TextContent(type="text", text="line2")),
            ]
        )
        client = make_client(session)

        result = asyncio.run(client.get_prompt("portfolio"))

        assert result == "line1\nline2"

    def test_unknown_prompt_raises_tool_executor_error_not_mcp_error(self):
        """Unlike call_tool, there's no soft is_error outcome for prompts - an
        unknown name is itself a transport-level MCPError, same as a dead connection."""
        session = AsyncMock()
        session.get_prompt.side_effect = MCPError(code=-1, message="Unknown prompt: does_not_exist")
        client = make_client(session)

        with pytest.raises(ToolExecutorError):
            asyncio.run(client.get_prompt("does_not_exist"))

    def test_dead_connection_raises_tool_executor_error(self):
        session = AsyncMock()
        session.get_prompt.side_effect = MCPError(code=-1, message="Connection closed")
        client = make_client(session)

        with pytest.raises(ToolExecutorError, match="portfolio"):
            asyncio.run(client.get_prompt("portfolio"))
