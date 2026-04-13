"""Mapping from PARLIS terminology to PaZuFa enum values.

The dictionaries below are fully populated from the architecture document.
The matching functions use case-insensitive substring matching against dictionary keys.
"""

import re

from openapi_client.models.doktyp import Doktyp
from openapi_client.models.stationstyp import Stationstyp
from openapi_client.models.vorgangstyp import Vorgangstyp

# ---------------------------------------------------------------------------
# Vorgangstyp mapping: PARLIS Vorgangstyp string → PaZuFa Vorgangstyp
# ---------------------------------------------------------------------------
VORGANGSTYP_MAP: dict[str, Vorgangstyp] = {
    "Gesetzgebung": Vorgangstyp.GG_MINUS_LAND_MINUS_PARL,
    "Haushaltsgesetzgebung": Vorgangstyp.GG_MINUS_LAND_MINUS_PARL,
    "Volksantrag": Vorgangstyp.GG_MINUS_LAND_MINUS_VOLK,
    "Antrag": Vorgangstyp.SONSTIG,
    "Antrag der Landesregierung/eines Ministeriums": Vorgangstyp.SONSTIG,
    "Antrag des Rechnungshofs": Vorgangstyp.SONSTIG,
    "Kleine Anfrage": Vorgangstyp.SONSTIG,
    "Große Anfrage": Vorgangstyp.SONSTIG,
    "Mündliche Anfrage": Vorgangstyp.SONSTIG,
    "Aktuelle Debatte": Vorgangstyp.SONSTIG,
    "Anmerkung zur Plenarsitzung": Vorgangstyp.SONSTIG,
    "Ansprache/Erklärung/Mitteilung": Vorgangstyp.SONSTIG,
    "Bericht des Parlamentarischen Kontrollgremiums": Vorgangstyp.SONSTIG,
    "Besetzung externer Gremien": Vorgangstyp.SONSTIG,
    "Besetzung interner Gremien": Vorgangstyp.SONSTIG,
    "Enquetekommission": Vorgangstyp.SONSTIG,
    "EU-Vorlage": Vorgangstyp.SONSTIG,
    "Geschäftsordnung": Vorgangstyp.SONSTIG,
    "Immunitätsangelegenheit": Vorgangstyp.SONSTIG,
    "Mitteilung der Landesregierung/eines Ministeriums": Vorgangstyp.SONSTIG,
    "Mitteilung des Bürgerbeauftragten": Vorgangstyp.SONSTIG,
    "Mitteilung des Landesbeauftragten für den Datenschutz": Vorgangstyp.SONSTIG,
    "Mitteilung des Präsidenten": Vorgangstyp.SONSTIG,
    "Mitteilung des Rechnungshofs": Vorgangstyp.SONSTIG,
    "Petitionen": Vorgangstyp.SONSTIG,
    "Regierungsbefragung": Vorgangstyp.SONSTIG,
    "Regierungserklärung/Regierungsinformation": Vorgangstyp.SONSTIG,
    "Schreiben des Bundesverfassungsgerichts": Vorgangstyp.SONSTIG,
    "Schreiben des Verfassungsgerichtshofs": Vorgangstyp.SONSTIG,
    "Untersuchungsausschuss": Vorgangstyp.SONSTIG,
    "Wahl im Landtag": Vorgangstyp.SONSTIG,
    "Wahlprüfung": Vorgangstyp.SONSTIG,
}

