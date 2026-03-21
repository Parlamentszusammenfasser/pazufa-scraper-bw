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
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

import requests
import toml
from icalendar import Calendar

from bawue.bawue_vorgaenge_scraper import (
    DEFAULT_ENABLED_VORGANGSTYPEN,
    DEFAULT_WAHLPERIODE_START,
    BawueVorgaengeScraper,
)
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
from bawue.wahlperiode_check import check_for_newer_wahlperiode

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
    parser.add_argument("--config-file", type=str, default="config.toml", help="Path to config TOML file")
    parser.add_argument("--vorgangstyp", type=str, default=None, help="Limit to one PARLIS Vorgangstyp")
    parser.add_argument("--wahlperiode", type=int, default=17, help="Wahlperiode (default: 17)")
    parser.add_argument("--limit", type=int, default=None, help="Max items per scraper")
    parser.add_argument("--verbosity", type=int, choices=[0, 1, 2], default=0, help="Output detail level")
    parser.add_argument("--json", action="store_true", help="Output JSON instead of formatted text")
    parser.add_argument("--ics-url", type=str, default=DEFAULT_ICS_URL, help="ICS calendar URL")
    parser.add_argument(
        "--wahlperiode-start-date",
        type=date.fromisoformat,
        default=DEFAULT_WAHLPERIODE_START,
        help="Start date of the Wahlperiode (default: 2021-04-26)",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=None,
        help="Only scrape the last N days (default: entire Wahlperiode)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=3,
        choices=range(1, 9),
        metavar="{1..8}",
        help="Number of parallel workers for Vorgangstyp scraping (default: 3)",
    )
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


def _load_enabled_vorgangstypen(config_file: str) -> list[str]:
    """Load enabled-vorgangstypen from [bawue] config section."""
    try:
        loaded = toml.load(config_file)
        return loaded.get("bawue", {}).get("enabled-vorgangstypen", DEFAULT_ENABLED_VORGANGSTYPEN)
    except Exception:
        logger.warning("Could not load config from %s, using defaults", config_file)
        return list(DEFAULT_ENABLED_VORGANGSTYPEN)


# ---------------------------------------------------------------------------
# Orchestration: Vorgaenge
# ---------------------------------------------------------------------------


def _search_one_type(
    vt: str,
    wahlperiode: int,
    date_from,
    date_to,
    wahlperiode_start_date,
) -> tuple[str, list[dict]]:
    """Search PARLIS for a single Vorgangstyp using its own client instance.

    Each call creates a fresh ParlisClient (own requests.Session) so workers
    can run concurrently without sharing session state.
    """
    client = ParlisClient(
        wahlperiode=wahlperiode,
        request_delay_s=1.0,
        wahlperiode_start_date=wahlperiode_start_date,
    )
    logger.info("Searching PARLIS for '%s' (%s to %s)...", vt, date_from, date_to)
    raw_vorgaenge = client.search(vt, date_from, date_to)
    logger.info("  '%s' -> %d results", vt, len(raw_vorgaenge))
    return vt, raw_vorgaenge


def run_vorgaenge(
    *,
    wahlperiode: int = 17,
    vorgangstypen: list[str] | None = None,
    limit: int | None = None,
    wahlperiode_start_date=None,
    max_workers: int = 3,
) -> tuple[list[VorgangReport], list[dict]]:
    """Search PARLIS for each Vorgangstyp and analyze results.

    Vorgangstypen are searched in parallel using a ThreadPoolExecutor.
    Each worker gets its own ParlisClient instance (separate requests.Session).

    Returns (vorgang_reports, raw_vorgaenge_list).
    """
    if vorgangstypen is None:
        vorgangstypen = list(VORGANGSTYP_MAP.keys())

    date_from = wahlperiode_start_date
    date_to = date.today() if wahlperiode_start_date is not None else None

    # Use object.__new__ to access _build_vorgang without full scraper init
    scraper = object.__new__(BawueVorgaengeScraper)
    scraper._wahlperiode = wahlperiode

    # Collect all raw results per type in parallel, then apply limit in deterministic order
    all_raw_by_type: dict[str, list[dict]] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_search_one_type, vt, wahlperiode, date_from, date_to, wahlperiode_start_date): vt
            for vt in vorgangstypen
        }
        for future in as_completed(futures):
            vt, raw_vorgaenge = future.result()
            all_raw_by_type[vt] = raw_vorgaenge

    all_reports: list[VorgangReport] = []
    all_raw: list[dict] = []

    for vt in vorgangstypen:  # deterministic order
        for raw in all_raw_by_type.get(vt, []):
            if limit is not None and len(all_reports) >= limit:
                return all_reports, all_raw
            vorgang = scraper._build_vorgang(raw)
            all_reports.append(analyze_vorgang(raw, vorgang))
            all_raw.append(raw)

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
        format="%(asctime)s %(levelname)-5s [%(threadName)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )

    check_for_newer_wahlperiode(args.wahlperiode)

    if args.lookback_days is not None:
        args.wahlperiode_start_date = date.today() - timedelta(days=args.lookback_days)

    start = time.monotonic()

    vorgang_reports: list[VorgangReport] = []
    raw_vorgaenge: list[dict] = []
    beteiligung_reports: list = []
    sitzung_reports: list = []
    sitzung_total_filtered = 0

    # Determine which scrapers to run
    run_v = args.scraper in ("all", "vorgaenge")
    run_b = args.scraper in ("all", "beteiligung")
    run_s = args.scraper in ("all", "sitzungen")

    if run_v:
        vorgangstypen = [args.vorgangstyp] if args.vorgangstyp else _load_enabled_vorgangstypen(args.config_file)
        vorgang_reports, raw_vorgaenge = run_vorgaenge(
            wahlperiode=args.wahlperiode,
            vorgangstypen=vorgangstypen,
            limit=args.limit,
            wahlperiode_start_date=args.wahlperiode_start_date,
            max_workers=args.workers,
        )

    if run_b:
        beteiligung_reports = run_beteiligung(
            wahlperiode=args.wahlperiode,
            limit=args.limit,
        )

    if run_s:
        sitzung_reports, _sitzung_total_all, sitzung_total_filtered = run_sitzungen(
            ics_url=args.ics_url,
        )

    duration = time.monotonic() - start

    summary = build_summary(
        vorgang_reports=vorgang_reports,
        beteiligung_reports=beteiligung_reports,
        sitzung_reports=sitzung_reports,
        raw_vorgaenge=raw_vorgaenge,
        duration_s=duration,
        wahlperiode=args.wahlperiode,
    )

    # Fill in sitzung filtered/kept from direct counts
    summary.sitzung_filtered = sitzung_total_filtered
    summary.sitzung_kept = summary.total_sitzung_events

    output = format_summary(summary, verbosity=args.verbosity, as_json=args.json)
    print(output)


if __name__ == "__main__":
    main()
