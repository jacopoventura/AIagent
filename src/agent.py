"""AiAgent: a Claude-backed chat agent with conversation memory and automatic summarization."""
from typing import Protocol

import anthropic


class ToolExecutor(Protocol):
    """A callable that executes one tool call and returns its result as a string.

    Depending only on this signature - not on MCP - lets the tool loop be built and
    tested with a plain function, and the transport swapped in later without touching it.
    """

    def __call__(self, name: str, arguments: dict) -> str: ...


class AiAgent:
    """
    Orchestrator of the AI agent: holds the Claude client and runs the chat loop.
    """

    def __init__(
        self,
        api_key: str,
        system_prompt: str | None = None,
        model: str = "claude-haiku-4-5",
        max_tokens: int = 1024,
        summary_max_tokens: int = 4096,
        count_of_answers_to_summarize: int = 50,
        count_of_answers_to_keep_after_summary: int = 10,
        tools: list[dict] | None = None,
        tool_executor: ToolExecutor | None = None,
        max_tool_iterations: int = 10,
    ) -> None:
        self.client = anthropic.Anthropic(api_key=api_key)
        self._model: str = model
        # cache_control marks the system prompt for prompt caching: it is a few thousand
        # tokens (role text + parsed CV/career-plan docx) and identical on every turn, so
        # without this it would be resent and re-processed as fresh input on every call.
        self._system_prompt_kwargs: dict = (
            {"system": [{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}]}
            if system_prompt is not None
            else {}
        )
        self._max_tokens: int = max_tokens                                          # Default max_tokens for chat calls
        self._summary_max_tokens: int = summary_max_tokens                          # max_tokens for summarization calls
        self._count_of_answers_to_summarize: int = count_of_answers_to_summarize    # Amount of answers to be summarized
        self._count_of_answers_to_keep_after_summary: int = count_of_answers_to_keep_after_summary  # Amount of answers to keep after the summary
        self._tools: list[dict] = tools or []
        self._tool_executor: ToolExecutor | None = tool_executor
        self._max_tool_iterations: int = max_tool_iterations
        self.__memory: list[dict] = []

    def ask_claude(
        self,
        input_prompt: list[dict],
        print_stream: bool = False,
        max_tokens: int | None = None,
        use_tools: bool = True,
    ) -> anthropic.types.Message:
        """
        Core call to LLM model via Anthropic API, using streaming.
        :param input_prompt: conversation messages sent to the model.
        :param print_stream: if True, print text chunks to stdout as they arrive.
        :param max_tokens: overrides the default max_tokens for this call (e.g. summarization needs more headroom).
        :param use_tools: if False, no `tools` are offered even if configured - summarization calls must set
                           this, or the summarizer may try to call a tool instead of summarizing.
        :return: the full accumulated LLM model response.
        """
        tools_kwargs = {"tools": self._tools} if (use_tools and self._tools) else {}
        with self.client.messages.stream(model=self._model,
                                          max_tokens=max_tokens if max_tokens is not None else self._max_tokens,
                                          messages=input_prompt,
                                          **self._system_prompt_kwargs,
                                          **tools_kwargs) as stream:
            for text in stream.text_stream:
                if print_stream:
                    print(text, end="", flush=True)
            return stream.get_final_message()


    @staticmethod
    def extract_text(response: anthropic.types.Message) -> str:
        """
        Extract text from an API response.
        :param response: agent full response dictionary.
        :return: text extracted from response, or "" if the response has no text block
                 (e.g. a tool-use-only response).
        """
        return next((block.text for block in response.content if block.type == "text"), "")

    @staticmethod
    def _flatten_content(content: str | list) -> str:
        """
        Render message content as plain text for the summarization prompt, which builds
        a single string and previously assumed every message's content already was one.
        Once the tool loop has been through a message, content is a *list* instead: raw
        SDK content-block objects (attribute access) for an assistant's tool_use turn, or
        the plain dicts this class builds itself (item access) for a tool_result turn.
        :param content: a message's content - plain text, or a list of blocks.
        :return: plain text summarizing what the block(s) contained.
        """
        if isinstance(content, str):
            return content

        parts = []
        for block in content:
            get = block.get if isinstance(block, dict) else lambda key: getattr(block, key)
            block_type = get("type")
            if block_type == "text":
                parts.append(get("text"))
            elif block_type == "tool_use":
                parts.append(f"[called tool {get('name')} with {get('input')}]")
            elif block_type == "tool_result":
                parts.append(f"[tool result: {get('content')}]")
        return "\n".join(parts)

    def __add_user_message(self, content: str | list[dict]) -> None:
        """
        Adds user message to memory list.
        :param content: user turn to be appended - plain text, or `tool_result` blocks.
        :return: None
        """
        self.__memory.append({"role": "user", "content": content})

    def __add_assistant_message(self, content: str | list[dict]) -> None:
        """
        Adds assistant message to memory list.
        :param content: Claude turn to be appended - plain text, or raw response content
                         blocks (text and/or `tool_use`).
        :return: None
        """
        self.__memory.append({"role": "assistant", "content": content})

    def _check_tool_calls(self, agent_response: anthropic.types.Message) -> anthropic.types.Message:
        """
        While `agent_response` requests tool calls, execute each via `tool_executor`,
        append the assistant's tool_use turn and the resulting tool_result turn to memory,
        and ask Claude again - until it stops requesting tools or the iteration cap is hit.
        :param agent_response: the response that may have `stop_reason == "tool_use"`.
        :return: the first response with a stop_reason other than "tool_use".
        """
        iterations = 0
        while agent_response.stop_reason == "tool_use":
            if iterations >= self._max_tool_iterations:
                print(f"\n[stopped after {self._max_tool_iterations} tool calls]")
                break
            iterations += 1

            self.__add_assistant_message(agent_response.content)
            tool_results = [
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": self._tool_executor(block.name, block.input),
                }
                for block in agent_response.content
                if block.type == "tool_use"
            ]
            self.__add_user_message(tool_results)
            agent_response = self.ask_claude(self.__memory, print_stream=True)

        return agent_response

    def _check_memory_for_summary(self) -> None:
        """
        Once the conversation grows past COUNT_OF_ANSWERS_TO_SUMMARIZE answers beyond the
        kept tail, summarize everything except the last COUNT_OF_ANSWERS_TO_KEEP_AFTER_SUMMARY
        answers and replace it with a single summary message.
        :return: None
        """

        # len(memory) is the count of questions from the user and answers from the agent; compare against
        # threshold*2 instead to avoid float division while keeping the same semantics.
        if len(self.__memory) > (self._count_of_answers_to_summarize + self._count_of_answers_to_keep_after_summary) * 2:

            # Partition memory: the last KEEP*2 messages are kept verbatim, everything
            # before that is summarized. This partition never overlaps and never leaves a gap.
            keep_count = self._count_of_answers_to_keep_after_summary * 2
            to_summarize = self.__memory[:-keep_count]
            to_keep = self.__memory[-keep_count:]

            prompt: str = "Make a concise summary of the following conversation:\n"
            for message in to_summarize:
                prompt += "Role: " + message["role"] + ": " + self._flatten_content(message["content"]) + "\n"

            summary_response = self.ask_claude(
                [{"role": "user", "content": prompt}],
                max_tokens=self._summary_max_tokens,
                use_tools=False,
            )
            summary_text = self.extract_text(summary_response)

            summary_message = {"role": "user", "content": f"[Summary of earlier conversation]\n{summary_text}"}
            self.__memory = [summary_message] + to_keep


    def run(self) -> None:
        """Run the interactive chat loop until the user exits."""

        while True:

            # Check if conversation is too long and must be summarized
            self._check_memory_for_summary()

            # Get the user message
            user_message = input("You: ")

            # Check if exit command is given
            if user_message.strip().lower() in ("exit", "quit", "bye"):
                break

            # Snapshot memory length so a failure anywhere in this turn - including mid
            # tool-calling round-trip, which appends several messages - can be rolled back
            # to exactly where the turn started, never leaving an orphaned tool_use/tool_result.
            turn_start = len(self.__memory)

            # Add latest user message to memory
            self.__add_user_message(user_message)

            # Call the LLM model via API, executing any tool calls it requests
            try:
                agent_response = self.ask_claude(self.__memory, print_stream=True)
                agent_response = self._check_tool_calls(agent_response)
            except anthropic.RateLimitError as e:
                self.__memory = self.__memory[:turn_start]
                retry_after = e.response.headers.get("retry-after", "a few")
                print(f"Rate limited, please wait {retry_after} seconds and try again.")
                continue
            except anthropic.APIConnectionError:
                self.__memory = self.__memory[:turn_start]
                print("Network error - could not reach Claude. Please try again.")
                continue
            except anthropic.APIStatusError as e:
                self.__memory = self.__memory[:turn_start]
                print(f"Claude API error ({e.status_code}): {e.message}")
                continue

            final_response = self.extract_text(agent_response)
            self.__add_assistant_message(final_response)
            print()
