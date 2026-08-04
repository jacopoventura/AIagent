"""Tests for AiAgent._check_memory_for_summary.

The method is exercised with small, test-friendly threshold constructor
parameters (SUMMARIZE=3, KEEP=2) instead of the real defaults (50/10), so
each test only needs a handful of messages to reach the trigger condition.

ask_claude is always mocked - these tests never hit the real Anthropic API.
_check_memory_for_summary is async (AiAgent awaits ask_claude), so ask_claude
is an AsyncMock and every call runs under asyncio.run(...).
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.agent import AiAgent


def make_message(text: str) -> SimpleNamespace:
    """Build a minimal fake Anthropic Message with one text content block."""
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])


def make_pair(i: int) -> list[dict]:
    """Build one user/assistant message pair for test memory."""
    return [
        {"role": "user", "content": f"Question {i}"},
        {"role": "assistant", "content": f"Answer {i}"},
    ]


def memory_of(pairs: int) -> list[dict]:
    """Build a memory list of the given number of user/assistant pairs."""
    memory = []
    for i in range(pairs):
        memory.extend(make_pair(i))
    return memory


@pytest.fixture
def agent() -> AiAgent:
    """AiAgent with small summarization thresholds (SUMMARIZE=3, KEEP=2) and a mocked client."""
    a = AiAgent(
        api_key="sk-ant-test-key-not-real",
        count_of_answers_to_summarize=3,
        count_of_answers_to_keep_after_summary=2,
    )
    a.ask_claude = AsyncMock(return_value=make_message("mocked summary text"))
    return a


class TestTriggerCondition:
    """Threshold is len(memory)/2 > SUMMARIZE + KEEP, i.e. more than 5 pairs here."""

    def test_empty_memory_does_not_crash_or_summarize(self, agent):
        agent._AiAgent__memory = []

        asyncio.run(agent._check_memory_for_summary())

        assert agent._AiAgent__memory == []
        agent.ask_claude.assert_not_called()

    def test_below_threshold_does_not_summarize(self, agent):
        agent._AiAgent__memory = memory_of(4)  # 4 <= 5
        original = list(agent._AiAgent__memory)

        asyncio.run(agent._check_memory_for_summary())

        assert agent._AiAgent__memory == original
        agent.ask_claude.assert_not_called()

    def test_exactly_at_threshold_does_not_summarize(self, agent):
        # Condition is strictly ">", so exactly 5 pairs must NOT trigger.
        agent._AiAgent__memory = memory_of(5)
        original = list(agent._AiAgent__memory)

        asyncio.run(agent._check_memory_for_summary())

        assert agent._AiAgent__memory == original
        agent.ask_claude.assert_not_called()

    def test_one_pair_over_threshold_triggers(self, agent):
        agent._AiAgent__memory = memory_of(6)  # 6 > 5

        asyncio.run(agent._check_memory_for_summary())

        agent.ask_claude.assert_called_once()


class TestSummaryResult:
    """6 pairs (12 messages), KEEP=2 -> keep last 4 messages, summarize the first 8."""

    def test_result_length_is_summary_plus_kept_tail(self, agent):
        agent._AiAgent__memory = memory_of(6)

        asyncio.run(agent._check_memory_for_summary())

        # 1 summary message + KEEP*2 = 1 + 4 = 5
        assert len(agent._AiAgent__memory) == 5

    def test_summary_message_shape(self, agent):
        agent._AiAgent__memory = memory_of(6)

        asyncio.run(agent._check_memory_for_summary())

        summary_message = agent._AiAgent__memory[0]
        assert summary_message["role"] == "user"
        assert "mocked summary text" in summary_message["content"]

    def test_kept_tail_is_last_keep_count_messages_unchanged(self, agent):
        agent._AiAgent__memory = memory_of(6)
        original = list(agent._AiAgent__memory)

        asyncio.run(agent._check_memory_for_summary())

        assert agent._AiAgent__memory[1:] == original[-4:]

    def test_most_recent_turn_is_never_dropped(self, agent):
        # Regression test: the turn that crosses the threshold must survive
        # (a previous bug dropped exactly this turn).
        agent._AiAgent__memory = memory_of(6)

        asyncio.run(agent._check_memory_for_summary())

        new_memory = agent._AiAgent__memory
        assert new_memory[-2] == {"role": "user", "content": "Question 5"}
        assert new_memory[-1] == {"role": "assistant", "content": "Answer 5"}

    def test_no_overlap_between_summarized_and_kept_portions(self, agent):
        # Regression test: an earlier bug summarized and kept the same messages.
        agent._AiAgent__memory = memory_of(6)

        asyncio.run(agent._check_memory_for_summary())

        kept_contents = {m["content"] for m in agent._AiAgent__memory[1:]}
        assert "Question 0" not in kept_contents
        assert "Answer 0" not in kept_contents


class TestAskClaudeCallShape:
    def test_summary_prompt_sent_as_single_user_message(self, agent):
        agent._AiAgent__memory = memory_of(6)

        asyncio.run(agent._check_memory_for_summary())

        sent_messages = agent.ask_claude.call_args.args[0]
        assert isinstance(sent_messages, list)
        assert len(sent_messages) == 1
        assert sent_messages[0]["role"] == "user"
        assert isinstance(sent_messages[0]["content"], str)

    def test_summary_prompt_contains_only_the_summarized_portion(self, agent):
        agent._AiAgent__memory = memory_of(6)
        original = list(agent._AiAgent__memory)

        asyncio.run(agent._check_memory_for_summary())

        prompt_text = agent.ask_claude.call_args.args[0][0]["content"]
        for message in original[:-4]:  # summarized: pairs 0-3
            assert message["content"] in prompt_text
        for message in original[-4:]:  # kept: pairs 4-5
            assert message["content"] not in prompt_text


class TestErrorHandling:
    def test_api_error_propagates_and_memory_is_untouched(self, agent):
        agent._AiAgent__memory = memory_of(6)
        original = list(agent._AiAgent__memory)
        agent.ask_claude.side_effect = RuntimeError("boom")

        with pytest.raises(RuntimeError):
            asyncio.run(agent._check_memory_for_summary())

        # Memory must not be partially mutated when the API call fails.
        assert agent._AiAgent__memory == original


class TestFlattenContent:
    """Regression coverage: message content is a list of blocks once a message has
    been through the tool loop, not always a string - _check_memory_for_summary
    used to concatenate content directly and raised TypeError on a list."""

    def test_plain_string_passes_through_unchanged(self):
        assert AiAgent._flatten_content("hello") == "hello"

    def test_flattens_a_text_block_object(self):
        content = [SimpleNamespace(type="text", text="hi there")]

        assert AiAgent._flatten_content(content) == "hi there"

    def test_flattens_a_tool_use_block_object(self):
        content = [SimpleNamespace(type="tool_use", id="call_1", name="get_weather", input={"city": "Zurich"})]

        result = AiAgent._flatten_content(content)

        assert "get_weather" in result
        assert "Zurich" in result

    def test_flattens_a_tool_result_dict(self):
        content = [{"type": "tool_result", "tool_use_id": "call_1", "content": "sunny"}]

        result = AiAgent._flatten_content(content)

        assert "sunny" in result

    def test_flattens_mixed_text_and_tool_use_in_one_turn(self):
        content = [
            SimpleNamespace(type="text", text="Let me check."),
            SimpleNamespace(type="tool_use", id="call_1", name="get_weather", input={"city": "Zurich"}),
        ]

        result = AiAgent._flatten_content(content)

        assert "Let me check." in result
        assert "get_weather" in result


class TestSummarizationWithToolBlocksInMemory:
    """End-to-end: a memory containing the shapes the tool loop actually produces
    (an assistant tool_use turn, a user tool_result turn) must summarize without
    raising - not just the isolated flattening helper above."""

    def test_does_not_raise_when_a_tool_turn_falls_in_the_summarized_portion(self, agent):
        tool_turn = [
            {"role": "user", "content": "weather?"},
            {"role": "assistant", "content": [SimpleNamespace(type="tool_use", id="call_1", name="get_weather", input={"city": "Zurich"})]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "sunny"}]},
            {"role": "assistant", "content": "It's sunny."},
        ]
        # tool_turn (4 msgs) + memory_of(4) (8 msgs) = 12 > threshold (10); KEEP=2 keeps
        # the last 4 messages, so tool_turn lands in the summarized portion, not the kept tail.
        agent._AiAgent__memory = tool_turn + memory_of(4)

        asyncio.run(agent._check_memory_for_summary())  # must not raise TypeError

        agent.ask_claude.assert_called_once()
        prompt_text = agent.ask_claude.call_args.args[0][0]["content"]
        assert "get_weather" in prompt_text
        assert "sunny" in prompt_text


class TestMultipleSummarizationCycles:
    def test_repeated_summarization_keeps_working(self, agent):
        agent._AiAgent__memory = memory_of(6)
        asyncio.run(agent._check_memory_for_summary())
        assert len(agent._AiAgent__memory) == 5

        # Grow memory again past threshold and summarize a second time.
        agent._AiAgent__memory.extend(make_pair(100))
        agent._AiAgent__memory.extend(make_pair(101))
        agent._AiAgent__memory.extend(make_pair(102))
        # len = 5 + 6 = 11 -> 5.5 pairs > 5, triggers again.

        asyncio.run(agent._check_memory_for_summary())

        assert len(agent._AiAgent__memory) == 5
        assert agent.ask_claude.call_count == 2
