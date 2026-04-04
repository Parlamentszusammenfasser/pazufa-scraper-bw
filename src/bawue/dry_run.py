"""Dry-run diagnostic script — runs scraper components without posting to the API.

Usage:
    python -m bawue.dry_run --scraper vorgaenge --limit 5 --verbosity 2
    python -m bawue.dry_run --json --limit 2
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

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
from bawue.wahlperiode_check import check_for_newer_wahlperiode

logger = logging.getLogger(__name__)

DEFAULT_ICS_URL = "https://www.landtag-bw.de/resource/calendar/501552/download/terminkalender.ics"


# ---------------------------------------------------------------------------
# Report dataclasses
# ---------------------------------------------------------------------------


@dataclass
class FieldCompleteness:
    """Completeness stats for a single field across a collection of records."""

    field: str
    present: int
    missing: int
    rate: float


@dataclass
class VorgangReport:
    """Per-Vorgang analysis result."""

    vorgang_id: str
    titel: str
    raw_type: str
    mapped_type: str
    station_count: int
    missing_fields: list[str]
    fundstelle_count: int


@dataclass
class BeteiligungReport:
    """Per-process analysis result from the Beteiligungsportal."""

    slug: str
    title: str
    ministry: str
    pdf_count: int
    skipped: bool


@dataclass
class SitzungReport:
    """Per-date analysis result from the ICS calendar."""

    date_key: str
    event_count: int
    gremium_names: list[str]


@dataclass
class DryRunSummary:
    """Top-level aggregate of all scrapers."""

    total_vorgaenge: int = 0
    by_parlis_type: dict[str, int] = field(default_factory=dict)
    vorgang_field_completeness: list[FieldCompleteness] = field(default_factory=list)
    fundstelle_field_completeness: list[FieldCompleteness] = field(default_factory=list)

    total_beteiligung: int = 0
    beteiligung_with_pdfs: int = 0
    beteiligung_skipped: int = 0

    total_sitzung_dates: int = 0
    total_sitzung_events: int = 0
    sitzung_filtered: int = 0
    sitzung_kept: int = 0

    vorgang_reports: list[VorgangReport] = field(default_factory=list)
    beteiligung_reports: list[BeteiligungReport] = field(default_factory=list)
    sitzung_reports: list[SitzungReport] = field(default_factory=list)

    duration_s: float = 0.0
    wahlperiode: int = 17


# ---------------------------------------------------------------------------
# Fields to track
# ---------------------------------------------------------------------------

VORGANG_FIELDS = [
    "titel",
    "vorgangs_id",
    "detail_url",
    "Vorgangstyp",
    "Initiative",
    "fundstellen_parsed",
]

FUNDSTELLE_FIELDS = [
    "datum",
    "drucksache",
    "plenarprotokoll",
    "station_typ",
    "autor_text",
    "ausschuss",
    "seiten",
    "pdf_url",
]


# ---------------------------------------------------------------------------
# Analysis functions
# ---------------------------------------------------------------------------


def analyze_vorgang(raw: dict[str, Any], vorgang: Any) -> VorgangReport:
    """Analyze a single raw Vorgang and its converted model."""
    missing = [f for f in VORGANG_FIELDS if not raw.get(f)]
    fundstellen = raw.get("fundstellen_parsed", [])

    return VorgangReport(
        vorgang_id=raw.get("vorgangs_id", "unknown"),
        titel=raw.get("titel", ""),
        raw_type=raw.get("Vorgangstyp", ""),
        mapped_type=str(vorgang.typ),
        station_count=len(vorgang.stationen),
        missing_fields=missing,
        fundstelle_count=len(fundstellen),
    )


def analyze_beteiligung(process: Any, detail: Any) -> BeteiligungReport:
    """Analyze a single Beteiligungsportal process."""
    pdf_count = len(detail.pdf_links)
    return BeteiligungReport(
        slug=process.slug,
        title=detail.title,
        ministry=detail.ministry,
        pdf_count=pdf_count,
        skipped=pdf_count == 0,
    )


def analyze_sitzungen(events: list[Any], date_key: str) -> SitzungReport:
    """Analyze events for a single date."""
    gremium_names = list({e.gremium_name for e in events})
    return SitzungReport(
        date_key=date_key,
        event_count=len(events),
        gremium_names=sorted(gremium_names),
    )


def compute_field_completeness(records: list[dict[str, Any]], fields: list[str]) -> list[FieldCompleteness]:
    """Compute per-field completeness across a list of dicts."""
    results = []
    total = len(records)
    for f in fields:
        present = sum(1 for r in records if r.get(f))
        missing = total - present
        rate = present / total if total > 0 else 0.0
        results.append(FieldCompleteness(field=f, present=present, missing=missing, rate=rate))
    return results


def build_summary(
    *,
    vorgang_reports: list[VorgangReport],
    beteiligung_reports: list[BeteiligungReport],
    sitzung_reports: list[SitzungReport],
    raw_vorgaenge: list[dict[str, Any]],
    duration_s: float = 0.0,
    wahlperiode: int = 17,
) -> DryRunSummary:
    """Build the top-level aggregate summary."""
    by_type: dict[str, int] = {}
    for vr in vorgang_reports:
        by_type[vr.raw_type] = by_type.get(vr.raw_type, 0) + 1

    vorgang_fc = compute_field_completeness(raw_vorgaenge, VORGANG_FIELDS)

    all_fundstellen = []
    for raw in raw_vorgaenge:
        all_fundstellen.extend(raw.get("fundstellen_parsed", []))
    fundstelle_fc = compute_field_completeness(all_fundstellen, FUNDSTELLE_FIELDS)

    with_pdfs = sum(1 for br in beteiligung_reports if not br.skipped)
    skipped = sum(1 for br in beteiligung_reports if br.skipped)

    total_events = sum(sr.event_count for sr in sitzung_reports)

    return DryRunSummary(
        total_vorgaenge=len(vorgang_reports),
        by_parlis_type=by_type,
        vorgang_field_completeness=vorgang_fc,
        fundstelle_field_completeness=fundstelle_fc,
        total_beteiligung=len(beteiligung_reports),
        beteiligung_with_pdfs=with_pdfs,
        beteiligung_skipped=skipped,
        total_sitzung_dates=len(sitzung_reports),
        total_sitzung_events=total_events,
        vorgang_reports=vorgang_reports,
        beteiligung_reports=beteiligung_reports,
        sitzung_reports=sitzung_reports,
        duration_s=duration_s,
        wahlperiode=wahlperiode,
    )


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def _fmt_completeness(items: list[FieldCompleteness], total_label: str = "") -> str:
    """Format field completeness as aligned text."""
    if not items:
        return ""
    lines = []
    total = items[0].present + items[0].missing if items else 0
    if total_label:
        lines.append(f"Field completeness ({total_label}, n={total}):")
    else:
        lines.append("Field completeness:")
    for fc in items:
        n = fc.present + fc.missing
        pct = f"{fc.rate * 100:3.0f}%"
        flag = " !!" if fc.rate < 0.95 else ""
        lines.append(f"  {fc.field + ':':20s} {fc.present:>3}/{n:<3} ({pct}){flag}")
    return "\n".join(lines)


def _fmt_vorgang_detail(vr: VorgangReport) -> str:
    """Format a single Vorgang report for verbosity=2."""
    lines = [f"[{vr.vorgang_id}] {vr.titel}"]
    lines.append(f"  Type: {vr.raw_type} -> {vr.mapped_type}")
    lines.append(f"  Stations: {vr.station_count} | Fundstellen: {vr.fundstelle_count}")
    if vr.missing_fields:
        lines.append(f"  Missing: {', '.join(vr.missing_fields)}")
    return "\n".join(lines)


def _to_serializable(summary: DryRunSummary) -> dict:
    """Convert summary to a JSON-serializable dict."""
    return {
        "total_vorgaenge": summary.total_vorgaenge,
        "by_parlis_type": summary.by_parlis_type,
        "vorgang_field_completeness": [
            {"field": fc.field, "present": fc.present, "missing": fc.missing, "rate": fc.rate}
            for fc in summary.vorgang_field_completeness
        ],
        "fundstelle_field_completeness": [
            {"field": fc.field, "present": fc.present, "missing": fc.missing, "rate": fc.rate}
            for fc in summary.fundstelle_field_completeness
        ],
        "total_beteiligung": summary.total_beteiligung,
        "beteiligung_with_pdfs": summary.beteiligung_with_pdfs,
        "beteiligung_skipped": summary.beteiligung_skipped,
        "total_sitzung_dates": summary.total_sitzung_dates,
        "total_sitzung_events": summary.total_sitzung_events,
        "sitzung_filtered": summary.sitzung_filtered,
        "sitzung_kept": summary.sitzung_kept,
        "duration_s": summary.duration_s,
        "wahlperiode": summary.wahlperiode,
    }


def format_summary(summary: DryRunSummary, verbosity: int = 0, *, as_json: bool = False) -> str:
    """Format the dry-run summary for console output.

    Args:
        summary: The aggregate summary to format.
        verbosity: 0=summary, 1=+type breakdown, 2=+per-item detail.
        as_json: If True, return JSON instead of formatted text.
    """
    if as_json:
        return json.dumps(_to_serializable(summary), indent=2, ensure_ascii=False)

    lines = []
    lines.append("=== PaZuFa BaWue Dry-Run Report ===")
    lines.append(f"Wahlperiode: {summary.wahlperiode} | Duration: {summary.duration_s:.1f}s")
    lines.append("")

    # --- Vorgaenge ---
    lines.append("--- Vorgaenge (PARLIS) ---")
    lines.append(f"Total: {summary.total_vorgaenge}")
    if summary.by_parlis_type:
        lines.append("By PARLIS type:")
        for typ, count in sorted(summary.by_parlis_type.items(), key=lambda x: -x[1]):
            lines.append(f"  {typ + ':':30s} {count:>3}")
    lines.append("")

    if summary.vorgang_field_completeness:
        lines.append(_fmt_completeness(summary.vorgang_field_completeness, "RawVorgang"))
        lines.append("")

    if summary.fundstelle_field_completeness:
        lines.append(_fmt_completeness(summary.fundstelle_field_completeness, "RawFundstelle"))
        lines.append("")

    # --- Beteiligung ---
    lines.append("--- Beteiligung ---")
    lines.append(
        f"Total: {summary.total_beteiligung} | "
        f"With PDFs: {summary.beteiligung_with_pdfs} | "
        f"Skipped: {summary.beteiligung_skipped}"
    )
    lines.append("")

    # --- Sitzungen ---
    lines.append("--- Sitzungen (ICS) ---")
    lines.append(
        f"Dates: {summary.total_sitzung_dates} | "
        f"Events: {summary.total_sitzung_events} | "
        f"Filtered: {summary.sitzung_filtered} | "
        f"Kept: {summary.sitzung_kept}"
    )

    # Verbosity 2: per-item details
    if verbosity >= 2 and summary.vorgang_reports:
        lines.append("")
        lines.append("--- Per-Vorgang Detail ---")
        for vr in summary.vorgang_reports:
            lines.append(_fmt_vorgang_detail(vr))
            lines.append("")

    return "\n".join(lines)


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
    scraper._llm_enabled = False
    scraper._llm = None
    scraper.session = None

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
            vorgang = asyncio.run(scraper._build_vorgang(raw))
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

    from pythonjsonlogger.json import JsonFormatter

    from bawue.log_context import VorgangsnummerFilter

    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(JsonFormatter("%(asctime)s %(levelname)s %(name)s %(message)s %(vorgangs_id)s"))
    _handler.addFilter(VorgangsnummerFilter())
    logging.root.setLevel(logging.INFO)
    logging.root.addHandler(_handler)

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
