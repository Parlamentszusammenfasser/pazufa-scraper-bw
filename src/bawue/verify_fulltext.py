"""Helper-class to verify PDF fulltext extraction and normalization against real PARLIS documents.
Not used in production

Usage:
    python -m bawue.verify_fulltext --limit 5 --verbosity 2
    python -m bawue.verify_fulltext --vorgangstyp Gesetzgebung --limit 3
    python -m bawue.verify_fulltext --lookback-days 90 --limit 10
    python -m bawue.verify_fulltext --compare-parser --wahlperiode 17 18 --limit 90 --include 17/1104 17/1201
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import aiohttp
from kreuzberg import ExtractionConfig, PageConfig, extract_file

from bawue.gesetzblatt_lookup import parse_german_date
from bawue.parlis_client import ParlisClient
from bawue.pdf_text import (
    _MIN_AGENDA_PAGE_CHARS,
    _WINDOW_PAGES,
    MAX_OCR_PAGES,
    MIN_TEXT_LENGTH,
    _page_map,
    agenda_text,
    ausgegeben_datum,
    download_pdf,
    extract_pdf_text,
    extract_relevant_pages,
    is_garbled,
    normalize_volltext,
    paragraph_quality_score,
    parse_page_hint,
)
from bawue.wahlperiode import CURRENT_WAHLPERIODE, wahlperiode_start

logger = logging.getLogger(__name__)

LOGDIR = Path("locallogs")


# ---------------------------------------------------------------------------
# Per-PDF analysis
# ---------------------------------------------------------------------------


def _count_c1(text: str) -> int:
    """Count C1 control characters (0x80-0x9F)."""
    return sum(1 for c in text if 0x80 <= ord(c) <= 0x9F)


def _count_cr(text: str) -> int:
    """Count carriage return characters."""
    return text.count("\r")


def _count_angle_brackets(text: str) -> int:
    return text.count("<") + text.count(">")


def _count_guillemets(text: str) -> int:
    return text.count("\u2039") + text.count("\u203a")


def _trailing_ws_lines(text: str) -> int:
    """Count lines with trailing whitespace."""
    return sum(1 for line in text.split("\n") if line != line.rstrip())


def _paragraph_stats(text: str) -> dict:
    """Compute paragraph quality stats from raw text."""
    paragraphs = re.split(r"\n\n+", text)
    scores = [(p[:80], paragraph_quality_score(p)) for p in paragraphs if p.strip()]
    removed = [(snippet, s) for snippet, s in scores if s < 0.5]
    return {
        "total": len(scores),
        "kept": len(scores) - len(removed),
        "removed": len(removed),
        "removed_snippets": [{"score": round(s, 3), "snippet": snippet} for snippet, s in removed],
    }


def _analyze_pdf(url: str, raw_text: str, normalized_text: str, doc_hash: str) -> dict:
    """Build a detailed analysis dict for one PDF."""
    para_stats = _paragraph_stats(raw_text)

    return {
        "url": url,
        "hash": doc_hash,
        "raw_len": len(raw_text),
        "normalized_len": len(normalized_text),
        "delta_len": len(raw_text) - len(normalized_text),
        "is_garbled": is_garbled(raw_text),
        "raw_c1_chars": _count_c1(raw_text),
        "norm_c1_chars": _count_c1(normalized_text),
        "raw_cr_count": _count_cr(raw_text),
        "norm_cr_count": _count_cr(normalized_text),
        "raw_angle_brackets": _count_angle_brackets(raw_text),
        "norm_angle_brackets": _count_angle_brackets(normalized_text),
        "norm_guillemets": _count_guillemets(normalized_text),
        "raw_trailing_ws_lines": _trailing_ws_lines(raw_text),
        "norm_trailing_ws_lines": _trailing_ws_lines(normalized_text),
        "paragraphs": para_stats,
        "raw_snippet": raw_text[:500],
        "normalized_snippet": normalized_text[:500],
    }


# ---------------------------------------------------------------------------
# PDF download + extraction
# ---------------------------------------------------------------------------


class OcrLogCapture(logging.Handler):
    """Captures OCR-related log messages from pdf_text."""

    def __init__(self):
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record):
        msg = record.getMessage()
        if "OCR" in msg or "garbled" in msg.lower():
            self.messages.append(msg)


async def process_one_pdf(session: aiohttp.ClientSession, url: str, ocr_handler: OcrLogCapture) -> dict:
    """Download one PDF, extract text, normalize, and return analysis."""
    ocr_handler.messages.clear()

    pdf_path: Path | None = None
    try:
        # Download
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=60)) as resp:
            if resp.status != 200:
                return {"url": url, "error": f"HTTP {resp.status}"}
            content = await resp.read()

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(content)
            pdf_path = Path(tmp.name)

        # Extract
        raw_text, doc_hash = await extract_pdf_text(pdf_path)

        # Normalize
        normalized_text = normalize_volltext(raw_text)

        analysis = _analyze_pdf(url, raw_text, normalized_text, doc_hash)
        analysis["ocr_messages"] = list(ocr_handler.messages)
        analysis["pdf_size_bytes"] = len(content)
        return analysis

    except Exception as exc:
        return {"url": url, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        if pdf_path is not None:
            pdf_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# PARLIS search → PDF URL collection
# ---------------------------------------------------------------------------


def _fundstellen(
    vorgangstyp: str, wahlperiode: int, wahlperiode_start_date: date | None, lookback_days: int | None
) -> list[dict]:
    """The Fundstellen of all Vorgänge PARLIS finds, in search order."""
    if lookback_days is not None:
        wahlperiode_start_date = date.today() - timedelta(days=lookback_days)
    start_date = wahlperiode_start(wahlperiode, wahlperiode_start_date)

    client = ParlisClient(
        wahlperiode=wahlperiode,
        request_delay_s=1.0,
        wahlperiode_start_date=start_date,
    )

    logger.info(
        "Searching PARLIS for '%s' (%s to %s)...",
        vorgangstyp,
        start_date,
        date.today(),
    )
    raw_vorgaenge = client.search(vorgangstyp, start_date, date.today())
    logger.info("Found %d Vorgänge", len(raw_vorgaenge))
    return [fund for rv in raw_vorgaenge for fund in rv.get("fundstellen_parsed", [])]


def collect_pdf_urls(
    *,
    vorgangstyp: str = "Gesetzgebung",
    wahlperiode: int = CURRENT_WAHLPERIODE,
    wahlperiode_start_date: date | None = None,
    lookback_days: int | None = None,
    limit: int = 5,
) -> list[str]:
    """Search PARLIS and extract unique PDF URLs from fundstellen."""
    urls: list[str] = []
    for fund in _fundstellen(vorgangstyp, wahlperiode, wahlperiode_start_date, lookback_days):
        url = fund.get("pdf_url")
        if url and url not in urls:
            urls.append(url)
            if len(urls) >= limit:
                break
    return urls


# ---------------------------------------------------------------------------
# --compare-parser: kreuzberg vs. pazufa-parser on the same documents (issue #216)
# ---------------------------------------------------------------------------

# Issue #216: the parser loses text when it keeps less than 90 % of today's normalized volltext.
_MIN_LENGTH_RATIO = 0.9


def sample_fundstellen(funds: list[dict], limit: int, include: set[str]) -> list[dict]:
    """*limit* Fundstellen spread evenly over *funds*, plus every one of a Drucksache in *include*."""
    unique = list({f["pdf_url"]: f for f in funds if f.get("pdf_url")}.values())
    picked = unique[:: max(1, len(unique) // limit)][:limit] if limit else []
    return picked + [f for f in unique if f.get("drucksache") in include and f not in picked]


async def _kreuzberg_side(pdf_path: Path, fund: dict, parlis_datum: datetime | None) -> tuple[dict, dict[int, str]]:
    """Today's enrichment path (bawue_dok.enrich_dokument), plus the native text per page."""
    hint = parse_page_hint(fund["pdf_url"])
    start = time.monotonic()
    marked, _ = await extract_pdf_text(pdf_path, page_markers=hint is not None)
    seconds = time.monotonic() - start

    # The native text without the OCR retry, once as extract_pdf_text read it (did OCR replace it?)
    # and once per page (did the parser OCR a page whose native text was usable?).
    paged = await extract_file(pdf_path, config=ExtractionConfig(pages=PageConfig(insert_page_markers=True)))
    native = paged if hint is not None else await extract_file(pdf_path, config=ExtractionConfig())
    page_count = paged.metadata.get("page_count", 0)

    full = normalize_volltext(marked if hint is None else extract_relevant_pages(marked, hint))
    agenda = agenda_text(marked, hint, fund.get("druckseiten"))
    side = {
        "seconds": round(seconds, 2),
        "ocr_pages": page_count if marked != (native.content or "") else 0,
        "length": len(full),
        "ausgegeben": _iso(parlis_datum and ausgegeben_datum(full, fund.get("drucksache"), parlis_datum)),
        "agenda": agenda and {"pages": agenda[1], "length": len(agenda[0])},
    }
    return side, _page_map(paged.content or "")


