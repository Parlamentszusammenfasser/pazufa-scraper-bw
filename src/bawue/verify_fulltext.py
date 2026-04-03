"""Helper-class to verify PDF fulltext extraction and normalization against real PARLIS documents.
Not used in production

Usage:
    python -m bawue.verify_fulltext --limit 5 --verbosity 2
    python -m bawue.verify_fulltext --vorgangstyp Gesetzgebung --limit 3
    python -m bawue.verify_fulltext --lookback-days 90 --limit 10
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
from datetime import date, timedelta
from pathlib import Path

import aiohttp

from bawue.bawue_dok import (
    _is_garbled,
    _paragraph_quality_score,
    extract_pdf_text,
    normalize_volltext,
)
from bawue.bawue_vorgaenge_scraper import DEFAULT_WAHLPERIODE_START
from bawue.parlis_client import ParlisClient

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
    scores = [(p[:80], _paragraph_quality_score(p)) for p in paragraphs if p.strip()]
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
        "is_garbled": _is_garbled(raw_text),
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
    """Captures OCR-related log messages from bawue_dok."""

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


def collect_pdf_urls(
    *,
    vorgangstyp: str = "Gesetzgebung",
    wahlperiode: int = 17,
    wahlperiode_start_date: date | None = None,
    lookback_days: int | None = None,
    limit: int = 5,
) -> list[str]:
    """Search PARLIS and extract unique PDF URLs from fundstellen."""
    start_date = wahlperiode_start_date or DEFAULT_WAHLPERIODE_START
    if lookback_days is not None:
        start_date = date.today() - timedelta(days=lookback_days)

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

    urls: list[str] = []
    seen: set[str] = set()
    for rv in raw_vorgaenge:
        for fund in rv.get("fundstellen_parsed", []):
            url = fund.get("pdf_url")
            if url and url not in seen:
                seen.add(url)
                urls.append(url)
                if len(urls) >= limit:
                    return urls
    return urls


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
    # Collect PDF URLs from PARLIS
    urls = collect_pdf_urls(
        vorgangstyp=args.vorgangstyp,
        wahlperiode=args.wahlperiode,
        wahlperiode_start_date=args.wahlperiode_start_date,
        lookback_days=args.lookback_days,
        limit=args.limit,
    )

    if not urls:
        print("No PDF URLs found. Try different search parameters.", file=sys.stderr)
        return

    print(f"Found {len(urls)} PDF URLs, processing...", file=sys.stderr)

    # Set up OCR log capture
    dok_logger = logging.getLogger("bawue.bawue_dok")
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
    parser.add_argument("--wahlperiode", type=int, default=17, help="Wahlperiode (default: 17)")
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
        default=DEFAULT_WAHLPERIODE_START,
    )
    parser.add_argument("--lookback-days", type=int, default=None, help="Only check last N days")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
