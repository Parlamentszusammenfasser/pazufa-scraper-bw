"""HTML parsing and Fundstelle regex extraction for PARLIS responses."""

import re
from datetime import datetime

from lxml import html

from bawue.types import RawVorgang

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

    date_match = re.search(r"(\d{2}\.\d{2}\.\d{4})", text)
    if date_match:
        result["datum"] = date_match.group(1)

    if "datum" not in result:
        de_match = _GERMAN_DATE_RE.search(text)
        if de_match:
            day = de_match.group(1).zfill(2)
            month = _GERMAN_MONTHS[de_match.group(2)]
            year = de_match.group(3)
            result["datum"] = f"{day}.{month}.{year}"

    ds_match = re.search(r"Drucksache\s+(\d+/\d+)", text)
    if ds_match:
        result["drucksache"] = ds_match.group(1)

    pp_match = re.search(r"Plenarprotokoll\s+(\d+/\d+)", text)
    if pp_match:
        result["plenarprotokoll"] = pp_match.group(1)

    type_match = re.match(r"^([\w\s\-äöüÄÖÜß]+?)(?:\s{2,}|\t)", text)
    if type_match:
        result["station_typ"] = type_match.group(1).strip()

    ausschuss_match = re.search(
        r"(Ausschuss\s+(?:für|fuer)\s+[^0-9]+?)(?:\s+\d{2}\.\d{2}\.|\s+Drucksache)",
        text,
    )
    if ausschuss_match:
        result["ausschuss"] = ausschuss_match.group(1).strip()

    if type_match and date_match:
        gap_text = text[type_match.end() : date_match.start()].strip()
        if gap_text and not gap_text.startswith(("Ausschuss", "Plenarprotokoll")):
            result["autor_text"] = gap_text

    pages_match = re.search(r"\((\d+)\s+S\.\)", text)
    if pages_match:
        result["seiten"] = int(pages_match.group(1))

    return result


def parse_results(html_content: str) -> list[RawVorgang]:
    """Parse Vorgang results from PARLIS HTML response."""
    tree = html.fromstring(html_content)
    records = tree.xpath('.//div[contains(@class, "efxRecordRepeater")]')

    results = []
    for record in records:
        item: dict = {}

        title_links = record.xpath('.//a[@class="efxZoomShort-Vorgang"]')
        if title_links:
            item["titel"] = title_links[0].text_content().strip()

        dts = record.xpath(".//dl/dt")
        for dt in dts:
            label = dt.text_content().strip().rstrip(":")
            if label == "Vorgangs-ID":
                label = "vorgangs_id"
            dd = dt.getnext()
            if dd is not None:
                item[label] = dd.text_content().strip()

        fund_links = record.xpath('.//a[@class="fundstellenLinks"]')
        if fund_links:
            item["fundstellen_parsed"] = []
            for link in fund_links:
                text = link.text_content().strip()
                href = link.get("href", "")
                parsed = parse_fundstelle_text(text)
                parsed["pdf_url"] = href

                if "datum" not in parsed:
                    parent = link.getparent()
                    time_els = parent.xpath('.//time[@datetime]') if parent is not None else []
                    if time_els:
                        iso_date = time_els[0].get("datetime")
                        try:
                            dt = datetime.strptime(iso_date, "%Y-%m-%d")
                            parsed["datum"] = dt.strftime("%d.%m.%Y")
                        except ValueError:
                            pass

                item["fundstellen_parsed"].append(parsed)

        scripts = record.xpath(".//script")
        for script in scripts:
            script_text = script.text_content()
            vid_match = re.search(r"link-(V-\d+)", script_text)
            if vid_match and "vorgangs_id" not in item:
                item["vorgangs_id"] = vid_match.group(1)

        url_match = None
        for script in scripts:
            url_match = re.search(r'"/parlis/vorgang/(V-\d+)"', script.text_content())
            if url_match:
                break
        if url_match:
            item["detail_url"] = f"https://parlis.landtag-bw.de/parlis/vorgang/{url_match.group(1)}"

        if item:
            results.append(item)

    return results
