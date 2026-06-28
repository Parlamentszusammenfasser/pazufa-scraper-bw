"""Internal data structures for raw PARLIS data before conversion to framework models."""

import re
from enum import StrEnum
from typing import TypedDict

# ---------------------------------------------------------------------------
# OpenAPI model/enum import surface (migration Phase 0.3 — type-alias adapter).
#
# All BaWue code imports the generated API models/enums from *here* rather than
# directly from `openapi_client.models`. This makes the Phase 1 client swap a
# single edit in this file instead of a 14-file sweep.
#
#   PHASE 0 (now):  re-export from `openapi_client.models`  (spec v0.2.2)
#   PHASE 1 (flip): re-export from `pazufa_corelib.api_client.models.*` (v0.2.3)
#
# Caveats the flip does NOT cover (see docs/openapi_v022_to_v023_diff.md):
#   - Enum *member* references still say `*_MINUS_*` and must be renamed.
#   - `StationDokumenteInner` does not exist in the new client (→ `Dokument | str`).
#   - `Dokument(hash=...)` becomes `Dokument(hash_=...)`.
# ---------------------------------------------------------------------------
from openapi_client.models import (
    Autor,
    Doktyp,
    Dokument,
    Gremium,
    Parlament,
    Sitzung,
    Station,
    StationDokumenteInner,
    Stationstyp,
    VgIdent,
    Vorgang,
    Vorgangstyp,
)

__all__ = [
    "TODO_MARKER",
    "Autor",
    "CanonicalOrganisation",
    "Doktyp",
    "Dokument",
    "Gremium",
    "Parlament",
    "RawFundstelle",
    "RawVorgang",
    "ReservedGremium",
    "Sitzung",
    "Station",
    "StationDokumenteInner",
    "Stationstyp",
    "VgIdent",
    "Vorgang",
    "Vorgangstyp",
    "canonicalize_organisation",
    "is_verfassungsaendernd",
    "none_if_blank",
    "todo_if_blank",
]


class CanonicalOrganisation(StrEnum):
    """Canonical form for the well-known finite set of Baden-Württemberg
    organizations appearing in `Autor.organisation` (DD-022).

    Scope is intentionally limited to entities with a stable, official name:
    the five Landtag-BW Fraktionen (per https://www.landtag-bw.de/home/fraktionen/)
    and the state government. Open-set organizations (individual ministries,
    external stakeholders, expert authors, …) are NOT listed — they pass
    through `canonicalize_organisation` unchanged, because enumerating them
    is both impractical and unnecessary: the backend already has a pg_trgm
    `SIMILARITY` canary on `Autor` inserts that flags near-duplicates.

    Canonical forms match the Landtag's own usage: `Fraktion GRÜNE` has no
    `der` because GRÜNE is styled as a proper name. This is a quirk, not a
    bug — aligning to the Landtag's convention avoids inventing a third
    variant.

    `StrEnum` members ARE `str` instances, so they pass through pydantic's
    `StrictStr` validation on `Autor.organisation` without conversion.
    """

    FRAKTION_GRUENE = "Fraktion GRÜNE"
    FRAKTION_CDU = "Fraktion der CDU"
    FRAKTION_SPD = "Fraktion der SPD"
    FRAKTION_FDP_DVP = "Fraktion der FDP/DVP"
    FRAKTION_AFD = "Fraktion der AfD"
    LANDESREGIERUNG = "Landesregierung"


# Lookup-normalized form (lower-case, non-alphanumeric stripped) → canonical.
# Each canonical value also appears as its own key so the function is idempotent.
# Variants are the forms observed in production + plausible alternate spellings
# seen on Landtag-BW sources; add new rows when a divergent form is observed.
_ORGANISATION_ALIASES: dict[str, CanonicalOrganisation] = {
    # GRÜNE — Landtag-BW spelling omits "der"
    "fraktiongrüne": CanonicalOrganisation.FRAKTION_GRUENE,
    "fraktiondergrünen": CanonicalOrganisation.FRAKTION_GRUENE,
    "bündnis90diegrünen": CanonicalOrganisation.FRAKTION_GRUENE,
    "grünefraktion": CanonicalOrganisation.FRAKTION_GRUENE,
    # CDU
    "fraktiondercdu": CanonicalOrganisation.FRAKTION_CDU,
    "cdufraktion": CanonicalOrganisation.FRAKTION_CDU,
    # SPD
    "fraktionderspd": CanonicalOrganisation.FRAKTION_SPD,
    "spdfraktion": CanonicalOrganisation.FRAKTION_SPD,
    # FDP/DVP
    "fraktionderfdpdvp": CanonicalOrganisation.FRAKTION_FDP_DVP,
    "fdpdvpfraktion": CanonicalOrganisation.FRAKTION_FDP_DVP,
    "fraktionderfdp": CanonicalOrganisation.FRAKTION_FDP_DVP,  # Bundestag-style short form
    # AfD
    "fraktionderafd": CanonicalOrganisation.FRAKTION_AFD,
    "afdfraktion": CanonicalOrganisation.FRAKTION_AFD,
    "alternativefürdeutschlandafd": CanonicalOrganisation.FRAKTION_AFD,  # seen in BY data
    # Landesregierung
    "landesregierung": CanonicalOrganisation.LANDESREGIERUNG,
    "landesregierungbadenwürttemberg": CanonicalOrganisation.LANDESREGIERUNG,
    "badenwürttembergischelandesregierung": CanonicalOrganisation.LANDESREGIERUNG,
    "regierungbadenwürttemberg": CanonicalOrganisation.LANDESREGIERUNG,
}


