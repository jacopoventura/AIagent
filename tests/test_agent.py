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
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])


def make_tool_use_message() -> SimpleNamespace:
    """Build a fake Anthropic Message with only a tool_use block, no text."""
    return SimpleNamespace(content=[SimpleNamespace(type="tool_use", id="tool_1", name="some_tool", input={})])


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
