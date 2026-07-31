import os

import anthropic
from dotenv import load_dotenv

load_dotenv()
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
if not ANTHROPIC_API_KEY:
    raise SystemExit("API key not provided")


class AiAgent:
    """Orchestrator of the AI agent: holds the Claude client and runs the chat loop."""

    def __init__(self, api_key: str) -> None:
        self.client = anthropic.Anthropic(api_key=api_key)
        self.__MODEL = "claude-haiku-4-5"
        self.__SUMMARY_SIZE_ANSWER = 50        # After this amount of answers, the agent will summarize the ongoing conversation
        self.__BUFFER_SIZE_LAST_ANSWER = 10    # The agent will keep this amount of last answers in memory


    def ask_claude(self, prompt: str) -> anthropic.types.Message:
        """
        Core call to LLM model via Anthropic API.
        :param prompt: User request in text form
        :return: LLM model response
        """
        return self.client.messages.create(
            model=self.__MODEL,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
            stream=False)

    @staticmethod
    def extract_text(response: anthropic.types.Message) -> str:
        """
        Extract text from an API response.
        :param response: agent full response dictionary.
        :return: text extracted from response to be displayed to the user.
        """
        return next(block.text for block in response.content if block.type == "text")

    def run(self) -> None:
        """Run the interactive chat loop until the user exits."""
        while True:

            # Get the user message
            user_message = input("You: ")

            # Check if exit command is given
            if user_message.strip().lower() in ("exit", "quit", "bye"):
                break

            # Call the LLM model via API
            try:
                agent_response = self.ask_claude(user_message)
                print(self.extract_text(agent_response))
            except anthropic.RateLimitError as e:
                retry_after = e.response.headers.get("retry-after", "a few")
                print(f"Rate limited, please wait {retry_after} seconds and try again.")
            except anthropic.APIConnectionError:
                print("Network error - could not reach Claude. Please try again.")
            except anthropic.APIStatusError as e:
                print(f"Claude API error ({e.status_code}): {e.message}")


# Press the green button in the gutter to run the script.
if __name__ == '__main__':

    agent = AiAgent(api_key=ANTHROPIC_API_KEY)
    agent.run()