_NON_ALNUM_RE = re.compile(r"[^\w]+", re.UNICODE)


def _org_lookup_key(raw: str) -> str:
    """Lowercase + strip non-alphanumeric for variant-tolerant lookup."""
    return _NON_ALNUM_RE.sub("", raw).lower()


TODO_MARKER = "TODO"
"""Placeholder for required string fields whose source value is missing
or empty. The new backend rejects empty strings; required fields must
carry this marker so a human (or a later enrichment pass) can fill them
in. Optional fields should use :func:`none_if_blank` instead."""


def todo_if_blank(value: str | None) -> str:
    """Return ``value`` stripped if non-blank, else ``TODO_MARKER``.

    Use for *required* string fields (Pydantic ``StrictStr``) whose source
    can be empty or whitespace-only.
    """
    if value and value.strip():
        return value
    return TODO_MARKER


def none_if_blank(value: str | None) -> str | None:
    """Return ``value`` if non-blank, else ``None``.

    Use for *optional* string fields so the JSON payload omits the field
    entirely instead of sending an empty string.
    """
    if value and value.strip():
        return value
    return None


def canonicalize_organisation(raw: str) -> str:
    """Map an organisation string to its canonical form (DD-022).

    Returns a `CanonicalOrganisation` member (a str subclass) when the input
    matches a known alias, otherwise returns the input string unchanged.
    Whitespace is always stripped.
    """
    stripped = raw.strip()
    if not stripped:
        return stripped
    return _ORGANISATION_ALIASES.get(_org_lookup_key(stripped), stripped)


# Matches the two canonical German title phrasings for acts that amend the
# Landesverfassung:
#   - "Änderung der Verfassung" / "Änderung der Landesverfassung"
#   - "Verfassungsänderung" (nominal compound)
# A `(?<!\w)` guard on the compound form avoids matches inside unrelated
# compounds like "Landesverfassungsschutz" or "Bundesverfassungsgericht".
_VERFASSUNGSAENDERND_RE = re.compile(
    r"Änderung\s+der\s+(Landes)?Verfassung|(?<!\w)Verfassungsänderung",
    re.IGNORECASE,
)


def is_verfassungsaendernd(titel: str) -> bool:
    """Heuristic: does the Vorgang title indicate a Landesverfassungs-Änderung (DD-023)?

    PARLIS does not expose a `verfassungsaendernd` attribute, so the flag is
    inferred from the title text. Only the two canonical phrasings match:
    "Änderung der (Landes-)Verfassung" and the compound "Verfassungsänderung".
    Everything else returns ``False`` — including titles that merely mention
    `Verfassungsschutz` or amend non-constitutional acts.
    """
    if not titel or not titel.strip():
        return False
    return _VERFASSUNGSAENDERND_RE.search(titel) is not None


class ReservedGremium(StrEnum):
    """Canonical Gremium names reserved by the community DoD + OpenAPI spec.

    Sources:
    - `vendor/pazufa-collector-core/openapi.yaml` field `Gremium.name`:
      "'plenum', 'regierung', 'volk' sind reservierte namen".
    - Community DoD wiki: adds `gesetzesblatt` "für die Veröffentlichung im
      Gesetzesblatt" and describes `plenum` as the default catch-all.
    - BY reference scraper (`vendor/pazufa-collector/collector/scrapers/bylt_scraper.py`):
      emits `gesetzesblatt` literally for `postparl-gsblt` stations and `plenum`
      for all other non-committee stations.

    `GESETZESBLATT` is absent from the spec description but present in the wiki
    and in the BY scraper's production output — the spec description appears
    incomplete rather than authoritative (the spec schema itself accepts any
    string). See DD-021.

    `StrEnum` members ARE `str` instances, so they pass through pydantic's
    `StrictStr` validation on `Gremium.name` without conversion.
    """

    PLENUM = "plenum"
    REGIERUNG = "regierung"
    VOLK = "volk"
    GESETZESBLATT = "gesetzesblatt"


class RawFundstelle(TypedDict, total=False):
    """Structured data parsed from a PARLIS Fundstelle text entry."""

    raw: str
    datum: str
    drucksache: str | None
    plenarprotokoll: str | None
    station_typ: str
    autor_text: str | None
    ausschuss: str | None
    seiten: int | None
    pdf_url: str | None


class RawVorgang(TypedDict, total=False):
    """Raw Vorgang data as returned by the PARLIS HTML parser.

    Contains both fixed keys (titel, vorgangs_id, etc.) and dynamic keys
    parsed from PARLIS ``<dt>/<dd>`` elements (Vorgangstyp, Initiative, ...).
    """

    titel: str
    vorgangs_id: str
    detail_url: str
    fundstellen_parsed: list[RawFundstelle]
    # Dynamic PARLIS keys (from <dt>/<dd> parsing):
    Vorgangstyp: str
    Initiative: str
