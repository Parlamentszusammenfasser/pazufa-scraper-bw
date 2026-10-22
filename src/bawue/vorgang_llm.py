"""Per-Vorgang LLM calls: Kurztitel (DD-053) and Ressort (DD-055), each with its own Redis cache."""

import hashlib
import json
import logging
import re

from pazufa_corelib.llm import LLMConnector

from bawue.bawue_dok import _SYSTEM_PROMPT, LLMMetrics, _llm_json, _redis_get, _redis_set, _sanitize_llm_text
from bawue.cache import BawueCache
from bawue.types import TODO_MARKER, Ressort

logger = logging.getLogger(__name__)


def _prompt_cache_key(user_message: str, model: str) -> str:
    """Cache key of a per-Vorgang LLM answer (Kurztitel, Ressort).

    The model is part of it, so switching models asks the new one (issue #57, DD-063).
    """
    return hashlib.sha256(f"{model}\n{_SYSTEM_PROMPT}\n{user_message}".encode()).hexdigest()


# ---------------------------------------------------------------------------
# Vorgang Kurztitel (GitHub issue #32, DD-053)
# ---------------------------------------------------------------------------

KURZTITEL_MAX_LEN = 60
_KURZTITEL_CACHE_PREFIX = "vorgang-kurztitel:"

KURZTITEL_PROMPT = f"""\
Formuliere einen Kurztitel für den folgenden parlamentarischen Vorgang. Er ist die
Überschrift auf einer öffentlichen Website für Bürgerinnen und Bürger ohne juristische
Vorkenntnisse.
Regeln:
- höchstens {KURZTITEL_MAX_LEN} Zeichen
- einfache, verständliche Sprache; benenne, worum es inhaltlich geht
- keine juristischen Floskeln wie „Gesetz zur Änderung des …“, keine Artikel- oder Paragrafenverweise
- als Substantivgruppe, ohne Verfahrensstand oder Ergebnis wie „umgesetzt“, „beschlossen“, „abgelehnt“ oder „in Kraft“
- eine Zeile, kein Punkt am Ende, keine Anführungszeichen
Ist der offizielle Titel bereits kurz und verständlich, übernimm ihn unverändert.
Antworte ausschließlich mit validem JSON: {{"kurztitel": "..."}}"""

# A lowercase, hyphen-joined token string is a URL slug, not a title (GitHub issue #32).
_SLUG_RE = re.compile(r"[a-z0-9äöüß]+(?:-[a-z0-9äöüß]+)+")
# Legal boilerplate the prompt forbids; checked so an echoed title gets the re-prompt.
_BOILERPLATE_RE = re.compile(r"^(?:Entwurf eines )?Gesetz(?:es)? zur Änderung\b|§|\bArt(?:ikel|\.)\s*\d")
# Status/outcome words: a kurztitel is cached for the Vorgang's lifetime (issue #72).
# Bare participles only: inflected ones describe the subject ("abgelehnte Asylbewerber").
_STATUS_RE = re.compile(
    r"\b(?:umgesetzt|beschlossen|verabschiedet|abgelehnt|angenommen|eingebracht|zurückgezogen|gescheitert"
    r"|ratifiziert|gebilligt|genehmigt|eingeführt|abgeschafft|verlängert|geändert)\b"
    r"|\b(?:in|außer) Kraft\b(?!-)",
    re.IGNORECASE,
)
_KURZTITEL_QUOTES = " \t\"'„“”«»"
# A trailing period after a word of 2+ letters; keeps abbreviations like "e.V." or "u. a.".
_TRAILING_PERIOD_RE = re.compile(r"(?<=[^\W\d_]{2})\.$")


def _clean_kurztitel(raw: object) -> str:
    """Sanitize (DD-027) and strip wrapping quotes / a trailing period."""
    text = _sanitize_llm_text(raw)
    text = (text or "").strip(_KURZTITEL_QUOTES)
    return _TRAILING_PERIOD_RE.sub("", text).strip(_KURZTITEL_QUOTES)


