"""Tests for McpClientGroup.

McpClientGroup depends only on McpClient's public interface (__aenter__,
__aexit__, list_tools, list_prompts, call_tool, get_prompt), so these tests use
a small fake double satisfying that shape rather than mocking McpClient
internals - the same "anything exposing this interface substitutes for
McpClient" contract the class itself is built on. No real subprocess is
spawned; that end-to-end path (and the anyio cancel-scope bug it once
surfaced - see McpClientGroup.__aenter__'s docstring) can only be verified by
actually running main.py against a real server, not by a fake like this one.
"""
import asyncio

import pytest

from src.agent import ToolExecutorError
from src.mcp_client import McpClientGroup, ServerCollisionError


class FakeClient:
    """Minimal stand-in for McpClient - only what McpClientGroup actually calls."""

    def __init__(self, tools=(), prompts=(), fail_connect: Exception | None = None):
        self.tools = list(tools)
        self.prompts = list(prompts)
        self.fail_connect = fail_connect
        self.entered = False
        self.exited = False

    async def __aenter__(self):
        if self.fail_connect is not None:
            raise self.fail_connect
        self.entered = True
        return self

    async def __aexit__(self, *exc_info):
        self.exited = True

    async def list_tools(self):
        return self.tools

    async def list_prompts(self):
        return self.prompts

    async def call_tool(self, name, arguments):
        return f"{name} called with {arguments}"

    async def get_prompt(self, name):
        return f"prompt:{name}"


def make_tool(name: str) -> dict:
    return {"name": name, "description": "d", "input_schema": {}}


def make_prompt(name: str) -> dict:
    return {"name": name, "description": "d"}


class TestConnectAndMergedToolList:
    def test_merges_tools_from_every_live_server(self):
        sheets = FakeClient(tools=[make_tool("read_x")])
        simulator = FakeClient(tools=[make_tool("run_sim")])

        async def scenario():
            async with McpClientGroup({"sheets": sheets, "simulator": simulator}) as group:
                return await group.list_tools()

        tools = asyncio.run(scenario())

        assert {t["name"] for t in tools} == {"read_x", "run_sim"}

    def test_every_live_client_is_entered_and_exited(self):
        sheets = FakeClient()
        simulator = FakeClient()

        async def scenario():
            async with McpClientGroup({"sheets": sheets, "simulator": simulator}):
                assert sheets.entered and simulator.entered

        asyncio.run(scenario())

        assert sheets.exited and simulator.exited


class TestCallToolRouting:
    def test_call_tool_routes_to_the_owning_server(self):
        sheets = FakeClient(tools=[make_tool("read_x")])
        simulator = FakeClient(tools=[make_tool("run_sim")])

        async def scenario():
            async with McpClientGroup({"sheets": sheets, "simulator": simulator}) as group:
                return await group.call_tool("run_sim", {"n": 1})

        result = asyncio.run(scenario())

        assert result == "run_sim called with {'n': 1}"

    def test_unknown_tool_raises_tool_executor_error(self):
        sheets = FakeClient(tools=[make_tool("read_x")])

        async def scenario():
            async with McpClientGroup({"sheets": sheets}) as group:
                await group.call_tool("does_not_exist", {})

        with pytest.raises(ToolExecutorError):
            asyncio.run(scenario())


class TestGetPromptRouting:
    def test_get_prompt_routes_to_the_owning_server(self):
        sheets = FakeClient(prompts=[make_prompt("portfolio")])
        simulator = FakeClient()

        async def scenario():
            async with McpClientGroup({"sheets": sheets, "simulator": simulator}) as group:
                return await group.get_prompt("portfolio")

        result = asyncio.run(scenario())

        assert result == "prompt:portfolio"

    def test_unknown_prompt_raises_tool_executor_error(self):
        sheets = FakeClient()

        async def scenario():
            async with McpClientGroup({"sheets": sheets}) as group:
                await group.get_prompt("does_not_exist")

        with pytest.raises(ToolExecutorError):
            asyncio.run(scenario())


class TestNameCollisions:
    """Two servers defining the same tool or prompt name is a configuration
    error - detected at connect time, in McpClientGroup._register, and raised
    as ServerCollisionError rather than silently letting one owner win."""

    def test_tool_collision_raises_server_collision_error(self):
        sheets = FakeClient(tools=[make_tool("read_x")])
        simulator = FakeClient(tools=[make_tool("read_x")])

        async def scenario():
            async with McpClientGroup({"sheets": sheets, "simulator": simulator}):
                pass

        with pytest.raises(ServerCollisionError):
            asyncio.run(scenario())

    def test_tool_collision_names_both_servers(self):
        sheets = FakeClient(tools=[make_tool("read_x")])
        simulator = FakeClient(tools=[make_tool("read_x")])

        async def scenario():
            async with McpClientGroup({"sheets": sheets, "simulator": simulator}):
                pass

        with pytest.raises(ServerCollisionError) as exc_info:
            asyncio.run(scenario())

        assert "sheets" in str(exc_info.value)
        assert "simulator" in str(exc_info.value)

    def test_prompt_collision_raises_server_collision_error(self):
        sheets = FakeClient(prompts=[make_prompt("portfolio")])
        simulator = FakeClient(prompts=[make_prompt("portfolio")])

        async def scenario():
            async with McpClientGroup({"sheets": sheets, "simulator": simulator}):
                pass

        with pytest.raises(ServerCollisionError):
            asyncio.run(scenario())

    def test_collision_still_tears_down_the_already_connected_clients(self):
        """Regression: __aenter__ raising means `async with` never calls
        __aexit__, so anything already connected in Step 1 must be torn down
        explicitly before re-raising in Step 2's except block - or it leaks.
        Checked here by confirming both fakes' __aexit__ actually ran, not just
        that the exception surfaced."""
        sheets = FakeClient(tools=[make_tool("read_x")])
        simulator = FakeClient(tools=[make_tool("read_x")])

        async def scenario():
            async with McpClientGroup({"sheets": sheets, "simulator": simulator}):
                pass

        with pytest.raises(ServerCollisionError):
            asyncio.run(scenario())

        assert sheets.entered and sheets.exited
        assert simulator.entered and simulator.exited


class TestPartialFailure:
    """A server that fails to connect is warned about and skipped, not fatal -
    a dead simulator should not block portfolio questions."""

    def test_one_dead_server_does_not_block_the_others(self):
        sheets = FakeClient(tools=[make_tool("read_x")])
        simulator = FakeClient(fail_connect=ToolExecutorError("simulator refused to start"))

        async def scenario():
            async with McpClientGroup({"sheets": sheets, "simulator": simulator}) as group:
                return await group.list_tools()

        tools = asyncio.run(scenario())

        assert {t["name"] for t in tools} == {"read_x"}

    def test_dead_server_is_never_entered_or_exited(self):
        sheets = FakeClient(tools=[make_tool("read_x")])
        simulator = FakeClient(fail_connect=ToolExecutorError("simulator refused to start"))

        async def scenario():
            async with McpClientGroup({"sheets": sheets, "simulator": simulator}):
                pass

        asyncio.run(scenario())

        assert simulator.entered is False
        assert simulator.exited is False


class TestTotalFailure:
    def test_all_servers_dead_raises_tool_executor_error(self):
        sheets = FakeClient(fail_connect=ToolExecutorError("sheets refused to start"))
        simulator = FakeClient(fail_connect=ToolExecutorError("simulator refused to start"))

        async def scenario():
            async with McpClientGroup({"sheets": sheets, "simulator": simulator}):
                pass

        with pytest.raises(ToolExecutorError):
            asyncio.run(scenario())
