"""Compare LLM provider output quality for parliamentary document enrichment.

Downloads real PDFs from the BaWue Landtag, extracts text, runs both OpenAI and
local Ollama models, and saves structured results for qualitative comparison.

Standalone script -- no Redis, no backend, no pytest required.
Requires: LLM_PROVIDER_KEY env var (for OpenAI) and/or running Ollama instance.

Usage:
    python scripts/compare_llm_providers.py
    python scripts/compare_llm_providers.py --ollama-only
    python scripts/compare_llm_providers.py --openai-only
    python scripts/compare_llm_providers.py --openai-model gpt-4.1-mini
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import aiohttp
from openapi_client.models.doktyp import Doktyp

from bawue.bawue_dok import (
    download_pdf,
    extract_pdf_text,
    extract_semantics,
    normalize_volltext,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

OUTPUT_DIR = Path(__file__).parent / "output"


# ---------------------------------------------------------------------------
# Sample documents — real PDFs from Landtag BW, Wahlperiode 17
# ---------------------------------------------------------------------------


@dataclass
class SampleDocument:
    doc_id: str
    url: str
    doktyp: Doktyp
    description: str


SAMPLE_DOCUMENTS = [
    SampleDocument(
        "17_10266",
        "https://www.landtag-bw.de/files/live/sites/LTBW/files/dokumente/WP17/Drucksachen/10000/17_10266_D.pdf",
        Doktyp.ENTWURF,
        "Gesetzentwurf Coronasoforthilfen",
    ),
    SampleDocument(
        "17_3456",
        "https://www.landtag-bw.de/files/live/sites/LTBW/files/dokumente/WP17/Drucksachen/3000/17_3456_D.pdf",
        Doktyp.STELLUNGNAHME,
        "Stellungnahme OEPNV Personalmangel",
    ),
    SampleDocument(
        "17_4900",
        "https://www.landtag-bw.de/files/live/sites/LTBW/files/dokumente/WP17/Drucksachen/4000/17_4900_D.pdf",
        Doktyp.BESCHLUSSEMPF,
        "Beschlussempfehlung Lehrkraefte-Arbeitszeit",
    ),
    SampleDocument(
        "17_1700",
        "https://www.landtag-bw.de/files/live/sites/LTBW/files/dokumente/WP17/Drucksachen/1000/17_1700_D.pdf",
        Doktyp.SONSTIG,
        "Kleine Anfrage Anrechnungsstunden",
    ),
    SampleDocument(
        "17_5500",
        "https://www.landtag-bw.de/files/live/sites/LTBW/files/dokumente/WP17/Drucksachen/5000/17_5500_D.pdf",
        Doktyp.ENTWURF,
        "Gesetzentwurf Fischereigesetz",
    ),
]


# ---------------------------------------------------------------------------
# Lightweight LLM mock — extract_semantics only reads these four attributes
# ---------------------------------------------------------------------------


@dataclass
class LLMMock:
    api_key: str | None
    api_base: str | None
    temperature: float = 0.1
    timeout_seconds: float = 120.0


@dataclass
class ModelConfig:
    name: str
    model: str
    api_key: str | None
    api_base: str | None


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


async def download_and_extract(session: aiohttp.ClientSession, doc: SampleDocument) -> tuple[str, str, float]:
    """Download PDF and extract text. Returns (text, hash, duration_seconds)."""
    start = time.monotonic()
    pdf_path = await download_pdf(session, doc.url)
    full_text, doc_hash = await extract_pdf_text(pdf_path)
    full_text = normalize_volltext(full_text)
    duration = time.monotonic() - start
    return full_text, doc_hash, duration


async def run_single_extraction(
    text: str,
    doktyp: Doktyp,
    model_cfg: ModelConfig,
) -> dict:
    """Run extract_semantics for one model, return result dict with timing."""
    llm = LLMMock(
        api_key=model_cfg.api_key,
        api_base=model_cfg.api_base,
    )
    start = time.monotonic()
    try:
        result = await extract_semantics(llm, text, doktyp, model=model_cfg.model)
        return {
            "status": "success",
            "duration_seconds": round(time.monotonic() - start, 2),
            "raw_response": result,
            "error": None,
        }
    except Exception as exc:
        return {
            "status": "error",
            "duration_seconds": round(time.monotonic() - start, 2),
            "raw_response": None,
            "error": f"{type(exc).__name__}: {exc}",
        }


async def process_document(
    session: aiohttp.ClientSession,
    doc: SampleDocument,
    models: list[ModelConfig],
) -> dict:
    """Process one document through all models."""
    logger.info("--- %s: %s ---", doc.doc_id, doc.description)

    # Download + extract text (shared across models)
    try:
        full_text, doc_hash, extract_dur = await download_and_extract(session, doc)
        logger.info("  Text extracted: %d chars, hash=%s...", len(full_text), doc_hash[:12])
    except Exception as exc:
        logger.error("  PDF download/extraction failed: %s", exc)
        return {
            "id": doc.doc_id,
            "url": doc.url,
            "doktyp": doc.doktyp.value,
            "description": doc.description,
            "text_hash": None,
            "text_length_chars": 0,
            "text_extract_seconds": 0,
            "results": {
                m.name: {"status": "skipped", "duration_seconds": 0, "raw_response": None, "error": str(exc)}
                for m in models
            },
        }

    # Run each model sequentially for fair timing (full document text, no truncation)
    results = {}
    for model_cfg in models:
        logger.info("  Running %s (%s)...", model_cfg.name, model_cfg.model)
        result = await run_single_extraction(full_text, doc.doktyp, model_cfg)
        results[model_cfg.name] = result
        if result["status"] == "success":
            logger.info("  %s: OK in %.1fs", model_cfg.name, result["duration_seconds"])
        else:
            logger.warning("  %s: FAILED in %.1fs — %s", model_cfg.name, result["duration_seconds"], result["error"])

    return {
        "id": doc.doc_id,
        "url": doc.url,
        "doktyp": doc.doktyp.value,
        "description": doc.description,
        "text_hash": doc_hash,
        "text_length_chars": len(full_text),
        "text_extract_seconds": round(extract_dur, 2),
        "results": results,
    }


def build_summary(documents: list[dict], models: list[ModelConfig]) -> dict:
    """Build summary stats from document results."""
    summary = {"total_documents": len(documents)}
    for m in models:
        successes = [d for d in documents if d["results"].get(m.name, {}).get("status") == "success"]
        durations = [d["results"][m.name]["duration_seconds"] for d in successes]
        failures = [d["id"] for d in documents if d["results"].get(m.name, {}).get("status") == "error"]
        summary[f"{m.name}_successes"] = len(successes)
        summary[f"{m.name}_avg_duration_s"] = round(sum(durations) / len(durations), 2) if durations else 0
        summary[f"{m.name}_failures"] = failures
    return summary


def print_summary(output: dict) -> None:
    """Print a readable summary table to stdout."""
    print("\n" + "=" * 72)
    print("LLM COMPARISON RESULTS")
    print("=" * 72)

    summary = output["summary"]
    models = list(output["meta"]["models"].keys())

    print(f"\nDocuments: {summary['total_documents']}")
    for m in models:
        s = summary.get(f"{m}_successes", 0)
        avg = summary.get(f"{m}_avg_duration_s", 0)
        fails = summary.get(f"{m}_failures", [])
        print(f"\n  {m}:")
        print(f"    Successes:    {s}/{summary['total_documents']}")
        print(f"    Avg duration: {avg:.1f}s")
        if fails:
            print(f"    Failures:     {', '.join(fails)}")

    print("\n" + "-" * 72)
    for doc in output["documents"]:
        print(f"\n  [{doc['id']}] {doc['description']} ({doc['doktyp']})")
        for m in models:
            r = doc["results"].get(m, {})
            status = r.get("status", "?")
            dur = r.get("duration_seconds", 0)
            if status == "success":
                resp = r["raw_response"]
                kw_count = len(resp.get("schlagworte", []))
                summary_len = len(resp.get("zusammenfassung", ""))
                kurz = resp.get("kurztitel", "")[:50]
                print(f'    {m:>10}: {dur:5.1f}s | {kw_count} keywords | {summary_len} chars summary | "{kurz}"')
            else:
                print(f"    {m:>10}: {status} ({dur:.1f}s)")

    print("\n" + "=" * 72)


async def main(models: list[ModelConfig], args: argparse.Namespace) -> None:
    """Run full comparison."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    meta = {
        "timestamp": datetime.now(UTC).isoformat(),
        "models": {
            m.name: {"model": m.model, "api_base": m.api_base, "has_api_key": m.api_key is not None} for m in models
        },
    }

    documents = []
    async with aiohttp.ClientSession() as session:
        for doc in SAMPLE_DOCUMENTS:
            result = await process_document(session, doc, models)
            documents.append(result)

    summary = build_summary(documents, models)
    output = {"meta": meta, "documents": documents, "summary": summary}

    # Write JSON report
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    out_path = args.output_dir / f"llm_comparison_{ts}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False))
    logger.info("Report written to %s", out_path)

    print_summary(output)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare LLM providers for BaWue document enrichment")
    parser.add_argument("--ollama-only", action="store_true", help="Skip OpenAI, run Ollama only")
    parser.add_argument("--openai-only", action="store_true", help="Skip Ollama, run OpenAI only")
    parser.add_argument("--openai-model", default="gpt-5-nano", help="OpenAI model (default: gpt-5-nano)")
    parser.add_argument("--ollama-model", default="ollama/gemma4:e4b", help="Ollama model (default: ollama/gemma4:e4b)")
    parser.add_argument("--ollama-base-url", default="http://localhost:11434", help="Ollama API base URL")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR, help="Output directory")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    models: list[ModelConfig] = []
    api_key = os.environ.get("LLM_PROVIDER_KEY")

    if not args.ollama_only:
        if api_key:
            models.append(ModelConfig("openai", args.openai_model, api_key, None))
        else:
            logger.warning("LLM_PROVIDER_KEY not set — skipping OpenAI. Use --ollama-only to suppress this warning.")

    if not args.openai_only:
        models.append(ModelConfig("ollama", args.ollama_model, None, args.ollama_base_url))

    if not models:
        logger.error("No models configured. Set LLM_PROVIDER_KEY or use --ollama-only.")
        sys.exit(1)

    logger.info("Models: %s", ", ".join(f"{m.name} ({m.model})" for m in models))

    asyncio.run(main(models, args))
