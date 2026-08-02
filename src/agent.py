"""AiAgent: a Claude-backed chat agent with conversation memory and automatic summarization."""
import anthropic


class AiAgent:
    """
    Orchestrator of the AI agent: holds the Claude client and runs the chat loop.
    """

    def __init__(self, api_key: str, system_prompt: str | None = None) -> None:
        self.client = anthropic.Anthropic(api_key=api_key)
        self.__MODEL: str = "claude-haiku-4-5"
        self.__SYSTEM_PROMPT_KWARGS: dict = {"system": system_prompt} if system_prompt is not None else {}
        self.__COUNT_OF_ANSWERS_TO_SUMMARIZE: int = 50              # Amount of answer to be summarized
        self.__COUNT_OF_ANSWERS_TO_KEEP_AFTER_SUMMARY: int = 10     # Amount of answers to keep after the summary
        self.__memory: list[dict] = []

    def ask_claude(self, input_prompt: list[dict[str, str]], print_stream: bool = False) -> anthropic.types.Message:
        """
        Core call to LLM model via Anthropic API, using streaming.
        :param input_prompt: conversation messages sent to the model.
        :param print_stream: if True, print text chunks to stdout as they arrive.
        :return: the full accumulated LLM model response.
        """
        with self.client.messages.stream(model=self.__MODEL,
                                          max_tokens=1024,
                                          messages=input_prompt,
                                          **self.__SYSTEM_PROMPT_KWARGS) as stream:
            for text in stream.text_stream:
                if print_stream:
                    print(text, end="", flush=True)
            return stream.get_final_message()


    @staticmethod
    def extract_text(response: anthropic.types.Message) -> str:
        """
        Extract text from an API response.
        :param response: agent full response dictionary.
        :return: text extracted from response to be displayed to the user.
        """
        return next(block.text for block in response.content if block.type == "text")

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

        # Divide length by 2 to get the count of answers from the agent
        if len(self.__memory) / 2. > (self.__COUNT_OF_ANSWERS_TO_SUMMARIZE + self.__COUNT_OF_ANSWERS_TO_KEEP_AFTER_SUMMARY):

            # Partition memory: the last KEEP*2 messages are kept verbatim, everything
            # before that is summarized. This partition never overlaps and never leaves a gap.
            keep_count = self.__COUNT_OF_ANSWERS_TO_KEEP_AFTER_SUMMARY * 2
            to_summarize = self.__memory[:-keep_count]
            to_keep = self.__memory[-keep_count:]

            prompt: str = "Make a concise summary of the following conversation:\n"
            for message in to_summarize:
                prompt += "Role: " + message["role"] + ": " + message["content"] + "\n"

            summary_response = self.ask_claude([{"role": "user", "content": prompt}])
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
                retry_after = e.response.headers.get("retry-after", "a few")
                print(f"Rate limited, please wait {retry_after} seconds and try again.")
                continue
            except anthropic.APIConnectionError:
                print("Network error - could not reach Claude. Please try again.")
                continue
            except anthropic.APIStatusError as e:
                print(f"Claude API error ({e.status_code}): {e.message}")
                continue

            final_response = self.extract_text(agent_response)
            self.__add_assistant_message(final_response)
            print()
