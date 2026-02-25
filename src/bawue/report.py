"""Dry-run report analysis and formatting — pure logic, no I/O."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Dataclasses
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
    lookback_days: int = 7
    wahlperiode: int = 17


# ---------------------------------------------------------------------------
# Fields to track
# ---------------------------------------------------------------------------

VORGANG_FIELDS = [
    "titel", "vorgangs_id", "detail_url", "Vorgangstyp", "Initiative", "fundstellen_parsed",
]

FUNDSTELLE_FIELDS = [
    "datum", "drucksache", "plenarprotokoll", "station_typ", "autor_text", "ausschuss",
    "seiten", "pdf_url",
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


def compute_field_completeness(
    records: list[dict[str, Any]], fields: list[str]
) -> list[FieldCompleteness]:
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
    lookback_days: int = 7,
    wahlperiode: int = 17,
) -> DryRunSummary:
    """Build the top-level aggregate summary."""
    # Vorgaenge type breakdown
    by_type: dict[str, int] = {}
    for vr in vorgang_reports:
        by_type[vr.raw_type] = by_type.get(vr.raw_type, 0) + 1

    # Field completeness
    vorgang_fc = compute_field_completeness(raw_vorgaenge, VORGANG_FIELDS)

    all_fundstellen = []
    for raw in raw_vorgaenge:
        all_fundstellen.extend(raw.get("fundstellen_parsed", []))
    fundstelle_fc = compute_field_completeness(all_fundstellen, FUNDSTELLE_FIELDS)

    # Beteiligung
    with_pdfs = sum(1 for br in beteiligung_reports if not br.skipped)
    skipped = sum(1 for br in beteiligung_reports if br.skipped)

    # Sitzungen
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
        lookback_days=lookback_days,
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
        "lookback_days": summary.lookback_days,
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
    lines.append(
        f"Lookback: {summary.lookback_days} days | "
        f"Wahlperiode: {summary.wahlperiode} | "
        f"Duration: {summary.duration_s:.1f}s"
    )
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
