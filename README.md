# Somerville Municipal Law Consolidation

Consolidated, LLM-ready extracts of Somerville's non-zoning municipal law from enCodePlus.

## What This Is

This repository fetches official municipal-law content from Somerville's enCodePlus publication, normalizes it into one machine-friendly Markdown file, and optionally renders a human-friendly HTML reading edition.

## Who This Is For

- People building RAG/LLM workflows over Somerville law text.
- Researchers, civic technologists, and policy teams who want one consolidated non-zoning law corpus.
- Developers who want a reproducible fetch + transform + verification pipeline.

## Default Scope

- Included: **Part I Charter**, **Part II Code of Ordinances**, and **Appendices B, D, E** from `somerville-ma-coo`.
- Excluded: Somerville's separate zoning publication.
- Excluded: top-level **Appendix A** (`tocid=001.007`) and top-level **Appendix C** (`tocid=001.009`).
- Source publication root: `https://online.encodeplus.com/regs/somerville-ma-coo/doc-viewer.aspx?tocid=001`

## Repository Contents

- `fetch_somerville_law.py`: main multi-part extractor and cleaner.
- `render_markdown_html.py`: standalone styled HTML renderer for the consolidated markdown.
- `fetch_somerville_code.py`: legacy Part II-only extractor retained for compatibility.
- `somerville-law-non-zoning.md`: primary consolidated markdown output.
- `somerville-law-non-zoning.raw.html`: bundled raw source HTML used for auditability.
- `somerville-law-non-zoning.readable.html`: styled reading edition.

## Quick Start

```bash
python3 fetch_somerville_law.py
python3 render_markdown_html.py
```

Expected outputs:

- `somerville-law-non-zoning.md`
- `somerville-law-non-zoning.raw.html`
- `somerville-law-non-zoning.readable.html`

## How It Works

```text
enCodePlus doc-view.aspx (selected tocid roots)
  -> fetch_somerville_law.py
  -> HTML parsing + markdown rendering + metadata cleanup
  -> consolidated markdown + raw HTML bundle
  -> render_markdown_html.py (optional)
  -> styled standalone HTML reading edition
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

## Content Cleanup Rules

`fetch_somerville_law.py` removes non-substantive publication metadata lines during markdown assembly, including:

- standalone ordinance-history parentheticals such as `(Ord. No. ...)`, `(Acts ####, ...)`, `(Code ####, ...)`, and related pending-repeal text,
- `Effective on: ...` lines,
- `Editor's note` and `Editor's note(s)` prefixed metadata lines.

This keeps regenerated outputs consistent and easier for downstream NLP/LLM use.

## Known Limitations

- This is a transformed convenience corpus, not a replacement for the official publication.
- If enCodePlus changes source structure, parser logic may need updates.
- Internal text may still reference excluded appendices or zoning; those references are part of included legal text.

## Legal Disclaimer

This repository is for informational and research use only and does not constitute legal advice.

## Legacy Script

Part II-only extraction is still available via:

```bash
python3 fetch_somerville_code.py
```
