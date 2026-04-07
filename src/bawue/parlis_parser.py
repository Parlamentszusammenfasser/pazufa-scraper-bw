"""HTML parsing, Fundstelle regex extraction, and JSON comment parsing for PARLIS responses."""

import json
import logging
import re
from datetime import datetime

from lxml import html

from bawue.types import RawVorgang

logger = logging.getLogger(__name__)

_HTML_COMMENT_RE = re.compile(r"<!--(.*?)-->", re.DOTALL)


def _extract_json_comments(html_content: str) -> list[dict]:
    """Extract JSON objects embedded in HTML comments (<!--{...}-->)."""
    results = []
    for match in _HTML_COMMENT_RE.finditer(html_content):
        text = match.group(1).strip()
        if not text.startswith("{"):
            continue
        try:
            results.append(json.loads(text))
        except json.JSONDecodeError:
            logger.debug("Skipping malformed JSON in HTML comment")
    return results


# PARLIS JSON comment field codes
_FIELD_TITEL = "EWBV10"
_FIELD_VORGANGS_ID = "EWBV02"
_FIELD_VORGANGS_ID_ALT = "WMV40"
_FIELD_VORGANGSTYP = "WMV41"
_FIELD_INITIATIVE = "WMV30"
_FIELD_AKTUELLER_STAND = "WMV31"
_FIELD_SACHGEBIET = "WMV32"
_FIELD_FUNDSTELLEN = "WMV35"

_BASE_URL = "https://parlis.landtag-bw.de/parlis/"


def _safe_main(data: dict, key: str) -> str | None:
    """Safely extract data[key][0]["main"], returning None if any level is missing."""
    entries = data.get(key)
    if not entries or not isinstance(entries, list):
        return None
    first = entries[0]
    if not isinstance(first, dict):
        return None
    return first.get("main")


def _parse_wmv35_fundstellen(wmv35_raw: str) -> list[dict]:
    """Parse the WMV35 Fundstellen field into a list of RawFundstelle dicts.

    Format per entry: ``pdf_url @@ blob_id @@ mime @@ description || internal_id``
    Entries are separated by ``<br>``.
    """
    results = []
    seen: set[str] = set()
    segments = re.split(r"\s*<br>\s*", wmv35_raw, flags=re.IGNORECASE)
    for segment in segments:
        segment = segment.strip()
        if not segment:
            continue
        # Deduplicate: PARLIS sometimes repeats identical Fundstelle entries
        if segment in seen:
            continue
        seen.add(segment)
        parts = segment.split(" @@ ")
        pdf_url = parts[0].strip() if parts else ""
        description = parts[3] if len(parts) >= 4 else segment
        # Strip trailing " || internal_id"
        id_sep = description.rfind(" || ")
        if id_sep != -1:
            description = description[:id_sep]
        parsed = parse_fundstelle_text(description.strip())
        if pdf_url:
            parsed["pdf_url"] = pdf_url
        results.append(parsed)
    return results


def _json_comment_to_raw_vorgang(data: dict) -> RawVorgang | None:
    """Convert a PARLIS embedded JSON comment to a RawVorgang dict."""
    vorgangs_id = _safe_main(data, _FIELD_VORGANGS_ID) or _safe_main(data, _FIELD_VORGANGS_ID_ALT)
    if not vorgangs_id:
        return None

    titel = _safe_main(data, _FIELD_TITEL)
    if not titel:
        return None

    result: RawVorgang = {
        "titel": titel,
        "vorgangs_id": vorgangs_id,
        "detail_url": f"{_BASE_URL}vorgang/{vorgangs_id}",
    }

    vorgangstyp = _safe_main(data, _FIELD_VORGANGSTYP)
    if vorgangstyp:
        result["Vorgangstyp"] = vorgangstyp

    initiative = _safe_main(data, _FIELD_INITIATIVE)
    if initiative:
        result["Initiative"] = initiative.strip()

    aktueller_stand = _safe_main(data, _FIELD_AKTUELLER_STAND)
    if aktueller_stand:
        result["Aktueller Stand"] = aktueller_stand

    sachgebiet = _safe_main(data, _FIELD_SACHGEBIET)
    if sachgebiet:
        result["Sachgebiet"] = sachgebiet

    wmv35 = _safe_main(data, _FIELD_FUNDSTELLEN)
    if wmv35:
        result["fundstellen_parsed"] = _parse_wmv35_fundstellen(wmv35)

    return result


