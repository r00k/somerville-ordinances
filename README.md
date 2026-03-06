# Somerville Municipal Law Consolidation

This workspace contains consolidated, LLM-ready extracts of Somerville's non-zoning municipal law from enCodePlus.

## Scope

- Included (default consolidated run): **Part I Charter**, **Part II Code of Ordinances**, and **Appendices B, D, E** from `somerville-ma-coo`.
- Excluded: Somerville's separate zoning publication.
- Source publication root: `https://online.encodeplus.com/regs/somerville-ma-coo/doc-viewer.aspx?tocid=001`

## Scripts

- `fetch_somerville_law.py`
  - Multi-part consolidated extractor (default scope: Part I + Part II + Appendices B, D, E).
  - Produces one markdown file with source secid markers `<!-- secid:... -->`.
- `render_markdown_html.py`
  - Converts the consolidated markdown into a standalone, styled, readable HTML document.
- `fetch_somerville_code.py`
  - Legacy PART II-only extractor retained for compatibility.

## Outputs

- `somerville-law-non-zoning.md`
  - Primary consolidated markdown output (Part I + Part II + Appendices B, D, E).
- `somerville-law-non-zoning.readable.html`
  - Styled HTML rendering optimized for on-screen reading and printing.
- `somerville-law-non-zoning.raw.html`
  - Raw HTML bundle for all included tocids, used for auditability.
- `somerville-code-ordinances-part-ii.pdf`
  - Best-effort host-exported PDF for PART II (`tocid=001.004`) when available.
- `somerville-code-ordinances-part-ii.md`
  - Existing PART II-only markdown output from the legacy script.

## Run

Full non-zoning law consolidation (default scope):

```bash
python3 fetch_somerville_law.py
```

Readable HTML export:

```bash
python3 render_markdown_html.py
```

Legacy PART II-only extraction:

```bash
python3 fetch_somerville_code.py
```

## `fetch_somerville_law.py` Options

- `--tocids <csv>` comma-separated tocid roots in desired order
- `--markdown-output <path>`
- `--html-output <path>`
- `--pdf-output <path>`
- `--pdf-tocid <tocid>`
- `--skip-pdf-attempt`

Default `--tocids` value:

`001.003,001.004,001.008,001.012,001.013`

## Notes

- If the city updates the publication, rerun the script to refresh outputs.
- The document preserves heading structure, lists, tables, and section traceability markers.

## Content Cleanup Rules

`fetch_somerville_law.py` removes non-substantive publication metadata lines from the markdown output, including:

- Standalone ordinance-history parentheticals such as `(Ord. No. ...)`, `(Acts ####, ...)`, `(Code ####, ...)`, and related "pending repeal" history text.
- `Effective on: ...` lines.
- `Editor's note` / `Editor's note(s)` prefixed metadata lines.

This cleanup is applied during markdown assembly so regenerated outputs stay consistent.

## Appendix Scope Clarification

- Top-level **Appendix A** (`tocid=001.007`) and **Appendix C** (`tocid=001.009`) are excluded from the default consolidated scope.
- You may still see internal headings named "Appendix A" or "Appendix C" inside included legal text sections; those are in-document references, not top-level publication roots.
