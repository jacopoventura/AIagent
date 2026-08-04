"""Tests for load_personal_context and generate_personal_career_and_finance_plan.

Docx fixtures are generated on the fly with python-docx, never committed -
the privacy guard blocks .docx files from entering git history regardless.
"""
from pathlib import Path

from docx import Document

from src.context import generate_personal_career_and_finance_plan, load_personal_context


def make_docx(path: Path, paragraphs: list[str]) -> None:
    """Write a minimal real .docx file at `path` with one paragraph per string."""
    document = Document()
    for text in paragraphs:
        document.add_paragraph(text)
    document.save(path)


class TestLoadPersonalContext:
    def test_missing_directory_returns_empty_string(self, tmp_path):
        missing = tmp_path / "does-not-exist"

        assert load_personal_context(missing) == ""

    def test_empty_directory_returns_empty_string(self, tmp_path):
        assert load_personal_context(tmp_path) == ""

    def test_reads_single_docx_file(self, tmp_path):
        make_docx(tmp_path / "career_plan.docx", ["Target: staff engineer by 2030.", "Risk appetite: moderate."])

        result = load_personal_context(tmp_path)

        assert "## career_plan" in result
        assert "Target: staff engineer by 2030." in result
        assert "Risk appetite: moderate." in result

    def test_concatenates_multiple_docx_files_in_sorted_order(self, tmp_path):
        make_docx(tmp_path / "b_career_plan.docx", ["career content"])
        make_docx(tmp_path / "a_cv.docx", ["cv content"])

        result = load_personal_context(tmp_path)

        assert result.index("a_cv") < result.index("b_career_plan")

    def test_ignores_non_docx_files(self, tmp_path):
        make_docx(tmp_path / "career_plan.docx", ["career content"])
        (tmp_path / "notes.txt").write_text("should be ignored")

        result = load_personal_context(tmp_path)

        assert "should be ignored" not in result

    def test_skips_blank_paragraphs(self, tmp_path):
        make_docx(tmp_path / "career_plan.docx", ["Real content.", "   ", ""])

        result = load_personal_context(tmp_path)

        assert result == "## career_plan\nReal content."

    def test_unreadable_docx_is_skipped_not_raised(self, tmp_path, capsys):
        corrupt = tmp_path / "corrupt.docx"
        corrupt.write_bytes(b"not a real docx file")
        make_docx(tmp_path / "career_plan.docx", ["career content"])

        result = load_personal_context(tmp_path)

        assert "career content" in result
        assert "could not read corrupt.docx" in capsys.readouterr().out


class TestGeneratePersonalCareerAndFinancePlan:
    """Regression coverage for the missing `return` bug: the function built the
    prompt string but fell off the end without returning it, so callers silently
    got None and the agent ran with no system prompt at all."""

    def test_returns_the_prompt_as_a_string(self, monkeypatch):
        monkeypatch.setattr("src.context.load_personal_context", lambda: "")

        result = generate_personal_career_and_finance_plan()

        assert isinstance(result, str)
        assert "personal advisor for career and financial planning" in result

    def test_appends_personal_context_when_present(self, monkeypatch):
        monkeypatch.setattr("src.context.load_personal_context", lambda: "## cv\nSome career history.")

        result = generate_personal_career_and_finance_plan()

        assert "# Personal context" in result
        assert "Some career history." in result

    def test_omits_personal_context_section_when_absent(self, monkeypatch):
        monkeypatch.setattr("src.context.load_personal_context", lambda: "")

        result = generate_personal_career_and_finance_plan()

        assert "# Personal context" not in result
