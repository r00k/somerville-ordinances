# Somerville Municipal Law Consolidation

Consolidated, LLM-ready extracts of Somerville municipal law from enCodePlus.

## What This Is

This repository fetches official municipal-law content from Somerville's enCodePlus publications, normalizes it into machine-friendly Markdown, and optionally renders a human-friendly HTML reading edition.

## Who This Is For

- People building RAG/LLM workflows over Somerville law text.
- Researchers, civic technologists, and policy teams who want consolidated non-zoning and zoning law corpora.
- Developers who want a reproducible fetch + transform + verification pipeline.

## Default Scope

- `fetch_somerville_law.py` default scope: **Part I Charter**, **Part II Code of Ordinances**, and **Appendices B, D, E** from `somerville-ma-coo`.
- `fetch_somerville_law.py` exclusions: top-level **Appendix A** (`tocid=001.007`), top-level **Appendix C** (`tocid=001.009`), and Somerville's separate zoning publication.
- `fetch_somerville_law.py` source root: `https://online.encodeplus.com/regs/somerville-ma-coo/doc-viewer.aspx?tocid=001`.
- `fetch_somerville_zoning.py` default scope: full zoning ordinance from `somerville-ma` `tocid=001`.
- `fetch_somerville_zoning.py` source root: `https://online.encodeplus.com/regs/somerville-ma/doc-viewer.aspx#secid--1`.

## Repository Contents

- `fetch_somerville_law.py`: main multi-part extractor and cleaner.
- `fetch_somerville_zoning.py`: zoning ordinance extractor with image placeholders + image manifest.
- `render_markdown_html.py`: standalone styled HTML renderer for the consolidated markdown.
- `somerville-law-non-zoning.md`: primary consolidated markdown output.
- `somerville-law-non-zoning.raw.html`: bundled raw source HTML used for auditability.
- `somerville-law-non-zoning.readable.html`: styled reading edition.
- `somerville-zoning.md`: primary zoning markdown output.
- `somerville-zoning.raw.html`: raw zoning source HTML bundle.
- `somerville-zoning.images.json`: zoning image/figure placeholder manifest.

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

## Zoning Extract (Text + Image Placeholders)

`fetch_somerville_zoning.py` targets Somerville's separate zoning publication at:

`https://online.encodeplus.com/regs/somerville-ma/doc-viewer.aspx#secid--1`

Default behavior:

- Fetches `doc-view.aspx?tocid=001` (full zoning ordinance).
- Preserves one `<!-- secid:... -->` marker per extracted section.
- Emits inline placeholders for images and embedded media (instead of dropping them).
- Writes a machine-readable image manifest JSON with section IDs and source URLs.

Useful options:

- `--skip-pdf-attempt` skip host PDF export (faster, fewer network calls).
- `--strip-metadata` remove publication metadata lines such as `Effective on: ...`.
- `--markdown-output`, `--html-output`, `--images-output`, `--pdf-output` for custom paths.

## How It Works

```text
Non-zoning pipeline:
enCodePlus (somerville-ma-coo) doc-view.aspx (selected tocid roots)
  -> fetch_somerville_law.py
  -> HTML parsing + markdown rendering + metadata cleanup
  -> somerville-law-non-zoning.md + somerville-law-non-zoning.raw.html
  -> render_markdown_html.py (optional)
  -> somerville-law-non-zoning.readable.html

Zoning pipeline:
enCodePlus (somerville-ma) doc-view.aspx?tocid=001
  -> fetch_somerville_zoning.py
  -> HTML parsing + markdown rendering + image placeholders + image manifest
  -> somerville-zoning.md + somerville-zoning.raw.html + somerville-zoning.images.json
  -> render_markdown_html.py (optional)
  -> somerville-zoning.readable.html
```

## Configuration

`fetch_somerville_law.py` options:

