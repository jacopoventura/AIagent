"""Loads personal context (CV, career plan) from local .docx files for the system prompt."""
from pathlib import Path

from docx import Document

PERSONAL_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "personal"

def load_personal_context(directory: Path = PERSONAL_DATA_DIR) -> str:
    """
    Read every .docx file in `directory` and concatenate their text, so personal
    context can be refreshed by editing the files without touching any code.
    :param directory: folder containing personal .docx files (gitignored).
    :return: concatenated text from all readable .docx files found, one section per
             file; "" if the directory is missing or contains none.
    """
    if not directory.is_dir():
        return ""

    sections = []
    for docx_path in sorted(directory.glob("*.docx")):
        try:
            document = Document(docx_path)
        except Exception as e:
            print(f"Warning: could not read {docx_path.name}: {e}")
            continue

        text = "\n".join(paragraph.text for paragraph in document.paragraphs if paragraph.text.strip())
        if text:
            sections.append(f"## {docx_path.stem}\n{text}")

    return "\n\n".join(sections)


def generate_personal_career_and_finance_plan() -> str:
    """Generates the system prompt for personal career and finance plan."""
    system_prompt = (
        "You are a personal advisor for career and financial planning, treated as one problem: "
        "the user's earnings trajectory determines what their portfolio can become, and their "
        "portfolio target determines what their career must deliver and by when. Always reason "
        "about the two together, not as separate topics.\n\n"
        "However, the user might want to deep dive into the financial planning alone. In this case "
        "you must be able to deep dive in the topic, understand the financial needs of the user, "
        "check if the financial plan makes sense and make suggestion." 
        "When advising on the career plan, take the user's personal background and professional "
        "experience into account rather than giving generic career advice — proposals must be "
        "realistic given their actual trajectory, not a hypothetical blank-slate candidate.\n\n"
        "Be direct: state disagreement plainly, do not hedge, and do not restate the user's own "
        "numbers back to them. Ground every answer in their actual plan and figures rather than "
        "generic advice."
    )

    personal_context = load_personal_context()
    if personal_context:
        system_prompt += "\n\n# Personal context\n" + personal_context

    return system_prompt