def _kurztitel_problem(kurztitel: str) -> str | None:
    """Why *kurztitel* breaks the rules, phrased for the re-prompt; None when valid."""
    if not kurztitel:
        return "Der Kurztitel ist leer."
    if "\n" in kurztitel:
        return "Der Kurztitel muss eine einzige Zeile sein."
    if len(kurztitel) > KURZTITEL_MAX_LEN:
        return f"Der Kurztitel hat {len(kurztitel)} Zeichen, erlaubt sind höchstens {KURZTITEL_MAX_LEN}."
    if _SLUG_RE.fullmatch(kurztitel):
        return "Der Kurztitel ist ein URL-Kürzel, kein lesbarer Titel."
    if _BOILERPLATE_RE.search(kurztitel):
        return "Der Kurztitel enthält juristische Floskeln oder Artikel-/Paragrafenverweise."
    if match := _STATUS_RE.search(kurztitel):
        return f"Der Kurztitel nennt einen Verfahrensstand („{match[0]}“), benenne nur den Gegenstand."
    return None


async def vorgang_kurztitel(
    llm: LLMConnector | None,
    titel: str,
    zusammenfassung: str | None,
    model: str = "gpt-5-nano",
    cache: BawueCache | None = None,
    metrics: LLMMetrics | None = None,
) -> str:
    """Short, human-readable Vorgang title of at most KURZTITEL_MAX_LEN characters.

    Generated from the official *titel* plus the initiating document's
    *zusammenfassung*. A rule violation is re-prompted once. Falls back to *titel*
    when the LLM is off, fails, or stays invalid, so the field is never empty and
    never a slug. Only generated titles are cached, so a transient failure does
    not pin the fallback.
    """
    if llm is None or not titel.strip():
        return titel

    user_message = f"{KURZTITEL_PROMPT}\n\nOffizieller Titel: {titel}\n\nZusammenfassung: {zusammenfassung or 'keine'}"
    cache_key = _prompt_cache_key(user_message, model)
    # A Redis error propagates: a fallback cached with the Vorgang would stick (issue #105).
    cached = _redis_get(cache, cache_key, prefix=_KURZTITEL_CACHE_PREFIX, typehint="Vorgang Kurztitel")
    if cached is not None:
        if metrics is not None:
            metrics.kurztitel_generated += 1
        return cached

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]
    try:
        for attempt in range(2):
            kurztitel = _clean_kurztitel((await _llm_json(llm, model, messages)).get("kurztitel"))
            problem = _kurztitel_problem(kurztitel)
            if problem is None:
                _redis_set(cache, cache_key, kurztitel, prefix=_KURZTITEL_CACHE_PREFIX, typehint="Vorgang Kurztitel")
                if metrics is not None:
                    metrics.kurztitel_generated += 1
                return kurztitel
            logger.info("Kurztitel attempt %d rejected (%s): %r", attempt + 1, problem, kurztitel)
            messages += [
                {"role": "assistant", "content": json.dumps({"kurztitel": kurztitel}, ensure_ascii=False)},
                {"role": "user", "content": f"{problem} Formuliere den Kurztitel neu."},
            ]
    except Exception:
        logger.warning("Kurztitel generation failed for %r, using titel", titel[:60], exc_info=True)
    if metrics is not None:
        metrics.kurztitel_fallback += 1
    return titel


# ---------------------------------------------------------------------------
# Vorgang Ressort (GitHub issue #39, DD-055)
# ---------------------------------------------------------------------------

_RESSORT_CACHE_PREFIX = "vorgang-ressort:"
_RESSORT_LISTE = "\n".join(f"- {r.value}" for r in Ressort)

# The rules mirror pazufa-scraper-bb's RESSORT_PROMPT (its issue #53) so a Ressort
# means the same thing in every Land's data: the subject matter of the regulation
# decides, not the body that submitted it. Keep the two in sync.
RESSORT_PROMPT = f"""\
Du bist ein parlamentarischer Analyst. Ordne den folgenden parlamentarischen Vorgang
genau einem Ressort (Ministerium/Geschäftsbereich) zu.

Wähle EXAKT einen Wert aus dieser Liste, Schreibweise unverändert übernehmen:
{_RESSORT_LISTE}

REGELN:
- Maßgeblich ist der fachliche Schwerpunkt der Regelung, nicht der einbringende Akteur.
- Haushaltsgesetze und Gesetze zu Abgaben/Steuern gehören zu "Finanzen".
- Kommunalrecht und Gesetze zur Gemeinde-/Kreisebene gehören zu "Kommunales".
- Wenn kein Wert der Liste fachlich passt: null zurückgeben.

Begründe zuerst kurz (ein Satz), dann nenne das Ressort.
Antworte ausschließlich mit validem JSON:
{{"begruendung": "...", "ressort": "..."}} oder {{"begruendung": "...", "ressort": null}}"""


