# Personal context — how to write your CV and career-plan documents

There is no template file to fill in here anymore. The agent reads every
`.docx` file in `data/personal/` (gitignored) directly, fresh on each startup —
see `src/context.py::load_personal_context`. Edit your Word documents, the next
run picks up the change; there is no separate markdown file to keep in sync.

This file is guidance on how to write those documents so the extraction — which
understands headings and tables, not just plain text — captures them well.

## What the loader understands

- **Body paragraphs** pass through as plain text.
- **Heading 1 / 2 / 3** styles map to markdown headings, so section structure
  survives. Bold or underlined "Normal"-style text does *not* — use the actual
  Heading style in Word, not manual formatting, or the section will flatten
  into a wall of undifferentiated text.
- **Tables** are rendered as markdown pipe tables, in document order relative
  to the surrounding paragraphs — so a table stays attached to the heading
  above it. Keep tables simple and rectangular (no merged cells); the renderer
  assumes one table row is one markdown row.
- Every file's content is grounded under a `## <filename>` heading, so name
  the files for what they are (e.g. `Career_Plan_JacopoVentura.docx`).

## What to put where

- **CV**: role history, seniority, credentials, core strengths — the "who I
  am" the agent should reason from when a career suggestion needs to be
  realistic rather than generic.
- **Career plan**: target role and compensation by when, the chosen path and
  the alternatives considered, decision log of settled choices, salary bands
  or timelines — put these in tables, they are exactly what used to get
  silently dropped before table extraction existed.

## What is *not* sourced from these documents

Financial position and goals, the public GitHub portfolio, and advising-style
preferences are not covered by CV/career-plan text — they still need a source
(a live Sheets tool for the first, something else for the other two). See the
open item in `TODO.md` Phase 2.