- `--tocids <csv>` comma-separated tocid roots in desired order.
- `--markdown-output <path>` output markdown path.
- `--html-output <path>` output raw HTML bundle path.
- `--pdf-output <path>` output path for optional host-exported PDF.
- `--pdf-tocid <tocid>` tocid used for optional host PDF export.
- `--skip-pdf-attempt` skip PDF export attempts.

Default `--tocids` value:

`001.003,001.004,001.008,001.012,001.013`

`fetch_somerville_zoning.py` options:

- `--tocid <tocid>` root tocid to extract (default: `001`).
- `--markdown-output <path>` output markdown path.
- `--html-output <path>` output raw HTML path.
- `--images-output <path>` output image manifest JSON path.
- `--pdf-output <path>` output path for optional host-exported PDF.
- `--skip-pdf-attempt` skip PDF export attempts.
- `--strip-metadata` remove publication metadata lines (off by default).

## Customization Examples

```bash
# Only Part I + Part II
python3 fetch_somerville_law.py --tocids 001.003,001.004

# Add output paths explicitly
python3 fetch_somerville_law.py \
  --markdown-output out/somerville.md \
  --html-output out/somerville.raw.html

# Skip PDF export attempt (faster, fewer network calls)
python3 fetch_somerville_law.py --skip-pdf-attempt

# Zoning extract with cleaner text for LLMs
python3 fetch_somerville_zoning.py --skip-pdf-attempt --strip-metadata
```

## Verification And Traceability

- The markdown includes one `<!-- secid:... -->` marker per extracted section.
- The raw HTML bundle preserves fetched source pages for audit and diff checks.
- A strict completeness check compares `data-secid` values in raw HTML to markdown markers.

```bash
python3 - <<'PY'
import re
from pathlib import Path
raw = Path('somerville-law-non-zoning.raw.html').read_text(encoding='utf-8')
md = Path('somerville-law-non-zoning.md').read_text(encoding='utf-8')
raw_ids = re.findall(r"data-secid=['\"](\d+)['\"]", raw)
md_ids = re.findall(r"<!--\s*secid:(\d+)\s*-->", md)
print('raw_total=', len(raw_ids), 'md_total=', len(md_ids), 'sequence_exact=', raw_ids == md_ids)
PY
```

```bash
python3 - <<'PY'
import json
import re
from pathlib import Path

raw = Path('somerville-zoning.raw.html').read_text(encoding='utf-8')
md = Path('somerville-zoning.md').read_text(encoding='utf-8')
images = json.loads(Path('somerville-zoning.images.json').read_text(encoding='utf-8'))

raw_ids = re.findall(r"data-secid=['\"](\d+)['\"]", raw)
md_ids = re.findall(r"<!--\s*secid:(\d+)\s*-->", md)
placeholders = re.findall(r"\[(?:Image|Embedded media)\s+\d+", md)

print('zoning_raw_total=', len(raw_ids), 'zoning_md_total=', len(md_ids), 'sequence_exact=', raw_ids == md_ids)
print('zoning_placeholder_total=', len(placeholders), 'zoning_manifest_total=', len(images), 'counts_match=', len(placeholders) == len(images))
PY
```

## Content Cleanup Rules

`fetch_somerville_law.py` removes non-substantive publication metadata lines during markdown assembly, including:

- standalone ordinance-history parentheticals such as `(Ord. No. ...)`, `(Acts ####, ...)`, `(Code ####, ...)`, and related pending-repeal text,
- `Effective on: ...` lines,
- `Editor's note` and `Editor's note(s)` prefixed metadata lines.

This keeps regenerated outputs consistent and easier for downstream NLP/LLM use.

## Known Limitations

- This is a transformed convenience corpus, not a replacement for the official publication.
- If enCodePlus changes source structure, parser logic may need updates.
- Internal text may still reference excluded appendices or external materials; those references are part of included legal text.
- Zoning images are represented as placeholders by default; image binaries are not downloaded locally.

## Legal Disclaimer

This repository is for informational and research use only and does not constitute legal advice.
