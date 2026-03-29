"""HTML parsing for the Beteiligungsportal Baden-Württemberg."""

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin

from lxml import html


@dataclass
class RawBeteiligungProcess:
    """A process entry from the LP index page."""

    title: str
    url: str
    slug: str
    status: str  # "open" | "closed"


@dataclass
class RawBeteiligungDetail:
    """Parsed metadata from a process detail page."""

    title: str
    ministry: str
    pdf_links: list[dict] = field(default_factory=list)
    comment_deadline: str | None = None
    phases: list[str] = field(default_factory=list)


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

    # PDF links — links with class "link-download-block" pointing to .pdf
    pdf_links = []
    for link in tree.xpath('//a[contains(@class, "link-download-block")]'):
        href = link.get("href", "")
        if href.endswith(".pdf"):
            pdf_url = urljoin(base_url, href) if not href.startswith("http") else href
            pdf_title = link.text_content().strip()
            pdf_links.append({"title": pdf_title, "url": pdf_url})

    # Comment deadline from comment-list__closed announcement
    comment_deadline = None
    closed_els = tree.xpath('//div[contains(@class, "comment-list__closed")]//p')
    for el in closed_els:
        text = el.text_content()
        date_match = re.search(r"bis zum (\d{1,2})\.\s*(\w+)\s+(\d{4})", text)
        if date_match:
            day = int(date_match.group(1))
            month_name = date_match.group(2)
            year = date_match.group(3)
            month_num = _month_name_to_number(month_name)
            if month_num:
                comment_deadline = f"{day:02d}.{month_num:02d}.{year}"

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
        comment_deadline=comment_deadline,
        phases=phases,
    )


_GERMAN_MONTHS = {
    "januar": 1,
    "februar": 2,
    "märz": 3,
    "april": 4,
    "mai": 5,
    "juni": 6,
    "juli": 7,
    "august": 8,
    "september": 9,
    "oktober": 10,
    "november": 11,
    "dezember": 12,
}


def _month_name_to_number(name: str) -> int | None:
    return _GERMAN_MONTHS.get(name.lower())
