"""Dry-run diagnostic script — runs scraper components without posting to the API.

Usage:
    python -m bawue.dry_run --scraper vorgaenge --limit 5 --verbosity 2
    python -m bawue.dry_run --json --limit 2
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date, timedelta

import requests
from icalendar import Calendar

from bawue.bawue_vorgaenge_scraper import BawueVorgaengeScraper
from bawue.beteiligung_client import BeteiligungClient
from bawue.beteiligung_parser import parse_process_detail
from bawue.enum_mapper import VORGANGSTYP_MAP
from bawue.ics_parser import group_events_by_date, parse_ics_feed
from bawue.parlis_client import ParlisClient
from bawue.report import (
    VorgangReport,
    analyze_beteiligung,
    analyze_sitzungen,
    analyze_vorgang,
    build_summary,
    format_summary,
)

logger = logging.getLogger(__name__)

DEFAULT_ICS_URL = "https://www.landtag-bw.de/resource/calendar/501552/download/terminkalender.ics"


# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PaZuFa BaWue dry-run diagnostic report")
    parser.add_argument(
        "--scraper",
        choices=["vorgaenge", "beteiligung", "sitzungen", "all"],
        default="all",
        help="Which scraper to run (default: all)",
    )
    parser.add_argument("--vorgangstyp", type=str, default=None, help="Limit to one PARLIS Vorgangstyp")
    parser.add_argument("--lookback-days", type=int, default=7, help="Days to look back (default: 7)")
    parser.add_argument("--wahlperiode", type=int, default=17, help="Wahlperiode (default: 17)")
    parser.add_argument("--limit", type=int, default=None, help="Max items per scraper")
    parser.add_argument("--verbosity", type=int, choices=[0, 1, 2], default=0, help="Output detail level")
    parser.add_argument("--json", action="store_true", help="Output JSON instead of formatted text")
    parser.add_argument("--ics-url", type=str, default=DEFAULT_ICS_URL, help="ICS calendar URL")
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Orchestration: Vorgaenge
# ---------------------------------------------------------------------------


def run_vorgaenge(
    *,
    wahlperiode: int = 17,
    lookback_days: int = 7,
    vorgangstypen: list[str] | None = None,
    limit: int | None = None,
) -> tuple[list[VorgangReport], list[dict]]:
    """Search PARLIS for each Vorgangstyp and analyze results.

    Returns (vorgang_reports, raw_vorgaenge_list).
    """
    if vorgangstypen is None:
        vorgangstypen = list(VORGANGSTYP_MAP.keys())

    date_to = date.today()
    date_from = date_to - timedelta(days=lookback_days)

    client = ParlisClient(wahlperiode=wahlperiode, request_delay_s=1.0)

    # Use object.__new__ to access _build_vorgang without full scraper init
    scraper = object.__new__(BawueVorgaengeScraper)
    scraper._wahlperiode = wahlperiode

    all_reports: list[VorgangReport] = []
    all_raw: list[dict] = []

    for vt in vorgangstypen:
        logger.info("Searching PARLIS for '%s' (%s to %s)...", vt, date_from, date_to)
        raw_vorgaenge = client.search(vt, date_from, date_to)
        logger.info("  -> %d results", len(raw_vorgaenge))

        for raw in raw_vorgaenge:
            if limit is not None and len(all_reports) >= limit:
                break
            vorgang = scraper._build_vorgang(raw)
            report = analyze_vorgang(raw, vorgang)
            all_reports.append(report)
            all_raw.append(raw)

        if limit is not None and len(all_reports) >= limit:
            break

    return all_reports, all_raw


# ---------------------------------------------------------------------------
# Orchestration: Beteiligung
# ---------------------------------------------------------------------------


def run_beteiligung(
    *,
    wahlperiode: int = 17,
    limit: int | None = None,
) -> list:
    """Fetch Beteiligungsportal processes and analyze."""
    from bawue.beteiligung_client import BASE_URL

    client = BeteiligungClient(wahlperiode=wahlperiode, request_delay_s=2.0)
    processes = client.fetch_process_list()
    logger.info("Found %d Beteiligung processes", len(processes))

    if limit is not None:
        processes = processes[:limit]

    reports = []
    for process in processes:
        html = client.fetch_process_detail(process.url)
        detail = parse_process_detail(html, BASE_URL)
        report = analyze_beteiligung(process, detail)
        reports.append(report)

    return reports


# ---------------------------------------------------------------------------
# Orchestration: Sitzungen
# ---------------------------------------------------------------------------


def run_sitzungen(
    *,
    ics_url: str = DEFAULT_ICS_URL,
) -> tuple[list, int, int]:
    """Fetch ICS feed and analyze session events.

    Returns (sitzung_reports, total_all_events, total_filtered_events).
    """
    logger.info("Fetching ICS feed: %s", ics_url)
    resp = requests.get(ics_url, timeout=30)
    resp.raise_for_status()

    # Count total VEVENT components before filtering
    cal = Calendar.from_ical(resp.content)
    total_all = sum(1 for c in cal.walk() if c.name == "VEVENT")

    events = parse_ics_feed(resp.content)
    total_filtered = total_all - len(events)

    grouped = group_events_by_date(events)

    reports = []
    for dt, evts in sorted(grouped.items()):
        report = analyze_sitzungen(evts, dt.isoformat())
        reports.append(report)

    return reports, total_all, total_filtered


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    start = time.monotonic()

    vorgang_reports: list[VorgangReport] = []
    raw_vorgaenge: list[dict] = []
    beteiligung_reports: list = []
    sitzung_reports: list = []
    sitzung_total_all = 0
    sitzung_total_filtered = 0

    # Determine which scrapers to run
    run_v = args.scraper in ("all", "vorgaenge")
    run_b = args.scraper in ("all", "beteiligung")
    run_s = args.scraper in ("all", "sitzungen")

    if run_v:
        vorgangstypen = [args.vorgangstyp] if args.vorgangstyp else None
        vorgang_reports, raw_vorgaenge = run_vorgaenge(
            wahlperiode=args.wahlperiode,
            lookback_days=args.lookback_days,
            vorgangstypen=vorgangstypen,
            limit=args.limit,
        )

    if run_b:
        beteiligung_reports = run_beteiligung(
            wahlperiode=args.wahlperiode,
            limit=args.limit,
        )

    if run_s:
        sitzung_reports, sitzung_total_all, sitzung_total_filtered = run_sitzungen(
            ics_url=args.ics_url,
        )

    duration = time.monotonic() - start

    summary = build_summary(
        vorgang_reports=vorgang_reports,
        beteiligung_reports=beteiligung_reports,
        sitzung_reports=sitzung_reports,
        raw_vorgaenge=raw_vorgaenge,
        duration_s=duration,
        lookback_days=args.lookback_days,
        wahlperiode=args.wahlperiode,
    )

    # Fill in sitzung filtered/kept from direct counts
    summary.sitzung_filtered = sitzung_total_filtered
    summary.sitzung_kept = summary.total_sitzung_events

    output = format_summary(summary, verbosity=args.verbosity, as_json=args.json)
    print(output)


if __name__ == "__main__":
    main()
