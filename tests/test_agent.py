"""Tests for AiAgent.extract_text and AiAgent.run.

ask_claude is always mocked - these tests never hit the real Anthropic API.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
import anthropic

from src.agent import AiAgent


def make_text_message(text: str) -> SimpleNamespace:
    """Build a minimal fake Anthropic Message with one text content block."""
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason="end_turn")


def make_tool_use_message(calls: list[tuple[str, str, dict]] | None = None) -> SimpleNamespace:
    """Build a fake Anthropic Message with one or more tool_use blocks, no text.
    `calls` is a list of (tool_use_id, tool_name, tool_input); defaults to a single call."""
    calls = calls or [("tool_1", "some_tool", {})]
    blocks = [SimpleNamespace(type="tool_use", id=call_id, name=name, input=tool_input) for call_id, name, tool_input in calls]
    return SimpleNamespace(content=blocks, stop_reason="tool_use")


@pytest.fixture
def agent() -> AiAgent:
    a = AiAgent(api_key="sk-ant-test-key-not-real")
    a.ask_claude = MagicMock()
    return a


class TestExtractText:
    def test_extracts_text_block(self):
        response = make_text_message("hello")

        assert AiAgent.extract_text(response) == "hello"

    def test_tool_use_only_response_does_not_raise(self):
        response = make_tool_use_message()

        assert AiAgent.extract_text(response) == ""


class TestSystemPromptCaching:
    def test_system_prompt_is_wrapped_with_ephemeral_cache_control(self):
        agent = AiAgent(api_key="sk-ant-test-key-not-real", system_prompt="You are a helpful assistant.")

        assert agent._system_prompt_kwargs == {
            "system": [
                {
                    "type": "text",
                    "text": "You are a helpful assistant.",
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        }

    def test_no_system_prompt_means_no_system_kwarg(self):
        agent = AiAgent(api_key="sk-ant-test-key-not-real")

        assert agent._system_prompt_kwargs == {}


class TestRunMemoryOnFailure:
    """Regression tests: a failed API call must not leave an unanswered user
    message behind in memory, or the next turn would send two consecutive
    user messages."""

    @staticmethod
    def _queue_inputs(monkeypatch, inputs: list[str]) -> None:
        responses = iter(inputs)
        monkeypatch.setattr("builtins.input", lambda _: next(responses))

    def test_rate_limit_error_does_not_leave_stray_user_message(self, agent, monkeypatch):
        request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        response = httpx.Response(429, request=request, headers={"retry-after": "1"})
        agent.ask_claude.side_effect = anthropic.RateLimitError("rate limited", response=response, body=None)
        self._queue_inputs(monkeypatch, ["hi", "exit"])

        agent.run()

        assert agent._AiAgent__memory == []

    def test_connection_error_does_not_leave_stray_user_message(self, agent, monkeypatch):
        request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        agent.ask_claude.side_effect = anthropic.APIConnectionError(request=request)
        self._queue_inputs(monkeypatch, ["hi", "exit"])

        agent.run()

        assert agent._AiAgent__memory == []

    def test_api_status_error_does_not_leave_stray_user_message(self, agent, monkeypatch):
        request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        response = httpx.Response(500, request=request)
        agent.ask_claude.side_effect = anthropic.APIStatusError("boom", response=response, body=None)
        self._queue_inputs(monkeypatch, ["hi", "exit"])

        agent.run()

        assert agent._AiAgent__memory == []

    def test_success_appends_both_user_and_assistant_messages(self, agent, monkeypatch):
        agent.ask_claude.return_value = make_text_message("hello back")
        self._queue_inputs(monkeypatch, ["hi", "exit"])

        agent.run()

        assert agent._AiAgent__memory == [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello back"},
        ]


class TestRunToolLoop:
    """Coverage for the agentic tool loop: execute -> append tool_result -> repeat
    until end_turn, the max_tool_iterations guard, and the memory-rollback fix that
    the loop's multi-message turns make necessary."""

    @staticmethod
    def _queue_inputs(monkeypatch, inputs: list[str]) -> None:
        responses = iter(inputs)
        monkeypatch.setattr("builtins.input", lambda _: next(responses))

    def test_selects_between_two_distinct_tools_then_answers(self, monkeypatch):
        calls_made = []

        def tool_executor(name: str, arguments: dict) -> str:
            calls_made.append((name, arguments))
            return f"{name} result"

        a = AiAgent(
            api_key="sk-ant-test-key-not-real",
            tools=[{"name": "get_weather"}, {"name": "get_stock_price"}],
            tool_executor=tool_executor,
        )
        a.ask_claude = MagicMock()
        a.ask_claude.side_effect = [
            make_tool_use_message([("call_1", "get_weather", {"city": "Zurich"})]),
            make_tool_use_message([("call_2", "get_stock_price", {"ticker": "NESN"})]),
            make_text_message("Sunny, and NESN is at 95."),
        ]
        self._queue_inputs(monkeypatch, ["weather and stock?", "exit"])

        a.run()

        assert calls_made == [("get_weather", {"city": "Zurich"}), ("get_stock_price", {"ticker": "NESN"})]
        assert a._AiAgent__memory[-1] == {"role": "assistant", "content": "Sunny, and NESN is at 95."}

    def test_tool_result_is_linked_to_the_matching_tool_use_id(self, monkeypatch):
        a = AiAgent(
            api_key="sk-ant-test-key-not-real",
            tools=[{"name": "get_weather"}],
            tool_executor=lambda name, arguments: "sunny",
        )
        a.ask_claude = MagicMock()
        a.ask_claude.side_effect = [
            make_tool_use_message([("call_xyz", "get_weather", {"city": "Zurich"})]),
            make_text_message("It's sunny."),
        ]
        self._queue_inputs(monkeypatch, ["weather?", "exit"])

        a.run()

        tool_result_message = a._AiAgent__memory[2]
        assert tool_result_message == {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "call_xyz", "content": "sunny"}],
        }

    def test_stops_after_max_tool_iterations(self, monkeypatch, capsys):
        calls_made = []

        def tool_executor(name: str, arguments: dict) -> str:
            calls_made.append(name)
            return "still going"

        # A confused model that keeps requesting the same tool forever, if not capped.
        def always_requests_a_tool(*args, **kwargs):
            return make_tool_use_message([("call", "loop_tool", {})])

        a = AiAgent(
            api_key="sk-ant-test-key-not-real",
            tools=[{"name": "loop_tool"}],
            tool_executor=tool_executor,
            max_tool_iterations=3,
        )
        a.ask_claude = MagicMock(side_effect=always_requests_a_tool)
        self._queue_inputs(monkeypatch, ["loop please", "exit"])

        a.run()

        assert len(calls_made) == 3
        assert "[stopped after 3 tool calls]" in capsys.readouterr().out

    def test_failure_mid_tool_turn_rolls_back_only_the_failed_turn(self, monkeypatch):
        a = AiAgent(
            api_key="sk-ant-test-key-not-real",
            tools=[{"name": "get_weather"}],
            tool_executor=lambda name, arguments: "sunny",
        )
        request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        response = httpx.Response(500, request=request)
        a.ask_claude = MagicMock()
        a.ask_claude.side_effect = [
            make_text_message("hi there"),                                          # turn 1: plain answer, no tools
            make_tool_use_message([("call_1", "get_weather", {"city": "Zurich"})]),  # turn 2: requests a tool...
            anthropic.APIStatusError("boom", response=response, body=None),          # ...then the follow-up call fails
        ]
        self._queue_inputs(monkeypatch, ["hi", "weather?", "exit"])

        a.run()

        # Turn 1's messages survive; turn 2's user question and the assistant's
        # tool_use turn it triggered are both rolled back, not just the last one.
        assert a._AiAgent__memory == [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hi there"},
        ]
