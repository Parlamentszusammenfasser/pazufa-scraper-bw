"""HTML parsing for the Gesetzblatt Baden-Württemberg.

Source: https://www.baden-wuerttemberg.de/de/service/gesetze-und-verordnungen/gesetzblatt
Detail URL pattern: /de/service/gesetze-und-verordnungen/gesetzblatt/detail/<JAHR>-<NUMMER>
"""

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urljoin, urlparse

from lxml import html

_AUSFERTIGUNG_RE = re.compile(r"Ausfertigung:\s*(\d{2}\.\d{2}\.\d{4})")
_TYP_RE = re.compile(r"Gesetzblatt-Typ:\s*([^\n\r]+?)\s*(?:Federführung|$)", re.DOTALL)
_FEDERFUEHRUNG_RE = re.compile(r"Federführung:\s*(.+?)\s*$", re.DOTALL)
_NUMMER_RE = re.compile(r"Gesetzblatt-Nr\.\s*(\d+)")


@dataclass
class RawGesetzblattDetail:
    """Parsed metadata from a Gesetzblatt detail page."""

    titel: str
    jahr: int
    nummer: int
    publikationsdatum: str  # DD.MM.YYYY (from <time>)
    ausfertigungsdatum: str | None  # DD.MM.YYYY (from "Ausfertigung: …")
    typ: str  # "Gesetz", "Verordnung", "Bekanntmachung", "Berichtigung", …
    federfuehrung: str | None  # e.g. "Innenministerium (IM)"
    pdf_url: str | None  # absolute
    pdf_filename: str | None  # parsed from ?fn= query parameter


def parse_detail(html_content: str, base_url: str) -> RawGesetzblattDetail:
    """Extract metadata + PDF link from a Gesetzblatt detail page."""
    tree = html.fromstring(html_content)

    # Title from the article's h1 (inside the rsmbwlawsheet container)
    h1_els = tree.xpath('//div[contains(@class, "tx-rsmbwlawsheet")]//h1')
    titel = h1_els[0].text_content().strip() if h1_els else ""

    # Gesetzblatt-Nr. from the page-title__category paragraph
    cat_els = tree.xpath('//p[contains(@class, "page-title__category")]')
    nummer = 0
    if cat_els:
        nm = _NUMMER_RE.search(cat_els[0].text_content())
        if nm:
            nummer = int(nm.group(1))

    # Publication date from <time datetime="…">
    time_els = tree.xpath('//time[contains(@class, "article-title__date")]')
    publikationsdatum = ""
    jahr = 0
    if time_els:
        publikationsdatum = time_els[0].text_content().strip()
        dt = time_els[0].get("datetime", "")
        if dt:
            jahr = int(dt[:4])

    # Metadata block: "Ausfertigung: … Gesetzblatt-Typ: … Federführung: …"
    meta_els = tree.xpath('//div[contains(@class, "rsmbwlawsheet_show_text")]')
    ausfertigungsdatum: str | None = None
    typ = ""
    federfuehrung: str | None = None
    if meta_els:
        meta_text = re.sub(r"\s+", " ", meta_els[0].text_content().strip())
        if m := _AUSFERTIGUNG_RE.search(meta_text):
            ausfertigungsdatum = m.group(1)
        if m := _TYP_RE.search(meta_text):
            typ = m.group(1).strip()
        if m := _FEDERFUEHRUNG_RE.search(meta_text):
            federfuehrung = m.group(1).strip()

    # PDF link via TYPO3 dumpFile eID
    pdf_url: str | None = None
    pdf_filename: str | None = None
    pdf_links = tree.xpath('//a[contains(@href, "eID=dumpFile")]')
    if pdf_links:
        href = pdf_links[0].get("href", "")
        pdf_url = href if href.startswith("http") else urljoin(base_url, href)
        params = parse_qs(urlparse(pdf_url).query)
        if params.get("fn"):
            pdf_filename = params["fn"][0]

    return RawGesetzblattDetail(
        titel=titel,
        jahr=jahr,
        nummer=nummer,
        publikationsdatum=publikationsdatum,
        ausfertigungsdatum=ausfertigungsdatum,
        typ=typ,
        federfuehrung=federfuehrung,
        pdf_url=pdf_url,
        pdf_filename=pdf_filename,
    )
