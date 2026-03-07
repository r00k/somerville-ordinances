# Somerville Municipal Law Consolidation

Consolidated, LLM-ready extracts of Somerville municipal law from enCodePlus, plus a FastAPI chat app that routes each question to exactly one full-corpus model context.

## Repository Contents

- `fetch_somerville_law.py`: non-zoning extractor and cleaner.
- `fetch_somerville_zoning.py`: zoning extractor with image placeholders + image manifest.
- `render_markdown_html.py`: styled HTML renderer for markdown outputs.
- `app/`: FastAPI backend + static chat UI.
- `scripts/verify_app_answers.py`: end-to-end app verification checks.
- `somerville-law-non-zoning.md`: full non-zoning corpus.
- `somerville-zoning.md`: full zoning corpus.

## Quick Start

```bash
python3 -m pip install -r requirements.txt
cp .env.example .env
python3 main.py
```

Open `http://127.0.0.1:8000`.

## Current QA Architecture

The chat backend uses a strict two-model routing strategy.

1. It loads the full non-zoning markdown corpus.
1. It loads the full zoning markdown corpus.
1. It creates two OpenAI corpus-bound model clients (same `MODEL_NAME`, different corpus context).
1. It routes each user question to exactly one corpus (`non_zoning` or `zoning`).
1. It refuses ambiguous prompts and asks for clarification instead of guessing or mixing corpora.
1. It validates citations server-side against the selected corpus (`corpus`, `secid`, exact quote match).

There is no retrieval index, no provider-swapping path, and no backward-compatibility shim for legacy QA behavior.

## Environment Variables

Copy `.env.example` and set:

- `OPENAI_API_KEY`
- `MODEL_NAME` (default `gpt-5.4`)
- `NON_ZONING_MARKDOWN`
- `ZONING_MARKDOWN`
- `MODEL_TIMEOUT_SECONDS`
- `MAX_HISTORY_MESSAGES`
- `OBSERVABILITY_LOG_LEVEL`

## Running The App

```bash
OPENAI_API_KEY=... MODEL_NAME=gpt-5.4 \
python3 -m uvicorn app.api:app --host 127.0.0.1 --port 8000 --reload
```

## Health Endpoint

`GET /health` returns:

- `status`
- `model`
- `non_zoning_sections`
- `zoning_sections`
- `non_zoning_context_chars`
- `zoning_context_chars`

## Verification

```bash
python3 scripts/verify_app_answers.py --base-url http://127.0.0.1:8000 --suite critical
python3 scripts/verify_app_answers.py --base-url http://127.0.0.1:8000 --suite all
```

Checks assert answer quality plus routing correctness (`routed_corpus`) and citation/corpus consistency.

## Structured Observability

The app logs JSON events (`chat.*`, `qa.*`) with a shared `request_id` for traceability. Routing decisions are logged via `qa.routing_decision`.

## Corpus Generation

Generate source corpora:

```bash
python3 fetch_somerville_law.py
python3 fetch_somerville_zoning.py --skip-pdf-attempt --strip-metadata
```

Optional readable HTML:

```bash
python3 render_markdown_html.py
python3 render_markdown_html.py \
  --input somerville-zoning.md \
  --output somerville-zoning.readable.html \
  --title 'Somerville Zoning Ordinance (Readable Edition)'
```

## Legal Disclaimer

This repository is for informational and research use only and does not constitute legal advice.
