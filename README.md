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

The app serves a chat interface for grounded legal Q&A over both corpora, powered by an Anthropic agent with tool use.

### How It Works

The agent uses Claude (`claude-sonnet-4-6` by default) with two tools:

1. **`search_toc`** — keyword search over a table of contents built from both markdown corpora at startup.
2. **`get_section`** — retrieves the full text of a chapter by index.

On each question the agent searches the TOC, fetches the relevant chapters, then answers with citations grounded in the retrieved text. Multi-turn conversation history is supported.

### Run Locally

```bash
python3 -m pip install -r requirements.txt
cp .env.example .env   # add your ANTHROPIC_API_KEY
./start.sh
```

Open `http://127.0.0.1:8000`.

### Deploy to Railway

The repo includes a `Dockerfile` and `railway.json` for one-step Railway deployment:

1. Connect the GitHub repo in the [Railway dashboard](https://railway.com).
2. Set the `ANTHROPIC_API_KEY` environment variable in the service settings.
3. Railway auto-builds from the Dockerfile, runs health checks on `/health`, and assigns a public URL.

Or deploy from the CLI:

```bash
railway link
railway up
```

### Configuration

All settings are via environment variables (see `.env.example`):

- `ANTHROPIC_API_KEY` — required
- `MODEL_NAME` — model for the agent (default `claude-sonnet-4-6`)
- `MAX_HISTORY_MESSAGES` — conversation turns to include (default `8`)
- `MAX_OUTPUT_TOKENS` — max response tokens (default `4096`)
- `TOC_SEARCH_LIMIT` — max TOC search results per query (default `8`)
- `OBSERVABILITY_LOG_LEVEL` — structured log level (default `INFO`)

## Known Limitations

- This is a transformed convenience corpus, not a replacement for the official publication.
- If enCodePlus changes source structure, parser logic may need updates.
- Internal text may still reference excluded appendices or external materials; those references are part of included legal text.
- Zoning images are represented as placeholders by default; image binaries are not downloaded locally.

## Legal Disclaimer

This repository is for informational and research use only and does not constitute legal advice.