_GERMAN_MONTHS = {
    "Januar": "01",
    "Februar": "02",
    "März": "03",
    "April": "04",
    "Mai": "05",
    "Juni": "06",
    "Juli": "07",
    "August": "08",
    "September": "09",
    "Oktober": "10",
    "November": "11",
    "Dezember": "12",
}
_GERMAN_DATE_RE = re.compile(
    r"(\d{1,2})\.\s*(Januar|Februar|März|April|Mai|Juni|Juli|August|"
    r"September|Oktober|November|Dezember)\s+(\d{4})"
)


def parse_fundstelle_text(text: str) -> dict:
    """Parse a Fundstelle text entry into structured station data."""
    result: dict = {"raw": text}

    # DD.MM.YYYY date format used in most Fundstelle entries (years 2000+)
    date_match = re.search(r"(\d{2}\.\d{2}\.20\d{2})", text)
    if date_match:
        result["datum"] = date_match.group(1)

    if "datum" not in result:
        # Fallback: written-out German month names, e.g. "3. März 2024"
        de_match = _GERMAN_DATE_RE.search(text)
        if de_match:
            day = de_match.group(1).zfill(2)
            month = _GERMAN_MONTHS[de_match.group(2)]
            year = de_match.group(3)
            result["datum"] = f"{day}.{month}.{year}"

    # Drucksache number: "Drucksache 17/1234"
    ds_match = re.search(r"Drucksache\s+(\d+/\d+)", text)
    if ds_match:
        result["drucksache"] = ds_match.group(1)

    # Plenarprotokoll number: "Plenarprotokoll 17/42"
    pp_match = re.search(r"Plenarprotokoll\s+(\d+/\d+)", text)
    if pp_match:
        result["plenarprotokoll"] = pp_match.group(1)

    # Station type: leading word(s) before a double-space or single-tab separator.
    # \t is listed separately because \s{2,} requires two chars and would miss a lone tab.
    type_match = re.match(r"^([\w\s\-äöüÄÖÜß]+?)(?:\s{2,}|\t)", text)
    if type_match:
        result["station_typ"] = type_match.group(1).strip()

    # Committee name: "Ausschuss für ..." up to a date or "Drucksache" keyword
    ausschuss_match = re.search(
        r"(Ausschuss\s+für\s+\D+?)(?:\s+\d{2}\.\d{2}\.|\s+Drucksache)",
        text,
    )
    if ausschuss_match:
        result["ausschuss"] = ausschuss_match.group(1).strip()

    # Author text: anything between the station type and the date that is not a
    # known keyword (committees and protocols have their own fields above)
    if type_match and date_match:
        gap_text = text[type_match.end() : date_match.start()].strip()
        if gap_text and not gap_text.startswith(("Ausschuss", "Plenarprotokoll")):
            result["autor_text"] = gap_text

    # Page count: "(42 S.)" → 42
    pages_match = re.search(r"\((\d+)\s+S\.\)", text)
    if pages_match:
        result["seiten"] = int(pages_match.group(1))

    return result


def _find_date_in_parent_time_elements(link) -> str | None:
    """Walk up to parent and grandparent to find a <time datetime="…"> element.

    PARLIS sometimes places the date in a sibling <time> element rather than
    inside the link text itself.  Returns a DD.MM.YYYY string or None.
    """
    parent = link.getparent()
    grandparent = parent.getparent() if parent is not None else None
    for ancestor in (parent, grandparent):
        if ancestor is None:
            continue
        time_els = ancestor.xpath(".//time[@datetime]")
        if time_els:
            iso_date = time_els[0].get("datetime")
            try:
                return datetime.strptime(iso_date, "%Y-%m-%d").strftime("%d.%m.%Y")
            except ValueError:
                return None
    return None


