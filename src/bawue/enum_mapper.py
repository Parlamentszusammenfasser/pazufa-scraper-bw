"""Mapping from PARLIS terminology to PaZuFa enum values.

The dictionaries below are fully populated from the architecture document.
The matching functions use case-insensitive substring matching against dictionary keys.
"""

import re

from bawue.types import Doktyp, Stationstyp, Vorgangstyp

# ---------------------------------------------------------------------------
# Vorgangstyp mapping: PARLIS Vorgangstyp string → PaZuFa Vorgangstyp
# ---------------------------------------------------------------------------
VORGANGSTYP_MAP: dict[str, Vorgangstyp] = {
    "Gesetzgebung": Vorgangstyp.GG_LAND_PARL,
    "Haushaltsgesetzgebung": Vorgangstyp.GG_LAND_PARL,
    "Volksantrag": Vorgangstyp.GG_LAND_VOLK,
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
#   PREPARL_REGENT  — Regierungsentwurf (Gesetzentwurf der Landesregierung)
#   PREPARL_ECKPUP  — Eckpunktepapier (Vorentwurf mit Kernpunkten)
#   PREPARL_REGBSL  — Regierungsbeschluss (Kabinettsbeschluss)
#   PREPARL_VBEGDE  — Verbändebeteiligung / Begründung (Anhörung externer Verbände)
#
# Parlamentarisch (parl-*):
#   PARL_INITIATIV  — Parlamentarische Initiative (Gesetzentwurf, Antrag, Anfrage)
#   PARL_AUSSCHBER  — Ausschussberatung (Beratung in Fachausschüssen)
#   PARL_VOLLVLSGN  — Vollversammlung / Lesung (1./2./3. Lesung im Plenum)
#   PARL_AKZEPTANZ  — Akzeptanz (Verabschiedung / Annahme durch den Landtag)
#   PARL_ABLEHNUNG  — Ablehnung (Ablehnung durch den Landtag)
#   PARL_ZURUECKGZ  — Zurückgezogen (Vorgang vom Initiator zurückgezogen)
#   PARL_GGENTWURF  — Gegenentwurf (Alternativentwurf zu einem Gesetzentwurf)
#
# Nachparlamentarisch (postparl-*):
#   POSTPARL_VESJA  — Volksentscheid Ja (Referendum angenommen)
#   POSTPARL_VESNE  — Volksentscheid Nein (Referendum abgelehnt)
#   POSTPARL_GSBLT  — Gesetzblatt (Verkündung im Gesetzblatt)
#   POSTPARL_KRAFT  — Inkrafttreten (Gesetz tritt in Kraft)
#
# Sonstige:
#   SONSTIG               — Nicht zuordenbare Stationen
# ---------------------------------------------------------------------------
# Stationstyp mapping: Fundstelle text pattern → PaZuFa Stationstyp
# Ordered longest-first so "Beschlussempfehlung und Bericht" matches before
# shorter patterns. "Gesetzentwurf" must come after "Erste/Zweite/Dritte Beratung".
# ---------------------------------------------------------------------------
STATIONSTYP_MAP: dict[str, Stationstyp] = {
    "Gesetzentwurf": Stationstyp.PARL_INITIATIV,
    "Gesetzentwürfe": Stationstyp.PARL_INITIATIV,
    "Antrag": Stationstyp.PARL_INITIATIV,
    "Anträge": Stationstyp.PARL_INITIATIV,
    "Kleine Anfrage": Stationstyp.PARL_INITIATIV,
    "Große Anfrage": Stationstyp.PARL_INITIATIV,
    "Mündliche Anfrage": Stationstyp.PARL_INITIATIV,
    "Volksantrag": Stationstyp.PARL_INITIATIV,
    "Überweisung": Stationstyp.PARL_VOLLVLSGN,
    "Erste Beratung": Stationstyp.PARL_VOLLVLSGN,
    "Zweite Beratung": Stationstyp.PARL_VOLLVLSGN,
    "Dritte Beratung": Stationstyp.PARL_VOLLVLSGN,
    "Beratung": Stationstyp.PARL_VOLLVLSGN,
    "Beschlussempfehlung und Bericht": Stationstyp.PARL_AUSSCHBER,
    "Bericht und Empfehlungen": Stationstyp.PARL_AUSSCHBER,
    "Ausschussberatung": Stationstyp.PARL_AUSSCHBER,
    "Gesetzesbeschluss": Stationstyp.PARL_AKZEPTANZ,
    "Gesetzesbeschlüsse": Stationstyp.PARL_AKZEPTANZ,
    "Beschluss des Landtags in": Stationstyp.PARL_VOLLVLSGN,
    "Beschluss des Landtags": Stationstyp.PARL_AKZEPTANZ,
    "Zustimmung": Stationstyp.PARL_AKZEPTANZ,
    "Annahme": Stationstyp.PARL_AKZEPTANZ,
    "Ablehnung": Stationstyp.PARL_ABLEHNUNG,
    "Bekanntmachung": Stationstyp.POSTPARL_GSBLT,
    "Gesetzblatt": Stationstyp.POSTPARL_GSBLT,
    "Gesetz": Stationstyp.POSTPARL_GSBLT,
    "Inkrafttreten": Stationstyp.POSTPARL_KRAFT,
}

# Sorted keys longest-first for greedy matching
_STATIONSTYP_KEYS_SORTED = sorted(STATIONSTYP_MAP.keys(), key=len, reverse=True)

# ---------------------------------------------------------------------------
# Dokumententyp mapping: document context → PaZuFa Doktyp
# ---------------------------------------------------------------------------
DOKUMENTENTYP_MAP: dict[str, Doktyp] = {
    "Gesetzentwurf": Doktyp.ENTWURF,
    "Antrag": Doktyp.ANTRAG,
    # Umlaut plurals need their own keys: the vowel change means "Anträge" does
    # not contain "Antrag", so the plural silently misses its singular key.
    # "Anträge" is observed in production (issue #69); the other two are
    # defensive coverage per docs/observed_station_types.md, and matter because
    # they do not fall through to SONSTIG but to the shorter "Gesetz" key —
    # a wrong typ rather than a visible gap.
    "Anträge": Doktyp.ANTRAG,
    "Gesetzentwürfe": Doktyp.ENTWURF,
    "Gesetzesbeschlüsse": Doktyp.MITTEILUNG,
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
                return Stationstyp.PREPARL_REGBSL
            return STATIONSTYP_MAP[key]
    return Stationstyp.SONSTIG


def map_dokumententyp(context: str, is_vorparlamentarisch: bool = False) -> Doktyp:
    """Map a document context string to the PaZuFa Doktyp enum."""
    context_lower = _normalize_whitespace(context).lower()
    for key in _DOKUMENTENTYP_KEYS_SORTED:
        if key.lower() in context_lower:
            if key == "Gesetzentwurf" and is_vorparlamentarisch:
                return Doktyp.PREPARL_ENTWURF
            return DOKUMENTENTYP_MAP[key]
    return Doktyp.SONSTIG
