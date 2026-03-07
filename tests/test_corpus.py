from __future__ import annotations

from app.corpus import parse_markdown_sections


def test_parse_markdown_sections_extracts_sections() -> None:
    markdown = """
# Title
<!-- tocid:001.004 title:PART II source:https://example.test breadcrumbs:A > B -->
<!-- secid:10 -->
## Section One
The first line.

<!-- secid:11 -->
## Section Two
The second line.
""".strip()

    sections = parse_markdown_sections(markdown, corpus="non_zoning", source_file="sample.md")

    assert len(sections) == 2
    assert sections[0].secid == "10"
    assert sections[0].heading == "Section One"
    assert "The first line" in sections[0].text
    assert sections[1].secid == "11"
    assert sections[1].heading == "Section Two"
