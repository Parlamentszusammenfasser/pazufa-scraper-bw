"""HTML parsing for the Beteiligungsportal Baden-Württemberg."""

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

from lxml import html


@dataclass
class RawBeteiligungProcess:
    """A process entry from the LP index page."""

    title: str
    url: str
    slug: str
    status: str  # "open" | "closed"


# Days count in the portal's calendar, as in DD-068.
BERLIN_TZ = ZoneInfo("Europe/Berlin")

# Phases that only a draft's timeline has (issue #123, DD-007).
_DRAFT_PHASES = {"Online-Kommentierung", "Gesetzentwurf"}


@dataclass
class RawBeteiligungDetail:
    """Parsed metadata from a process detail page."""

    title: str
    ministry: str
    pdf_links: list[dict] = field(default_factory=list)
    phases: list[str] = field(default_factory=list)

    @property
    def is_verordnung(self) -> bool:
        """Only Verordnung pages carry this phase; Gesetz pages never do (issue #71, DD-007)."""
        return any(phase.startswith("Beschluss der geltenden Verordnung") for phase in self.phases)

    @property
    def is_draft(self) -> bool:
        """A draft is open for comment or published as such; strategy pages and
        Bürgerforen are neither, though they carry PDFs too (issue #123, DD-007)."""
        return any(phase in _DRAFT_PHASES for phase in self.phases)


def parse_process_list(html_content: str) -> list[RawBeteiligungProcess]:
    """Extract processes from the LP index page."""
    tree = html.fromstring(html_content)
    articles = tree.xpath('//article[contains(@class, "teaser")]')

    processes = []
    for article in articles:
        # Title from teaser__headline h2
        headline_els = article.xpath('.//div[contains(@class, "teaser__headline")]//h2')
        if not headline_els:
            continue
        title = headline_els[0].text_content().strip().replace("\xad", "")

        # URL from teaser__overlay-link
        link_els = article.xpath('.//a[contains(@class, "teaser__overlay-link")]')
        if not link_els:
            continue
        url = link_els[0].get("href", "")

        # Slug: last path segment
        slug = url.rstrip("/").rsplit("/", 1)[-1] if url else ""

        # Status: badge text — "Mitmachen" = open, "Abgeschlossen" = closed
        badge_els = article.xpath('.//span[contains(@class, "teaser__badge-text")]')
        if badge_els:
            badge_text = badge_els[0].text_content().strip()
            status = "open" if badge_text == "Mitmachen" else "closed"
        else:
            status = "open"

        processes.append(RawBeteiligungProcess(title=title, url=url, slug=slug, status=status))

    return processes


def parse_process_detail(html_content: str, base_url: str) -> RawBeteiligungDetail:
    """Extract metadata and PDFs from a process detail page."""
    tree = html.fromstring(html_content)

    # Title: try dossier-header template first, then article template fallback
    title_els = tree.xpath('//header[contains(@class, "dossier-header")]//h1')
    if not title_els:
        title_els = tree.xpath("//main//article//h1")
    title = title_els[0].text_content().strip().replace("\xad", "") if title_els else ""

    # Ministry from contact-box headline
    ministry_els = tree.xpath('//div[contains(@class, "contact-box__headline")]//h3')
    ministry = ministry_els[0].text_content().strip() if ministry_els else ""

    # PDF links from both download markups, restricted to portal-hosted files (DD-007)
    pdf_links = []
    seen_urls = set()
    portal_host = urlparse(base_url).netloc.lower()
    for link in tree.xpath('//a[contains(@class, "link-download-block") or contains(@class, "link-list__link")]'):
        href = link.get("href", "")
        if not href.endswith(".pdf"):
            continue
        pdf_url = urljoin(base_url, href)
        if urlparse(pdf_url).netloc.lower() != portal_host or pdf_url in seen_urls:
            continue
        seen_urls.add(pdf_url)
        pdf_links.append({"title": " ".join(link.text_content().split()), "url": pdf_url})

    # Phases from phase-timeline
    phases = []
    for phase_el in tree.xpath('//li[contains(@class, "phase-timeline__item")]'):
        headline_els = phase_el.xpath('.//span[contains(@class, "phase-timeline__item-headline")]')
        if headline_els:
            phases.append(headline_els[0].text_content().strip())

    return RawBeteiligungDetail(
        title=title,
        ministry=ministry,
        pdf_links=pdf_links,
        phases=phases,
    )


# A file name starts with its date, YYMMDD_ (260922_Entwurf.pdf).
_FILE_NAME_DATE_RE = re.compile(r"/(\d{6})[_-][^/]*$")


def release_date(pdf_url: str, last_modified: date | None, earliest: date) -> date | None:
    """When the portal published *pdf_url* (issue #123, DD-074): its upload day
    (``Last-Modified``) or, if earlier, the date in its file name, which survives a
    re-upload. File names are typed by hand (``200818_`` for 2026-08-18), so that date
    counts only from *earliest* up to the upload day."""
    candidates = [last_modified] if last_modified else []
    named = _file_name_date(pdf_url)
    if named and earliest <= named <= (last_modified or datetime.now(BERLIN_TZ).date()):
        candidates.append(named)
    return min(candidates, default=None)


def _file_name_date(pdf_url: str) -> date | None:
    match = _FILE_NAME_DATE_RE.search(urlparse(pdf_url).path)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%y%m%d").date()
    except ValueError:  # 261399_
        return None