# ---------------------------------------------------------------------------
# Stationstyp-Referenz: Alle 16 Enum-Werte aus der OpenAPI-Spezifikation
#
# Vorparlamentarisch (preparl-*):
#   PREPARL_MINUS_REGENT  — Regierungsentwurf (Gesetzentwurf der Landesregierung)
#   PREPARL_MINUS_ECKPUP  — Eckpunktepapier (Vorentwurf mit Kernpunkten)
#   PREPARL_MINUS_REGBSL  — Regierungsbeschluss (Kabinettsbeschluss)
#   PREPARL_MINUS_VBEGDE  — Verbändebeteiligung / Begründung (Anhörung externer Verbände)
#
# Parlamentarisch (parl-*):
#   PARL_MINUS_INITIATIV  — Parlamentarische Initiative (Gesetzentwurf, Antrag, Anfrage)
#   PARL_MINUS_AUSSCHBER  — Ausschussberatung (Beratung in Fachausschüssen)
#   PARL_MINUS_VOLLVLSGN  — Vollversammlung / Lesung (1./2./3. Lesung im Plenum)
#   PARL_MINUS_AKZEPTANZ  — Akzeptanz (Verabschiedung / Annahme durch den Landtag)
#   PARL_MINUS_ABLEHNUNG  — Ablehnung (Ablehnung durch den Landtag)
#   PARL_MINUS_ZURUECKGZ  — Zurückgezogen (Vorgang vom Initiator zurückgezogen)
#   PARL_MINUS_GGENTWURF  — Gegenentwurf (Alternativentwurf zu einem Gesetzentwurf)
#
# Nachparlamentarisch (postparl-*):
#   POSTPARL_MINUS_VESJA  — Volksentscheid Ja (Referendum angenommen)
#   POSTPARL_MINUS_VESNE  — Volksentscheid Nein (Referendum abgelehnt)
#   POSTPARL_MINUS_GSBLT  — Gesetzblatt (Verkündung im Gesetzblatt)
#   POSTPARL_MINUS_KRAFT  — Inkrafttreten (Gesetz tritt in Kraft)
#
# Sonstige:
#   SONSTIG               — Nicht zuordenbare Stationen
# ---------------------------------------------------------------------------
# Stationstyp mapping: Fundstelle text pattern → PaZuFa Stationstyp
# Ordered longest-first so "Beschlussempfehlung und Bericht" matches before
# shorter patterns. "Gesetzentwurf" must come after "Erste/Zweite/Dritte Beratung".
# ---------------------------------------------------------------------------
STATIONSTYP_MAP: dict[str, Stationstyp] = {
    "Gesetzentwurf": Stationstyp.PARL_MINUS_INITIATIV,
    "Antrag": Stationstyp.PARL_MINUS_INITIATIV,
    "Anträge": Stationstyp.PARL_MINUS_INITIATIV,
    "Kleine Anfrage": Stationstyp.PARL_MINUS_INITIATIV,
    "Große Anfrage": Stationstyp.PARL_MINUS_INITIATIV,
    "Mündliche Anfrage": Stationstyp.PARL_MINUS_INITIATIV,
    "Volksantrag": Stationstyp.PARL_MINUS_INITIATIV,
    "Überweisung": Stationstyp.PARL_MINUS_VOLLVLSGN,
    "Erste Beratung": Stationstyp.PARL_MINUS_VOLLVLSGN,
    "Zweite Beratung": Stationstyp.PARL_MINUS_VOLLVLSGN,
    "Dritte Beratung": Stationstyp.PARL_MINUS_VOLLVLSGN,
    "Beratung": Stationstyp.PARL_MINUS_VOLLVLSGN,
    "Beschlussempfehlung und Bericht": Stationstyp.PARL_MINUS_AUSSCHBER,
    "Bericht und Empfehlungen": Stationstyp.PARL_MINUS_AUSSCHBER,
    "Ausschussberatung": Stationstyp.PARL_MINUS_AUSSCHBER,
    "Gesetzesbeschluss": Stationstyp.PARL_MINUS_AKZEPTANZ,
    "Beschluss des Landtags in": Stationstyp.PARL_MINUS_VOLLVLSGN,
    "Beschluss des Landtags": Stationstyp.PARL_MINUS_AKZEPTANZ,
    "Zustimmung": Stationstyp.PARL_MINUS_AKZEPTANZ,
    "Annahme": Stationstyp.PARL_MINUS_AKZEPTANZ,
    "Ablehnung": Stationstyp.PARL_MINUS_ABLEHNUNG,
    "Bekanntmachung": Stationstyp.POSTPARL_MINUS_GSBLT,
    "Gesetzblatt": Stationstyp.POSTPARL_MINUS_GSBLT,
    "Gesetz": Stationstyp.POSTPARL_MINUS_GSBLT,
    "Inkrafttreten": Stationstyp.POSTPARL_MINUS_KRAFT,
}

