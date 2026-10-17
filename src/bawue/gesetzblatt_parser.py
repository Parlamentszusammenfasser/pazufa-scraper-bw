"""HTML parsing for the Gesetzblatt Baden-Württemberg.

Source: https://www.baden-wuerttemberg.de/de/service/gesetze-und-verordnungen/gesetzblatt
Detail URL pattern: /de/service/gesetze-und-verordnungen/gesetzblatt/detail/<JAHR>-<NUMMER>
"""

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta
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


_MONATE = (
    "Januar",
    "Februar",
    "März",
    "April",
    "Mai",
    "Juni",
    "Juli",
    "August",
    "September",
    "Oktober",
    "November",
    "Dezember",
)
_ORDINALE = (
    "",
    "zweiten",
    "dritten",
    "vierten",
    "fünften",
    "sechsten",
    "siebten",
    "achten",
    "neunten",
    "zehnten",
    "elften",
    "zwölften",
)
_ZAHLEN = {
    "einen": 1,
    "zwei": 2,
    "drei": 3,
    "vier": 4,
    "fünf": 5,
    "sechs": 6,
    "sieben": 7,
    "acht": 8,
    "neun": 9,
    "zehn": 10,
    "elf": 11,
    "zwölf": 12,
}
_HEADING_RE = re.compile(r"^(?:Artikel|§)\s*\d+[a-z]?\s*$", re.MULTILINE)
# The law's own text ends at its signature; a Staatsvertrag is published after it.
_SIGNATURE = "Stuttgart, den"
_WHEN_RE = re.compile(
    r"(?:(?P<tag>am Tag (?P<nach>nach )?(?:seiner|der) Verkündung)"
    r"|(?:am|mit Wirkung vom) (?P<d>\d{1,2})\. ?(?P<m>" + "|".join(_MONATE) + r") (?P<y>\d{4})"
    r"|am ersten Tag des (?P<uebern>übernächsten )?auf (?:die|seine) Verkündung folgenden"
    r" (?:(?P<ord>\w+) )?(?:Kalender)?[mM]onats"
    r"|(?P<n>\w+) Monate? nach (?:seiner|der) Verkündung)"
    r"(?=(?:(?!Kraft).){0,300}?\b(?P<kind>in|außer) Kraft)"
)
# "in Kraft" without a date of its own: a treaty's date announced later, or "Gleichzeitig" repeating one.
_NO_OWN_DATE_RE = re.compile(r"an dem (?:(?!Kraft).)*\bin Kraft tr|Gleichzeitig (?:(?!Kraft).)*\bin Kraft")


def _add_months(d: date, months: int, first_of_month: bool) -> date:
    m = d.month - 1 + months
    year, month = d.year + m // 12, m % 12 + 1
    return date(year, month, 1 if first_of_month else min(d.day, calendar.monthrange(year, month)[1]))


def parse_inkrafttreten(text: str, verkuendung: date) -> list[date]:
    """Every date on which (part of) the law enters into force, in clause order (DD-071).

    Reads the law's final Artikel/§ before its signature; amended texts quoted earlier
    in an Artikelgesetz never count. Empty when any "in Kraft" of that article has no
    resolvable date (a condition, another law's date): no guessing.
    """
    end = text.find(_SIGNATURE)
    body = text if end < 0 else text[:end]
    headings = list(_HEADING_RE.finditer(body))
    if not headings:
        return []
    article = re.sub(r"\s+", " ", body[headings[-1].start() :])

    dates = []
    for m in _WHEN_RE.finditer(article):
        if m.group("kind") != "in":
            continue
        if m.group("tag"):
            dates.append(verkuendung + timedelta(days=1 if m.group("nach") else 0))
        elif m.group("y"):
            dates.append(date(int(m.group("y")), _MONATE.index(m.group("m")) + 1, int(m.group("d"))))
        elif m.group("n"):
            if m.group("n") not in _ZAHLEN:
                return []
            dates.append(_add_months(verkuendung, _ZAHLEN[m.group("n")], first_of_month=False))
        else:
            ordinal = m.group("ord") or ""
            if ordinal not in _ORDINALE:
                return []
            months = 2 if m.group("uebern") else _ORDINALE.index(ordinal) + 1
            dates.append(_add_months(verkuendung, months, first_of_month=True))

    in_kraft = len(re.findall(r"\bin Kraft", article)) - len(_NO_OWN_DATE_RE.findall(article))
    return dates if dates and len(dates) >= in_kraft else []
