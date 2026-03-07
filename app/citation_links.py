from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

from .config import AppSettings
from .types import CorpusSection

LOCAL_DOCUMENT_PATHS = {
    "non_zoning": "/documents/non-zoning",
    "zoning": "/documents/zoning",
}

OFFICIAL_DOC_VIEWER_URLS = {
    "non_zoning": "https://online.encodeplus.com/regs/somerville-ma-coo/doc-viewer.aspx",
    "zoning": "https://online.encodeplus.com/regs/somerville-ma/doc-viewer.aspx",
}

OFFICIAL_DOC_VIEW_URLS = {
    "non_zoning": "https://online.encodeplus.com/regs/somerville-ma-coo/doc-view.aspx",
    "zoning": "https://online.encodeplus.com/regs/somerville-ma/doc-view.aspx",
}


@dataclass(frozen=True)
class CitationLinks:
    url: str | None
    local_url: str | None
    official_url: str | None


def build_citation_links(section: CorpusSection, settings: AppSettings) -> CitationLinks:
    local_document_path = LOCAL_DOCUMENT_PATHS.get(section.corpus)
    local_file_path = (
        settings.non_zoning_readable_html
        if section.corpus == "non_zoning"
        else settings.zoning_readable_html
    )

    local_url: str | None = None
    if local_document_path and local_file_path.exists():
        local_url = f"{local_document_path}#secid-{quote(section.secid, safe='')}"

    official_url = _build_official_link(section)
    return CitationLinks(
        url=local_url or official_url,
        local_url=local_url,
        official_url=official_url,
    )


def _build_official_link(section: CorpusSection) -> str | None:
    viewer_root = OFFICIAL_DOC_VIEWER_URLS.get(section.corpus)
    if viewer_root and section.secid:
        return f"{viewer_root}#secid-{quote(section.secid, safe='')}"

    tocid_root = OFFICIAL_DOC_VIEW_URLS.get(section.corpus)
    if tocid_root and section.tocid:
        return f"{tocid_root}?tocid={quote(section.tocid, safe='')}"

    return None