async def vorgang_ressort(
    llm: LLMConnector | None,
    titel: str,
    zusammenfassung: str | None,
    model: str = "gpt-5-nano",
    cache: BawueCache | None = None,
    metrics: LLMMetrics | None = None,
) -> Ressort | None:
    """The Ressort a Vorgang belongs to, classified from *titel* + *zusammenfassung*.

    Returns None when the LLM is off, the title is the `TODO` placeholder (there is
    nothing to classify, and an invented Ressort is indistinguishable from a real
    one downstream), the call fails, or no listed Ressort fits — the caller then
    omits the field instead of sending a guess.

    The classification *including* a "nothing fits" answer is cached under its own
    prefix, so the `llm-semantics:` cache stays untouched (DD-052/DD-053) and a
    permanent null is not re-asked on every re-derivation. Only a failed call stays
    uncached, so a transient error does not pin an empty result.

    Each classification — cached or not — is counted into *metrics* (issue #56).
    """
    if llm is None or not titel.strip() or titel.strip() == TODO_MARKER:
        return None

    user_message = f"{RESSORT_PROMPT}\n\nTitel: {titel}\n\nZusammenfassung: {zusammenfassung or 'keine'}"
    cache_key = _prompt_cache_key(user_message, model)
    # A Redis error propagates: a fallback cached with the Vorgang would stick (issue #105).
    cached = _redis_get(cache, cache_key, prefix=_RESSORT_CACHE_PREFIX, typehint="Vorgang Ressort")
    if cached is not None:
        raw = _cached_ressort(cached)
    else:
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ]
        try:
            answer = await _llm_json(llm, model, messages)
            raw = answer.get("ressort")  # inside: valid JSON need not be an object
        except Exception:
            logger.warning("Ressort classification failed for %r", titel[:60], exc_info=True)
            if metrics is not None:
                metrics.ressort_failed += 1
            return None
        # %.60r: the answer is untrusted model output — bounded, and on one log line.
        logger.info("Ressort: %s → %.60r (%.60r)", titel[:40], raw, answer.get("begruendung"))
        # The raw answer travels with its begründung: a wrong classification can be
        # examined later without re-calling the model, and a rejected one stays
        # rejected instead of reading back as null (issue #56).
        _redis_set(
            cache,
            cache_key,
            json.dumps({"ressort": raw, "begruendung": answer.get("begruendung")}),
            prefix=_RESSORT_CACHE_PREFIX,
            typehint="Vorgang Ressort",
        )

    ressort = _parse_ressort(raw, titel)
    if metrics is not None:
        if ressort is not None:
            metrics.ressort_classified += 1
        elif raw is None:
            metrics.ressort_null += 1
        else:
            metrics.ressort_rejected += 1
    return ressort


def _cached_ressort(cached: object) -> object:
    """The Ressort value out of a cached answer.

    Entries are JSON objects (value + begründung). Anything else is read as a bare
    value: a malformed entry must not crash a run, and the first iteration of this
    cache stored the plain string.
    """
    try:
        return json.loads(cached).get("ressort")
    except (TypeError, ValueError, AttributeError):
        return cached


def _ressort_lookup_key(value: str) -> str:
    """Spelling-tolerant key: case, spacing and the separators of the compound values.

    8 of the 33 Ressort values are slash- or hyphen-joined ("Verkehr/Infrastruktur",
    "Landes-/Stadtentwicklung"), which is exactly where a small model drifts —
    "Verkehr / Infrastruktur", lower case, or the enum member name.
    """
    return re.sub(r"[^\w]+", "", value).casefold()


_RESSORT_BY_KEY: dict[str, Ressort] = {_ressort_lookup_key(r.value): r for r in Ressort}


def _parse_ressort(raw: object, titel: str) -> Ressort | None:
    """The `Ressort` member *raw* names, None for null or anything off the enum.

    A near-miss spelling of a compound value is normalised rather than dropped;
    anything else but null — an unknown value, an empty string, a non-string — is
    logged at warning level, so a drift between the model and the enum shows up in
    the run log instead of as a quiet gap.
    """
    if raw is None:
        return None
    ressort = _RESSORT_BY_KEY.get(_ressort_lookup_key(raw)) if isinstance(raw, str) else None
    if ressort is None:
        logger.warning("LLM returned unknown Ressort %.60r for %r", raw, titel[:60])
    return ressort