# Sorted keys longest-first for greedy matching
_STATIONSTYP_KEYS_SORTED = sorted(STATIONSTYP_MAP.keys(), key=len, reverse=True)

# ---------------------------------------------------------------------------
# Dokumententyp mapping: document context → PaZuFa Doktyp
# ---------------------------------------------------------------------------
DOKUMENTENTYP_MAP: dict[str, Doktyp] = {
    "Gesetzentwurf": Doktyp.ENTWURF,
    "Antrag": Doktyp.ANTRAG,
    "Kleine Anfrage": Doktyp.ANFRAGE,
    "Große Anfrage": Doktyp.ANFRAGE,
    "Mündliche Anfrage": Doktyp.ANFRAGE,
    "Antwort": Doktyp.ANTWORT,
    "Stellungnahme": Doktyp.STELLUNGNAHME,
    "Beschlussempfehlung": Doktyp.BESCHLUSSEMPF,
    "Plenarprotokoll": Doktyp.REDEPROTOKOLL,
    "Mitteilung": Doktyp.MITTEILUNG,
    # Plenary readings → redeprotokoll
    "Erste Beratung": Doktyp.REDEPROTOKOLL,
    "Zweite Beratung": Doktyp.REDEPROTOKOLL,
    "Dritte Beratung": Doktyp.REDEPROTOKOLL,
    "Beratung": Doktyp.REDEPROTOKOLL,
    # Legislative decision → mitteilung
    "Gesetzesbeschluss": Doktyp.MITTEILUNG,
    "Beschluss des Landtags in": Doktyp.REDEPROTOKOLL,
    "Beschluss des Landtags": Doktyp.MITTEILUNG,
    "Zustimmung": Doktyp.MITTEILUNG,
    "Annahme": Doktyp.MITTEILUNG,
    # Gesetzblatt publication → mitteilung
    "Gesetzblatt": Doktyp.MITTEILUNG,
    "Bekanntmachung": Doktyp.MITTEILUNG,
    "Gesetz": Doktyp.MITTEILUNG,
}

_DOKUMENTENTYP_KEYS_SORTED = sorted(DOKUMENTENTYP_MAP.keys(), key=len, reverse=True)


def map_vorgangstyp(parlis_typ: str) -> Vorgangstyp:
    """Map a PARLIS Vorgangstyp string to the PaZuFa Vorgangstyp enum."""
    return VORGANGSTYP_MAP.get(parlis_typ, Vorgangstyp.SONSTIG)


def _normalize_whitespace(text: str) -> str:
    """Collapse runs of whitespace into single spaces for reliable substring matching."""
    return re.sub(r"\s+", " ", text).strip()


def map_stationstyp(fundstelle_text: str, initiator: str | None = None) -> Stationstyp:
    """Map a Fundstelle text to the PaZuFa Stationstyp enum.

    Uses case-insensitive substring matching against known patterns, longest first.
    Whitespace is normalized so that internal double-spaces (common in PARLIS
    Fundstelle text) don't prevent matching of multi-word keys like
    "Beschluss des Landtags in".
    If the station is a Gesetzentwurf from the Landesregierung, maps to PREPARL_REGBSL
    (Kabinettsbeschluss) — PARLIS shows the bill after the cabinet decided to submit it.
    """
    text_lower = _normalize_whitespace(fundstelle_text).lower()
    for key in _STATIONSTYP_KEYS_SORTED:
        if key.lower() in text_lower:
            if key == "Gesetzentwurf" and initiator and "Landesregierung" in initiator:
                return Stationstyp.PREPARL_MINUS_REGBSL
            return STATIONSTYP_MAP[key]
    return Stationstyp.SONSTIG


def map_dokumententyp(context: str, is_vorparlamentarisch: bool = False) -> Doktyp:
    """Map a document context string to the PaZuFa Doktyp enum."""
    context_lower = _normalize_whitespace(context).lower()
    for key in _DOKUMENTENTYP_KEYS_SORTED:
        if key.lower() in context_lower:
            if key == "Gesetzentwurf" and is_vorparlamentarisch:
                return Doktyp.PREPARL_MINUS_ENTWURF
            return DOKUMENTENTYP_MAP[key]
    return Doktyp.SONSTIG
