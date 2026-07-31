import os
from mailbox import Message

import anthropic
from dotenv import load_dotenv

load_dotenv()
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
if not ANTHROPIC_API_KEY:
    raise SystemExit("API key not provided")

# Settings
SUMMARY_SIZE_ANSWER = 50        # After this amount of answers, the agent will summarize the ongoing conversation
BUFFER_SIZE_LAST_ANSWER = 10    # The agent will keep this amount of last answers in memory

MODEL = "claude-haiku-4-5"
client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def extract_text(response: anthropic.types.Message) -> str:
    """
    Extract text from an API response.
    :param response: agent full response dictionary.
    :return: text extracted from response to be displayed to the user.
    """
    return next(block.text for block in response.content if block.type == "text")


def ask_claude(prompt: str) -> anthropic.types.Message:
    """
    Core call to LLM model via Anthropic API.
    :param prompt: User request in text form
    :return: LLM model response
    """
    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    return response

def ai_agent() -> None:
    """
    This is the orchestrator of the AI agent.
    """
    while True:

        # Get the user message
        user_message = input("You: ")

        # Check if exit command is given
        if user_message.strip().lower() in ("exit", "quit", "bye"):
            break

        # Call the LLM model via API
        try:
            agent_response = ask_claude(user_message)
            print(extract_text(agent_response))
        except anthropic.RateLimitError as e:
            retry_after = e.response.headers.get("retry-after", "a few")
            print(f"Rate limited, please wait {retry_after} seconds and try again.")
        except anthropic.APIConnectionError:
            print("Network error - could not reach Claude. Please try again.")
        except anthropic.APIStatusError as e:
            print(f"Claude API error ({e.status_code}): {e.message}")



# Press the green button in the gutter to run the script.
if __name__ == '__main__':

    ai_agent()

