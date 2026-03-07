from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .types import CorpusName, CorpusSection


HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
SECID_RE = re.compile(r"^<!--\s*secid:(\d+)\s*-->$")
TOCID_RE = re.compile(r"^<!--\s*tocid:([^\s]+)")


@dataclass(frozen=True)
class CorpusBundle:
    sections: list[CorpusSection]
    by_key: dict[tuple[CorpusName, str], CorpusSection]


def _clean_inline_comment(line: str) -> str:
    stripped = line.strip()
    if stripped.startswith("<!--") and stripped.endswith("-->"):
        return ""
    return line


def _heading_path_from_lines(lines: list[str], fallback: tuple[str, ...]) -> tuple[str, ...]:
    heading_stack = [""] * 6
    captured: tuple[str, ...] | None = None

    for raw in lines:
        match = HEADING_RE.match(raw.strip())
        if not match:
            continue
        hashes, text = match.groups()
        level = len(hashes)
        heading_stack[level - 1] = text.strip()
        for idx in range(level, 6):
            heading_stack[idx] = ""
        path = tuple(part for part in heading_stack if part)
        if path and captured is None:
            captured = path

    return captured or fallback


def parse_markdown_sections(markdown_text: str, corpus: CorpusName, source_file: str) -> list[CorpusSection]:
    lines = markdown_text.splitlines()

    sections: list[CorpusSection] = []
    heading_stack = [""] * 6
    current_secid: str | None = None
    current_lines: list[str] = []
    current_tocid: str | None = None
    current_heading_snapshot: tuple[str, ...] = tuple()

    def flush() -> None:
        nonlocal current_secid, current_lines, current_heading_snapshot
        if current_secid is None:
            return

        cleaned_lines = [_clean_inline_comment(line).rstrip() for line in current_lines]
        body = "\n".join(line for line in cleaned_lines if line.strip()).strip()
        if not body:
            current_secid = None
            current_lines = []
            return

        heading_path = _heading_path_from_lines(current_lines, current_heading_snapshot)
        heading = heading_path[-1] if heading_path else f"Section {current_secid}"
        section = CorpusSection(
            corpus=corpus,
            secid=current_secid,
            tocid=current_tocid,
            heading=heading,
            heading_path=heading_path,
            source_file=source_file,
            ordinal=len(sections),
            text=body,
        )
        sections.append(section)
        current_secid = None
        current_lines = []

    for raw in lines:
        stripped = raw.strip()

        tocid_match = TOCID_RE.match(stripped)
        if tocid_match:
            current_tocid = tocid_match.group(1)

        heading_match = HEADING_RE.match(stripped)
        if heading_match:
            hashes, heading_text = heading_match.groups()
            level = len(hashes)
            heading_stack[level - 1] = heading_text.strip()
            for idx in range(level, 6):
                heading_stack[idx] = ""

        secid_match = SECID_RE.match(stripped)
        if secid_match:
            flush()
            current_secid = secid_match.group(1)
            current_heading_snapshot = tuple(part for part in heading_stack if part)
            continue

        if current_secid is not None:
            current_lines.append(raw)

    flush()
    return sections


def load_corpus_sections(non_zoning_path: Path, zoning_path: Path) -> CorpusBundle:
    non_zoning_md = non_zoning_path.read_text(encoding="utf-8")
    zoning_md = zoning_path.read_text(encoding="utf-8")

    sections: list[CorpusSection] = []
    sections.extend(parse_markdown_sections(non_zoning_md, "non_zoning", non_zoning_path.name))
    sections.extend(parse_markdown_sections(zoning_md, "zoning", zoning_path.name))

    by_key: dict[tuple[CorpusName, str], CorpusSection] = {}
    for section in sections:
        by_key[(section.corpus, section.secid)] = section

    return CorpusBundle(sections=sections, by_key=by_key)