def _parser_agenda(doc, hint: int | None, druckseiten: list[int] | None) -> tuple[str, list[int]] | None:
    """agenda_text with the parser's printed page numbers instead of a fixed offset to the anchor."""
    if not hint or not druckseiten:
        return None
    by_printed = {p.printed_page: p.number for p in doc.pages if hint <= p.number < hint + _WINDOW_PAGES}
    wanted = [str(d) for d in druckseiten if d - druckseiten[0] < _WINDOW_PAGES]
    if not all(w in by_printed for w in wanted):
        return None
    pages = sorted(by_printed[w] for w in wanted)
    agenda = normalize_volltext(doc.text(pages=pages))
    return (agenda, pages) if len(agenda) >= _MIN_AGENDA_PAGE_CHARS * len(pages) else None


def _parser_side(pdf_path: Path, fund: dict, parlis_datum: datetime | None, native_pages: dict[int, str]) -> dict:
    """What the parser yields for the same Fundstelle, cut and normalized the same way."""
    from pazufa_pdf_parser import Ocr, PageSource, ParseOptions, Severity, parse

    hint = parse_page_hint(fund["pdf_url"])
    start = time.monotonic()
    # Two tesseract processes: today's OCR budget (_OCR_LIMIT x _OCR_MAX_THREADS in pdf_text).
    doc = parse(pdf_path, ParseOptions(ocr_engine=Ocr(max_parallel=2), max_ocr_pages=MAX_OCR_PAGES))
    seconds = time.monotonic() - start

    window = [p for p in doc.pages if hint <= p.number < hint + _WINDOW_PAGES] if hint else list(doc.pages)
    body = doc.text(pages=[p.number for p in window])
    if len(body.strip()) < MIN_TEXT_LENGTH:  # extract_relevant_pages' fallback (issue #50)
        window, body = list(doc.pages), doc.text()
    full = normalize_volltext(body)
    # The parser moves the page-1 footer ("Ausgegeben: …") and the header into furniture.
    with_furniture = normalize_volltext("\n\n".join(p.furniture_text() for p in window) + "\n\n" + body)

    # DD-015 keeps OCR only when more usable text survives than from the native text.
    replaced = []
    for page in doc.pages:
        native = native_pages.get(page.number, "")
        if page.source is not PageSource.OCR or is_garbled(native):
            continue
        kept = len(normalize_volltext(native))
        ocr = len(normalize_volltext(page.furniture_text() + "\n\n" + page.text()))
        if kept >= MIN_TEXT_LENGTH and kept > ocr:
            replaced.append(page.number)

    def datum(text: str) -> str | None:
        return _iso(parlis_datum and ausgegeben_datum(text, fund.get("drucksache"), parlis_datum))

    agenda = _parser_agenda(doc, hint, fund.get("druckseiten"))
    return {
        "seconds": round(seconds, 2),
        "ocr_pages": sum(p.source is PageSource.OCR for p in doc.pages),
        "length": len(full),
        "ausgegeben": datum(with_furniture),
        "ausgegeben_body_only": datum(full),
        "agenda": agenda and {"pages": agenda[1], "length": len(agenda[0])},
        "native_replaced": replaced,
        "issues": sorted({i.code for i in doc.report.issues if i.severity >= Severity.WARNING}),
    }