def _extract_fundstellen(record) -> list[dict]:
    """Parse all Fundstelle links within a record element."""
    fund_links = record.xpath('.//a[@class="fundstellenLinks"]')
    fundstellen = []
    for link in fund_links:
        parsed = parse_fundstelle_text(link.text_content().strip())
        parsed["pdf_url"] = link.get("href", "")

        # Date may not appear in the link text — try nearby <time> elements
        if "datum" not in parsed:
            date = _find_date_in_parent_time_elements(link)
            if date:
                parsed["datum"] = date

        fundstellen.append(parsed)
    return fundstellen


def _extract_from_scripts(record) -> tuple[str | None, str | None]:
    """Extract vorgangs_id and detail_url from inline <script> blocks.

    PARLIS embeds the Vorgang ID in JavaScript as both:
      - "link-V-1234"  (used as a fallback when the <dl> metadata is missing)
      - '"/parlis/vorgang/V-1234"'  (used to build the canonical detail URL)
    """
    vorgangs_id = None
    detail_url = None
    for script in record.xpath(".//script"):
        script_text = script.text_content()

        # Pattern: link-V-<digits>  — JS anchor ID referencing the Vorgang
        if vorgangs_id is None:
            vid_match = re.search(r"link-(V-\d+)", script_text)
            if vid_match:
                vorgangs_id = vid_match.group(1)

        # Pattern: "/parlis/vorgang/V-<digits>"  — path used in the detail link
        if detail_url is None:
            url_match = re.search(r'"/parlis/vorgang/(V-\d+)"', script_text)
            if url_match:
                detail_url = f"https://parlis.landtag-bw.de/parlis/vorgang/{url_match.group(1)}"

        if vorgangs_id and detail_url:
            break

    return vorgangs_id, detail_url


def _parse_results_from_json(json_comments: list[dict]) -> list[RawVorgang]:
    """Convert a list of PARLIS JSON comment dicts to RawVorgang list."""
    results = []
    for data in json_comments:
        vorgang = _json_comment_to_raw_vorgang(data)
        if vorgang:
            results.append(vorgang)
    return results


def _parse_results_from_html(html_content: str) -> list[RawVorgang]:
    """Parse Vorgang results from PARLIS HTML response (XPath/regex fallback)."""
    tree = html.fromstring(html_content)
    records = tree.xpath('.//div[contains(@class, "efxRecordRepeater")]')

    results = []
    for record in records:
        item: dict = {}

        title_links = record.xpath('.//a[@class="efxZoomShort-Vorgang"]')
        if title_links:
            item["titel"] = title_links[0].text_content().strip()

        # Extract <dl> metadata key/value pairs (e.g. Vorgangstyp, Status, …)
        for dt in record.xpath(".//dl/dt"):
            label = dt.text_content().strip().rstrip(":")
            if label == "Vorgangs-ID":
                label = "vorgangs_id"
            dd = dt.getnext()
            if dd is not None:
                item[label] = dd.text_content().strip()

        fundstellen = _extract_fundstellen(record)
        if fundstellen:
            item["fundstellen_parsed"] = fundstellen

        script_id, detail_url = _extract_from_scripts(record)
        # Only use the script-derived ID when the <dl> didn't already provide one
        if script_id and "vorgangs_id" not in item:
            item["vorgangs_id"] = script_id
        if detail_url:
            item["detail_url"] = detail_url

        if item:
            results.append(item)

    return results


def parse_results(html_content: str) -> list[RawVorgang]:
    """Parse Vorgang results from PARLIS HTML response.

    Tries JSON comment extraction first (more robust), falls back to HTML/XPath
    parsing when no embedded JSON comments are found.
    """
    json_comments = _extract_json_comments(html_content)
    if json_comments:
        results = _parse_results_from_json(json_comments)
        if results:
            return results
        logger.warning("JSON comments found but yielded no results, falling back to HTML parsing")
    return _parse_results_from_html(html_content)
