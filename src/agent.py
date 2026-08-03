"""AiAgent: a Claude-backed chat agent with conversation memory and automatic summarization."""
import anthropic


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
    ) -> None:
        self.client = anthropic.Anthropic(api_key=api_key)
        self._model: str = model
        self._system_prompt_kwargs: dict = {"system": system_prompt} if system_prompt is not None else {}
        self._max_tokens: int = max_tokens                                          # Default max_tokens for chat calls
        self._summary_max_tokens: int = summary_max_tokens                          # max_tokens for summarization calls
        self._count_of_answers_to_summarize: int = count_of_answers_to_summarize    # Amount of answers to be summarized
        self._count_of_answers_to_keep_after_summary: int = count_of_answers_to_keep_after_summary  # Amount of answers to keep after the summary
        self.__memory: list[dict] = []

    def ask_claude(
        self,
        input_prompt: list[dict[str, str]],
        print_stream: bool = False,
        max_tokens: int | None = None,
    ) -> anthropic.types.Message:
        """
        Core call to LLM model via Anthropic API, using streaming.
        :param input_prompt: conversation messages sent to the model.
        :param print_stream: if True, print text chunks to stdout as they arrive.
        :param max_tokens: overrides the default max_tokens for this call (e.g. summarization needs more headroom).
        :return: the full accumulated LLM model response.
        """
        with self.client.messages.stream(model=self._model,
                                          max_tokens=max_tokens if max_tokens is not None else self._max_tokens,
                                          messages=input_prompt,
                                          **self._system_prompt_kwargs) as stream:
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

    def __add_user_message(self, text: str) -> None:
        """
        Adds user message to memory list.
        :param text: user message to be appended.
        :return: None
        """
        user_message = {"role": "user", "content": text}
        self.__memory.append(user_message)

    def __add_assistant_message(self, text: str) -> None:
        """
        Adds assistant message to memory list.
        :param text: Claude answer to be appended.
        :return: None
        """
        assistant_message = {"role": "assistant", "content": text}
        self.__memory.append(assistant_message)

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
                prompt += "Role: " + message["role"] + ": " + message["content"] + "\n"

            summary_response = self.ask_claude(
                [{"role": "user", "content": prompt}],
                max_tokens=self._summary_max_tokens,
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

            # Add latest user message to memory
            self.__add_user_message(user_message)

            # Call the LLM model via API
            try:
                agent_response = self.ask_claude(self.__memory, print_stream=True)
            except anthropic.RateLimitError as e:
                # Remove the user question from the list (last item) since this conversation failed
                self.__memory.pop()
                retry_after = e.response.headers.get("retry-after", "a few")
                print(f"Rate limited, please wait {retry_after} seconds and try again.")
                continue
            except anthropic.APIConnectionError:
                # Remove the user question from the list (last item) since this conversation failed
                self.__memory.pop()
                print("Network error - could not reach Claude. Please try again.")
                continue
            except anthropic.APIStatusError as e:
                # Remove the user question from the list (last item) since this conversation failed
                self.__memory.pop()
                print(f"Claude API error ({e.status_code}): {e.message}")
                continue

            final_response = self.extract_text(agent_response)
            self.__add_assistant_message(final_response)
            print()