def _iso(value: datetime | None) -> str | None:
    return value.date().isoformat() if value else None


def compare_findings(row: dict) -> list[str]:
    """Where the parser falls short of today's extraction, by issue #216's acceptance criteria."""
    k, p = row["kreuzberg"], row["parser"]
    findings = []
    if k["ausgegeben"] and p["ausgegeben"] != k["ausgegeben"]:
        findings.append(f"Ausgegeben lost: {k['ausgegeben']} → {p['ausgegeben']}")
    if p["length"] < _MIN_LENGTH_RATIO * k["length"]:
        findings.append(f"text {p['length']:,} of {k['length']:,} chars ({p['length'] / k['length']:.0%})")
    if p["native_replaced"]:
        findings.append(f"OCR replaced usable native text on pages {p['native_replaced']}")
    k_pages, p_pages = (k["agenda"] or {}).get("pages"), (p["agenda"] or {}).get("pages")
    if k_pages != p_pages:
        findings.append(f"agenda pages {k_pages} → {p_pages}")
    return findings


async def compare_one(session: aiohttp.ClientSession, fund: dict) -> dict:
    """Run both extractors on one Fundstelle's PDF."""
    row = {key: fund.get(key) for key in ("pdf_url", "drucksache", "plenarprotokoll", "datum")}
    pdf_path: Path | None = None
    try:
        pdf_path = await download_pdf(session, fund["pdf_url"])
        parlis_datum = parse_german_date(fund.get("datum"))
        row["kreuzberg"], native_pages = await _kreuzberg_side(pdf_path, fund, parlis_datum)
        row["parser"] = await asyncio.to_thread(_parser_side, pdf_path, fund, parlis_datum, native_pages)
        row["findings"] = compare_findings(row)
    except Exception as exc:
        row["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if pdf_path is not None:
            pdf_path.unlink(missing_ok=True)
    return row


def compare_report(rows: list[dict]) -> str:
    """Markdown report: totals, then every document with a finding or an error."""
    ok = [r for r in rows if "error" not in r]

    def total(side: str, key: str) -> float:
        return sum(r[side][key] for r in ok)

    def found(side: str, key: str) -> int:
        return sum(bool(r[side][key]) for r in ok)

    lines = [
        "# kreuzberg vs. pazufa-parser (issue #216)",
        "",
        f"{len(rows)} Fundstellen, {len(rows) - len(ok)} errors, {sum(bool(r['findings']) for r in ok)} with findings.",
        "",
        "| | kreuzberg | pazufa-parser |",
        "|---|---|---|",
        f"| seconds | {total('kreuzberg', 'seconds'):,.0f} | {total('parser', 'seconds'):,.0f} |",
        f"| OCR pages | {total('kreuzberg', 'ocr_pages'):,.0f} | {total('parser', 'ocr_pages'):,.0f} |",
        f"| normalized chars | {total('kreuzberg', 'length'):,.0f} | {total('parser', 'length'):,.0f} |",
        f"| Ausgegeben found | {found('kreuzberg', 'ausgegeben')} | {found('parser', 'ausgegeben')} "
        f"(body only: {found('parser', 'ausgegeben_body_only')}) |",
        f"| agenda cut | {found('kreuzberg', 'agenda')} | {found('parser', 'agenda')} |",
        "",
        "## Findings",
        "",
    ]
    for r in rows:
        problems = [r["error"]] if "error" in r else r["findings"]
        if problems:
            name = r["drucksache"] or r["plenarprotokoll"] or "?"
            lines.append(f"- [{name}]({r['pdf_url']}): " + "; ".join(problems))
    return "\n".join(lines) + "\n"


async def _compare(args: argparse.Namespace) -> None:
    include = set(args.include)
    funds: list[dict] = []
    for wp in args.wahlperiode:
        found = _fundstellen(args.vorgangstyp, wp, args.wahlperiode_start_date, args.lookback_days)
        funds += sample_fundstellen(found, args.limit, include)
    # A Drucksache outside the search still gets measured, without a PARLIS date to check Ausgegeben against.
    from bawue.bawue_vorgaenge_scraper import construct_drucksache_pdf_url

    funds += [
        {"drucksache": d, "pdf_url": construct_drucksache_pdf_url(d)}
        for d in sorted(include - {f.get("drucksache") for f in funds})
    ]

    LOGDIR.mkdir(exist_ok=True)
    stem = LOGDIR / f"compare_parser_{time.strftime('%Y%m%d_%H%M%S')}"
    rows: list[dict] = []
    async with aiohttp.ClientSession() as session:
        for i, fund in enumerate(funds, 1):
            row = await compare_one(session, fund)
            rows.append(row)
            with open(stem.with_suffix(".jsonl"), "a") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(f"{i}/{len(funds)} {fund['pdf_url']}: {row.get('error') or row['findings'] or 'ok'}", file=sys.stderr)
    stem.with_suffix(".md").write_text(compare_report(rows))
    print(f"\nReport written to: {stem.with_suffix('.md')}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------


def _print_analysis(analysis: dict, verbosity: int, index: int) -> None:
    """Print a single PDF analysis to stderr."""
    if "error" in analysis:
        print(f"\n--- PDF #{index}: ERROR ---", file=sys.stderr)
        print(f"  URL: {analysis['url']}", file=sys.stderr)
        print(f"  Error: {analysis['error']}", file=sys.stderr)
        return

    print(f"\n--- PDF #{index} ---", file=sys.stderr)
    print(f"  URL:       {analysis['url']}", file=sys.stderr)
    print(f"  Hash:      {analysis['hash'][:16]}...", file=sys.stderr)
    print(f"  PDF size:  {analysis.get('pdf_size_bytes', 0):,} bytes", file=sys.stderr)
    print(f"  Raw len:   {analysis['raw_len']:,} chars", file=sys.stderr)
    print(f"  Norm len:  {analysis['normalized_len']:,} chars  (delta: -{analysis['delta_len']:,})", file=sys.stderr)
    print(f"  Garbled:   {analysis['is_garbled']}", file=sys.stderr)

    # Normalization step indicators
    steps = []
    if analysis["raw_c1_chars"] > 0:
        steps.append(f"C1 removed: {analysis['raw_c1_chars']}")
    if analysis["raw_cr_count"] > 0:
        steps.append(f"CR removed: {analysis['raw_cr_count']}")
    if analysis["paragraphs"]["removed"] > 0:
        steps.append(f"Paragraphs filtered: {analysis['paragraphs']['removed']}/{analysis['paragraphs']['total']}")
    if analysis["raw_trailing_ws_lines"] > 0:
        steps.append(f"Trailing WS lines cleaned: {analysis['raw_trailing_ws_lines']}")
    if analysis["raw_angle_brackets"] > 0:
        steps.append(f"Angle brackets neutralized: {analysis['raw_angle_brackets']}")

    if steps:
        print("  Normalization:", file=sys.stderr)
        for s in steps:
            print(f"    - {s}", file=sys.stderr)
    else:
        print("  Normalization: no changes needed", file=sys.stderr)

    if analysis["ocr_messages"]:
        print("  OCR events:", file=sys.stderr)
        for msg in analysis["ocr_messages"]:
            print(f"    - {msg}", file=sys.stderr)

    if verbosity >= 2:
        print("\n  === Raw text (first 500 chars) ===", file=sys.stderr)
        print(f"  {analysis['raw_snippet']!r}", file=sys.stderr)
        print("\n  === Normalized text (first 500 chars) ===", file=sys.stderr)
        print(f"  {analysis['normalized_snippet']!r}", file=sys.stderr)

        if analysis["paragraphs"]["removed_snippets"]:
            print("\n  === Removed paragraphs ===", file=sys.stderr)
            for rp in analysis["paragraphs"]["removed_snippets"]:
                print(f"    score={rp['score']}: {rp['snippet']!r}", file=sys.stderr)


def _print_summary(results: list[dict], duration: float) -> None:
    """Print aggregate summary."""
    total = len(results)
    errors = sum(1 for r in results if "error" in r)
    ok = [r for r in results if "error" not in r]

    print("\n" + "=" * 60, file=sys.stderr)
    print("FULLTEXT VERIFICATION SUMMARY", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print(f"  PDFs processed:     {total}", file=sys.stderr)
    print(f"  Successful:         {len(ok)}", file=sys.stderr)
    print(f"  Errors:             {errors}", file=sys.stderr)
    print(f"  Duration:           {duration:.1f}s", file=sys.stderr)

    if not ok:
        return

    garbled = sum(1 for r in ok if r["is_garbled"])
    ocr_triggered = sum(1 for r in ok if r.get("ocr_messages"))
    total_removed = sum(r["paragraphs"]["removed"] for r in ok)
    total_paras = sum(r["paragraphs"]["total"] for r in ok)
    avg_delta = sum(r["delta_len"] for r in ok) / len(ok)
    total_c1 = sum(r["raw_c1_chars"] for r in ok)
    total_cr = sum(r["raw_cr_count"] for r in ok)
    total_angles = sum(r["raw_angle_brackets"] for r in ok)

    print(f"\n  Garbled detected:   {garbled}/{len(ok)}", file=sys.stderr)
    print(f"  OCR fallback:       {ocr_triggered}/{len(ok)}", file=sys.stderr)
    print(f"  Avg chars removed:  {avg_delta:,.0f}", file=sys.stderr)
    print(f"  C1 chars removed:   {total_c1:,}", file=sys.stderr)
    print(f"  CR chars removed:   {total_cr:,}", file=sys.stderr)
    print(f"  Angle brackets:     {total_angles:,}", file=sys.stderr)
    print(f"  Paragraphs filtered: {total_removed}/{total_paras}", file=sys.stderr)

    # Verify normalization invariants
    print("\n  --- Invariant checks ---", file=sys.stderr)
    all_norm_c1_zero = all(r["norm_c1_chars"] == 0 for r in ok)
    all_norm_cr_zero = all(r["norm_cr_count"] == 0 for r in ok)
    all_norm_no_angles = all(r["norm_angle_brackets"] == 0 for r in ok)
    all_norm_no_trailing_ws = all(r["norm_trailing_ws_lines"] == 0 for r in ok)

    checks = [
        ("No C1 chars after normalization", all_norm_c1_zero),
        ("No CR chars after normalization", all_norm_cr_zero),
        ("No angle brackets after normalization", all_norm_no_angles),
        ("No trailing whitespace after normalization", all_norm_no_trailing_ws),
    ]
    for label, passed in checks:
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {label}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def _run(args: argparse.Namespace) -> None:
    if args.compare_parser:
        await _compare(args)
        return

    # Collect PDF URLs from PARLIS
    urls = [
        url
        for wp in args.wahlperiode
        for url in collect_pdf_urls(
            vorgangstyp=args.vorgangstyp,
            wahlperiode=wp,
            wahlperiode_start_date=args.wahlperiode_start_date,
            lookback_days=args.lookback_days,
            limit=args.limit,
        )
    ]

    if not urls:
        print("No PDF URLs found. Try different search parameters.", file=sys.stderr)
        return

    print(f"Found {len(urls)} PDF URLs, processing...", file=sys.stderr)

    # Set up OCR log capture
    dok_logger = logging.getLogger("bawue.pdf_text")
    ocr_handler = OcrLogCapture()
    ocr_handler.setLevel(logging.WARNING)
    dok_logger.addHandler(ocr_handler)

    # Prepare log output
    LOGDIR.mkdir(exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    logfile = LOGDIR / f"verify_fulltext_{ts}.jsonl"

    results: list[dict] = []
    start = time.monotonic()

    try:
        async with aiohttp.ClientSession() as session:
            for i, url in enumerate(urls, 1):
                logger.info("Processing PDF %d/%d: %s", i, len(urls), url)
                analysis = await process_one_pdf(session, url, ocr_handler)
                results.append(analysis)

                # Write JSONL line immediately (survives interruption)
                with open(logfile, "a") as f:
                    f.write(json.dumps(analysis, ensure_ascii=False) + "\n")

                _print_analysis(analysis, args.verbosity, i)

                # Brief pause between downloads
                if i < len(urls):
                    await asyncio.sleep(1.0)

    except KeyboardInterrupt:
        print(f"\n\nInterrupted after {len(results)} PDFs.", file=sys.stderr)

    duration = time.monotonic() - start
    _print_summary(results, duration)
    print(f"\n  Log written to: {logfile}", file=sys.stderr)

    # Clean up handler
    dok_logger.removeHandler(ocr_handler)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify PDF fulltext extraction and normalization")
    parser.add_argument("--limit", type=int, default=5, help="Max PDFs to process (default: 5)")
    parser.add_argument(
        "--vorgangstyp",
        type=str,
        default="Gesetzgebung",
        help="PARLIS Vorgangstyp (default: Gesetzgebung)",
    )
    parser.add_argument(
        "--wahlperiode",
        type=int,
        nargs="+",
        default=[CURRENT_WAHLPERIODE],
        help=f"Wahlperioden, --limit applies to each (default: {CURRENT_WAHLPERIODE})",
    )
    parser.add_argument(
        "--verbosity",
        type=int,
        choices=[0, 1, 2],
        default=1,
        help="0=summary, 1=per-PDF, 2=text snippets",
    )
    parser.add_argument(
        "--wahlperiode-start-date",
        type=date.fromisoformat,
        default=None,
        help="Start of the search (default: the Wahlperiode's constitution date)",
    )
    parser.add_argument("--lookback-days", type=int, default=None, help="Only check last N days")
    parser.add_argument(
        "--compare-parser",
        action="store_true",
        help="Run kreuzberg and pazufa-parser on each PDF and write a report (issue #216)",
    )
    parser.add_argument(
        "--include", nargs="*", default=[], help="Drucksachen to compare in any case, e.g. 17/1104 (--compare-parser)"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    from pythonjsonlogger.json import JsonFormatter

    from bawue.log_context import VorgangsnummerFilter

    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(JsonFormatter("%(asctime)s %(levelname)s %(name)s %(message)s %(vorgangs_id)s"))
    _handler.addFilter(VorgangsnummerFilter())
    logging.root.setLevel(logging.INFO)
    logging.root.addHandler(_handler)
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
