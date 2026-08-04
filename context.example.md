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
- **Public portfolio**: a table of `Repo | What it is | What it demonstrates`
  for what's on GitHub. A dated snapshot you update by hand when repos are
  renamed, added or archived — not a live feed, deliberately, since what
  matters for career conversations is what each repo demonstrates, not its
  star count. Keep it in its own file; it changes on a different cadence than
  the career plan.

## What is *not* sourced from these documents

- **How you want to be advised** — directness, what you don't want, standing
  constraints — isn't a fact about you to extract from a document. It's an
  instruction about the agent's behavior, so it's hand-written directly into
  the system prompt in `main.py` instead.
- **Financial position and goals** — the target portfolio value and the
  assumptions behind it (withdrawal rate, confidence, tax treatment) aren't
  static facts either; they're computed by the simulator from hypotheses that
  already live as config in `portfolio-lifecycle-simulator`. Writing them into
  a docx here would duplicate that and go stale the moment an assumption
  changes there. This comes from a live tool call once the simulator MCP
  server exists (`TODO.md` Phase 4), not from a document.
