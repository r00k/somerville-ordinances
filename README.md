# Somerville Municipal Law Consolidation

Consolidated, LLM-ready extracts of Somerville municipal law from enCodePlus.

## What This Is

This repository fetches official municipal-law content from Somerville's enCodePlus publications, normalizes it into machine-friendly Markdown, and optionally renders a human-friendly HTML reading edition.

It now also includes a local web app with a ChatGPT-style interface for grounded legal Q&A over both corpora.

## Who This Is For

- People building RAG/LLM workflows over Somerville law text.
- Researchers, civic technologists, and policy teams who want consolidated non-zoning and zoning law corpora.
- Developers who want a reproducible fetch + transform + verification pipeline.

## Quick Start

```bash
python3 fetch_somerville_law.py
python3 render_markdown_html.py

# Zoning ordinance (text-first with image placeholders)
python3 fetch_somerville_zoning.py --skip-pdf-attempt --strip-metadata
python3 render_markdown_html.py \
  --input somerville-zoning.md \
  --output somerville-zoning.readable.html \
  --title 'Somerville Zoning Ordinance (Readable Edition)'
```

Expected outputs:

- `somerville-law-non-zoning.md`
- `somerville-law-non-zoning.raw.html`
- `somerville-law-non-zoning.readable.html`
- `somerville-zoning.md`
- `somerville-zoning.raw.html`
- `somerville-zoning.images.json`
- `somerville-zoning.readable.html`

## Legal QA Web App (Chat Interface)

The app serves a public-facing chat experience with strict grounding controls.

### Two-Pass Architecture

The QA engine uses a two-pass approach to answer questions:

**Pass 1 — Chapter Selection (cheap model).** At startup, the app parses both markdown corpora into a table of contents listing every chapter and its subheadings. When a question arrives, this TOC is sent to a fast, inexpensive model (`gpt-4.1-mini` by default, configurable via `PASS1_MODEL_NAME`) which selects the 1–3 most relevant chapters by index.

**Pass 2 — Answer Generation (capable model).** The full text of the selected chapters is sent to the primary model (`gpt-5.4` by default, configurable via `MODEL_NAME`) along with the user's question and conversation history. This model generates an answer with citations grounded in the retrieved text.

This split keeps costs low (the TOC selection task is simple enough for a mini model) while preserving answer quality for the generation step.

Additional guardrails:
- requires citations tied to retrieved sections,
- refuses or asks clarification if grounding is insufficient,
- supports multi-turn conversation with history.

Run locally:

```bash
python3 -m pip install -r requirements.txt

# Optional: copy and edit provider/runtime settings
cp .env.example .env

# Default offline mode uses MODEL_PROVIDER=mock
python3 main.py

# or
python3 -m uvicorn app.api:app --host 127.0.0.1 --port 8000 --reload
```

Open `http://127.0.0.1:8000`.

Container run:

```bash
docker build -t somerville-law-assistant .
docker run --rm -p 8000:8000 \
  -e MODEL_PROVIDER=mock \
  -e MODEL_NAME=mock-local \
  somerville-law-assistant
```

## Model Provider Swapping

The model layer is provider-agnostic. Retrieval + citation validation behavior stays the same while swapping only env config.

Examples:

```bash
# OpenAI
MODEL_PROVIDER=openai \
MODEL_NAME=gpt-5.4 \
OPENAI_API_KEY=... \
python3 -m uvicorn app.api:app --host 127.0.0.1 --port 8000

# Anthropic
MODEL_PROVIDER=anthropic \
MODEL_NAME=claude-sonnet-4-5 \
ANTHROPIC_API_KEY=... \
python3 -m uvicorn app.api:app --host 127.0.0.1 --port 8000

# Generic key override (works for either provider)
MODEL_PROVIDER=openai MODEL_API_KEY=... python3 main.py
```

Relevant settings (see `.env.example`):

- `MODEL_PROVIDER` (`openai`, `anthropic`)
- `MODEL_NAME` (primary model for answer generation, default `gpt-5.4`)
- `PASS1_MODEL_NAME` (cheaper model for chapter selection, default `gpt-4.1-mini`)
- `MODEL_API_KEY` or provider-specific key (`OPENAI_API_KEY` / `ANTHROPIC_API_KEY`)
- `OBSERVABILITY_LOG_LEVEL` (`DEBUG`, `INFO`, `WARNING`, etc.)

## Known Limitations

- This is a transformed convenience corpus, not a replacement for the official publication.
- If enCodePlus changes source structure, parser logic may need updates.
- Internal text may still reference excluded appendices or external materials; those references are part of included legal text.
- Zoning images are represented as placeholders by default; image binaries are not downloaded locally.

## Legal Disclaimer

This repository is for informational and research use only and does not constitute legal advice.
