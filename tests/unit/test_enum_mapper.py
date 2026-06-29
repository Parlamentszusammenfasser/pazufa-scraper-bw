"""Tests for the PARLIS→PaZuFa enum mapper."""

import pytest

from bawue.enum_mapper import (
    VORGANGSTYP_MAP,
    map_dokumententyp,
    map_stationstyp,
    map_vorgangstyp,
)
from bawue.types import (
    CanonicalOrganisation,
    Doktyp,
    ReservedGremium,
    Stationstyp,
    Vorgangstyp,
    canonicalize_organisation,
    is_verfassungsaendernd,
)


class TestVorgangstypMapping:
    @pytest.mark.parametrize(
        "parlis_typ,expected",
        [
            ("Gesetzgebung", Vorgangstyp.GG_LAND_PARL),
            ("Haushaltsgesetzgebung", Vorgangstyp.GG_LAND_PARL),
            ("Volksantrag", Vorgangstyp.GG_LAND_VOLK),
            ("Antrag", Vorgangstyp.SONSTIG),
            ("Antrag der Landesregierung/eines Ministeriums", Vorgangstyp.SONSTIG),
            ("Antrag des Rechnungshofs", Vorgangstyp.SONSTIG),
            ("Kleine Anfrage", Vorgangstyp.SONSTIG),
            ("Große Anfrage", Vorgangstyp.SONSTIG),
            ("Mündliche Anfrage", Vorgangstyp.SONSTIG),
            ("Aktuelle Debatte", Vorgangstyp.SONSTIG),
            ("Anmerkung zur Plenarsitzung", Vorgangstyp.SONSTIG),
            ("Ansprache/Erklärung/Mitteilung", Vorgangstyp.SONSTIG),
            ("Bericht des Parlamentarischen Kontrollgremiums", Vorgangstyp.SONSTIG),
            ("Besetzung externer Gremien", Vorgangstyp.SONSTIG),
            ("Besetzung interner Gremien", Vorgangstyp.SONSTIG),
            ("Enquetekommission", Vorgangstyp.SONSTIG),
            ("EU-Vorlage", Vorgangstyp.SONSTIG),
            ("Geschäftsordnung", Vorgangstyp.SONSTIG),
            ("Immunitätsangelegenheit", Vorgangstyp.SONSTIG),
            ("Mitteilung der Landesregierung/eines Ministeriums", Vorgangstyp.SONSTIG),
            ("Mitteilung des Bürgerbeauftragten", Vorgangstyp.SONSTIG),
            ("Mitteilung des Landesbeauftragten für den Datenschutz", Vorgangstyp.SONSTIG),
            ("Mitteilung des Präsidenten", Vorgangstyp.SONSTIG),
            ("Mitteilung des Rechnungshofs", Vorgangstyp.SONSTIG),
            ("Petitionen", Vorgangstyp.SONSTIG),
            ("Regierungsbefragung", Vorgangstyp.SONSTIG),
            ("Regierungserklärung/Regierungsinformation", Vorgangstyp.SONSTIG),
            ("Schreiben des Bundesverfassungsgerichts", Vorgangstyp.SONSTIG),
            ("Schreiben des Verfassungsgerichtshofs", Vorgangstyp.SONSTIG),
            ("Untersuchungsausschuss", Vorgangstyp.SONSTIG),
            ("Wahl im Landtag", Vorgangstyp.SONSTIG),
            ("Wahlprüfung", Vorgangstyp.SONSTIG),
        ],
    )
    def test_known_types(self, parlis_typ, expected):
        assert map_vorgangstyp(parlis_typ) == expected

    def test_unknown_type_defaults_to_sonstig(self):
        assert map_vorgangstyp("Unbekannter Typ") == Vorgangstyp.SONSTIG

    def test_map_covers_all_known_parlis_types(self):
        assert len(VORGANGSTYP_MAP) == 32


class TestStationstypMapping:
    @pytest.mark.parametrize(
        "text,initiator,expected",
        [
            ("Erste Beratung   Plenarprotokoll 17/141 05.02.2026", None, Stationstyp.PARL_VOLLVLSGN),
            ("Zweite Beratung   Plenarprotokoll 17/142 06.02.2026", None, Stationstyp.PARL_VOLLVLSGN),
            (
                "Gesetzentwurf    Fraktion GRÜNE  04.02.2026 Drucksache 17/10266   (13 S.)",
                None,
                Stationstyp.PARL_INITIATIV,
            ),
            (
                "Gesetzentwurf    Landesregierung  04.02.2026 Drucksache 17/10266",
                "Landesregierung",
                Stationstyp.PREPARL_REGBSL,
            ),
            (
                "Beschlussempfehlung und Bericht    Ausschuss für Wirtschaft  02.02.2026 Drucksache 17/10210",
                None,
                Stationstyp.PARL_AUSSCHBER,
            ),
            (
                "Kleine Anfrage   Dr. Schweickert (FDP/DVP)  15.01.2026 Drucksache 17/10143",
                None,
                Stationstyp.PARL_INITIATIV,
            ),
            (
                "Große Anfrage   Fraktion der SPD  10.01.2026 Drucksache 17/10100",
                None,
                Stationstyp.PARL_INITIATIV,
            ),
            (
                "Mündliche Anfrage   Plenarprotokoll 17/141 05.02.2026",
                None,
                Stationstyp.PARL_INITIATIV,
            ),
            ("Zustimmung   Plenarprotokoll 17/143", None, Stationstyp.PARL_AKZEPTANZ),
            (
                "Gesetzesbeschluss des Landtags      05.02.2026 Drucksache 17/10267",
                None,
                Stationstyp.PARL_AKZEPTANZ,
            ),
            (
                "Beschluss des Landtags in Zweiter Beratung      06.02.2026 Drucksache 17/2271",
                None,
                Stationstyp.PARL_VOLLVLSGN,
            ),
            (
                "Beschluss des Landtags in Dritter Beratung      22.12.2021 Drucksache 17/1234",
                None,
                Stationstyp.PARL_VOLLVLSGN,
            ),
            ("Ablehnung   Plenarprotokoll 17/143", None, Stationstyp.PARL_ABLEHNUNG),
            (
                "Gesetz  vom 10. Februar 2026 Gesetzblatt für Baden-Württemberg 2026 Nr. 22",
                None,
                Stationstyp.POSTPARL_GSBLT,
            ),
            (
                "Bekanntmachung der Neufassung      12.05.2021",
                None,
                Stationstyp.POSTPARL_GSBLT,
            ),
            ("Gesetzblatt   15.03.2026", None, Stationstyp.POSTPARL_GSBLT),
            ("Inkrafttreten   01.04.2026", None, Stationstyp.POSTPARL_KRAFT),
            (
                "Änderungsanträge    Fraktion der FDP/DVP  20.07.2021 Drucksache 17/569",
                None,
                Stationstyp.PARL_INITIATIV,
            ),
            (
                "Bericht und Empfehlungen    Petitionsausschuss  15.03.2026 Drucksache 17/1234",
                None,
                Stationstyp.PARL_AUSSCHBER,
            ),
            (
                "Volksantrag    05.02.2023 Drucksache 17/4567",
                None,
                Stationstyp.PARL_INITIATIV,
            ),
            (
                "Beratung   Plenarprotokoll 17/99 12.03.2023",
                None,
                Stationstyp.PARL_VOLLVLSGN,
            ),
            # Gap #2 coverage — STATIONSTYP_MAP keys previously only covered
            # transitively (e.g. "Antrag" via "Änderungsanträge", "Dritte Beratung"
            # only within "Beschluss des Landtags in Dritter Beratung").
            (
                "Antrag    Fraktion der FDP/DVP  10.01.2026 Drucksache 17/10140",
                None,
                Stationstyp.PARL_INITIATIV,
            ),
            (
                "Dritte Beratung   Plenarprotokoll 17/145 15.03.2026",
                None,
                Stationstyp.PARL_VOLLVLSGN,
            ),
            (
                "Ausschussberatung    Ausschuss für Inneres  20.02.2026 Drucksache 17/10180",
                None,
                Stationstyp.PARL_AUSSCHBER,
            ),
            (
                "Annahme   Plenarprotokoll 17/143 12.02.2026",
                None,
                Stationstyp.PARL_AKZEPTANZ,
            ),
            (
                "Beschluss des Landtags      05.02.2026 Drucksache 17/10267",
                None,
                Stationstyp.PARL_AKZEPTANZ,
            ),
        ],
    )
    def test_known_patterns(self, text, initiator, expected):
        assert map_stationstyp(text, initiator=initiator) == expected

    def test_unknown_and_empty_default_to_sonstig(self):
        assert map_stationstyp("unknown text") == Stationstyp.SONSTIG
        assert map_stationstyp("") == Stationstyp.SONSTIG

    def test_case_insensitive(self):
        assert map_stationstyp("erste beratung   Plenarprotokoll 17/141") == Stationstyp.PARL_VOLLVLSGN

    def test_longer_keys_take_precedence_over_gesetz(self):
        """'Gesetzentwurf' and 'Gesetzesbeschluss' must match before shorter 'Gesetz'."""
        assert map_stationstyp("Gesetzentwurf    Fraktion GRÜNE") == Stationstyp.PARL_INITIATIV
        assert map_stationstyp("Gesetzesbeschluss des Landtags      05.02.2026") == Stationstyp.PARL_AKZEPTANZ
        assert map_stationstyp("Gesetzblatt   15.03.2026") == Stationstyp.POSTPARL_GSBLT
        # Only bare "Gesetz" (enacted law) matches the short key
        assert map_stationstyp("Gesetz  vom 10. Februar 2026") == Stationstyp.POSTPARL_GSBLT

    def test_antraege_plural_maps_to_initiativ(self):
        """Plural 'Änderungsanträge' (with umlaut ä) must not fall through to SONSTIG."""
        assert map_stationstyp("Änderungsanträge    Fraktion der FDP/DVP") == Stationstyp.PARL_INITIATIV

    def test_ueberweisung_maps_to_vollversammlung(self):
        """Committee referral 'Überweisung' maps to PARL_VOLLVLSGN, not sonstig."""
        assert map_stationstyp("Überweisung") == Stationstyp.PARL_VOLLVLSGN


# PARLIS station-type strings observed in production that intentionally map to
# SONSTIG at the enum-mapper level. Each entry cites the design decision that
# justifies why — per community DoD, every SONSTIG fallback must be documented.
# Kept as a module-level constant so TestStationstypDocumentedSonstig and
# TestStationstypCoverageAudit share the same canonical list.
DOCUMENTED_SONSTIG_STATION_TYPES: list[tuple[str, str]] = [
    ("Mitteilung", "DD-002: sender/timing-dependent, no single mapping is correct"),
    ("Stellungnahme", "DD-005: scraper attaches as child of preceding station"),
    ("Antwort", "DD-005: scraper attaches as child of preceding station"),
    ("Neufassung", "DD-012: postparl-only meta-entry, scraper skips the whole Vorgang"),
    ("Berichtigung", "DD-012: postparl-only meta-entry, scraper skips the whole Vorgang"),
    ("Dokument", "DD-017: generic PARLIS label with no parliamentary-process meaning"),
]


class TestStationstypDocumentedSonstig:
    """Regression guard for PARLIS station types that intentionally fall through to SONSTIG.

    These are input strings observed in PARLIS whose mapping to SONSTIG is a
    deliberate design choice (not a gap). If any of them starts mapping to a
    concrete enum, either the mapper grew a matching key or a shorter key now
    shadows them — both cases need review against the cited DD.
    """

    @pytest.mark.parametrize("text,reason", DOCUMENTED_SONSTIG_STATION_TYPES)
    def test_documented_sonstig(self, text, reason):
        assert map_stationstyp(text) == Stationstyp.SONSTIG, reason


# Fundstelle station_typ strings observed in PARLIS (without trailing
# author/date payload). Reconciled against the WP 17 full run from
# 2026-04-21/22 — see docs/observed_station_types.md for the inventory and
# refresh procedure. Entries marked "(production)" appeared in that run;
# the others remain as defensive coverage for STATIONSTYP_MAP keys that
# only fire under non-default Vorgangstyp configurations.
OBSERVED_STATION_TYPES: list[str] = [
    # Initiative (parl-initiativ)
    "Gesetzentwurf",  # production, 455x
    "Antrag",  # production, 1x
    "Anträge",
    "Änderungsantrag",  # production, 83x — singular variant
    "Änderungsanträge",  # production, 4x
    "Kleine Anfrage",
    "Große Anfrage",
    "Mündliche Anfrage",
    "Volksantrag",
    # Ausschussberatung (parl-ausschber)
    "Beschlussempfehlung und Bericht",  # production, 265x
    "Bericht und Empfehlungen",
    "Ausschussberatung",
    # Plenarlesungen (parl-vollvlsgn)
    "Erste Beratung",  # production, 263x
    "Zweite Beratung",  # production, 262x
    "Dritte Beratung",
    "Zweite und Dritte Beratung",  # production, 1x — joint reading variant
    "Beratung",
    "Überweisung",
    "Beschluss des Landtags in Zweiter Beratung",  # production, 1x
    "Beschluss des Landtags in Dritter Beratung",
    # Akzeptanz (parl-akzeptanz)
    "Gesetzesbeschluss",
    "Gesetzesbeschluss des Landtags",  # production, 219x — qualified variant
    "Beschluss des Landtags",
    "Zustimmung",
    "Annahme",
    # Ablehnung (parl-ablehnung)
    "Ablehnung",
    # Gesetzblatt / Inkrafttreten (postparl-*)
    "Bekanntmachung",
    "Bekanntmachung über das Inkrafttreten",  # production, 23x
    "Bekanntmachung der Neufassung",  # production, 4x
    "Berichtigung des Gesetzes",  # production, 4x
    "Gesetzblatt",
    "Gesetz",  # production, 219x
    "Inkrafttreten",
    # Documented SONSTIG fallbacks (see DOCUMENTED_SONSTIG_STATION_TYPES)
    "Mitteilung",
    "Stellungnahme",
    "Antwort",
    "Neufassung",
    "Berichtigung",
    "Dokument",
]


class TestStationstypCoverageAudit:
    """DoD audit (Roadmap #9): every PARLIS station type observed in production
    must be exercised by this file.

    Each entry in OBSERVED_STATION_TYPES is either:
      - mapped to a concrete (non-SONSTIG) Stationstyp by the mapper, or
      - listed in DOCUMENTED_SONSTIG_STATION_TYPES with a DD reference.
    """

    @pytest.mark.parametrize("station_typ", OBSERVED_STATION_TYPES)
    def test_every_observed_type_is_handled(self, station_typ):
        """Every observed PARLIS station type must either map concretely or be
        on the documented-SONSTIG allow-list."""
        result = map_stationstyp(station_typ)
        if result is Stationstyp.SONSTIG:
            documented = {t for t, _ in DOCUMENTED_SONSTIG_STATION_TYPES}
            assert station_typ in documented, (
                f"'{station_typ}' maps to SONSTIG but is not in "
                f"DOCUMENTED_SONSTIG_STATION_TYPES — either add a STATIONSTYP_MAP "
                f"entry or document the fallback with a DD reference."
            )


class TestDokumententypMapping:
    @pytest.mark.parametrize(
        "context,is_vorparl,expected",
        [
            ("Gesetzentwurf", False, Doktyp.ENTWURF),
            ("Gesetzentwurf", True, Doktyp.PREPARL_ENTWURF),
            ("Plenarprotokoll", False, Doktyp.REDEPROTOKOLL),
            ("Antrag", False, Doktyp.ANTRAG),
            ("Kleine Anfrage", False, Doktyp.ANFRAGE),
            ("Stellungnahme", False, Doktyp.STELLUNGNAHME),
            ("Beschlussempfehlung", False, Doktyp.BESCHLUSSEMPF),
            # Plenary readings → redeprotokoll
            ("Erste Beratung", False, Doktyp.REDEPROTOKOLL),
            ("Zweite Beratung", False, Doktyp.REDEPROTOKOLL),
            ("Dritte Beratung", False, Doktyp.REDEPROTOKOLL),
            ("Beratung", False, Doktyp.REDEPROTOKOLL),
            # Legislative decision → mitteilung
            ("Gesetzesbeschluss", False, Doktyp.MITTEILUNG),
            ("Beschluss des Landtags", False, Doktyp.MITTEILUNG),
            ("Zustimmung", False, Doktyp.MITTEILUNG),
            ("Annahme", False, Doktyp.MITTEILUNG),
            # Gesetzblatt → mitteilung
            ("Gesetzblatt", False, Doktyp.MITTEILUNG),
            ("Bekanntmachung", False, Doktyp.MITTEILUNG),
            ("Gesetz", False, Doktyp.MITTEILUNG),
        ],
    )
    def test_known_patterns(self, context, is_vorparl, expected):
        assert map_dokumententyp(context, is_vorparlamentarisch=is_vorparl) == expected

    def test_unknown_and_empty_default_to_sonstig(self):
        assert map_dokumententyp("unknown") == Doktyp.SONSTIG
        assert map_dokumententyp("") == Doktyp.SONSTIG

    def test_longer_keys_take_precedence(self):
        """Longest-first matching must prevent short keys from shadowing longer ones."""
        # "Gesetzesbeschluss" (17 chars) matches before "Gesetz" (6 chars)
        assert map_dokumententyp("Gesetzesbeschluss") == Doktyp.MITTEILUNG
        # "Gesetzentwurf" (13 chars) still matches ENTWURF, not "Gesetz" → MITTEILUNG
        assert map_dokumententyp("Gesetzentwurf") == Doktyp.ENTWURF
        # "Beschluss des Landtags" and "Beschlussempfehlung" share no substring conflict
        assert map_dokumententyp("Beschlussempfehlung") == Doktyp.BESCHLUSSEMPF
        assert map_dokumententyp("Beschluss des Landtags") == Doktyp.MITTEILUNG
        # Qualified "Beschluss des Landtags in ..." is a reading vote, not final decision
        assert map_dokumententyp("Beschluss des Landtags in Zweiter Beratung") == Doktyp.REDEPROTOKOLL


class TestEnumValuesExistInFramework:
    """Verify that all enum values used by the BaWue scraper exist in the generated models."""

    def test_all_stationstyp_values_valid(self):
        bawue_values = {
            "preparl-regent",
            "parl-initiativ",
            "parl-ausschber",
            "parl-vollvlsgn",
            "parl-akzeptanz",
            "parl-ablehnung",
            "postparl-gsblt",
            "postparl-kraft",
            "sonstig",
        }
        framework_values = {m.value for m in Stationstyp}
        assert bawue_values.issubset(framework_values)

    def test_all_vorgangstyp_values_valid(self):
        bawue_values = {"gg-land-parl", "gg-land-volk", "bw-einsatz", "sonstig"}
        framework_values = {m.value for m in Vorgangstyp}
        assert bawue_values.issubset(framework_values)

    def test_all_doktyp_values_valid(self):
        bawue_values = {
            "preparl-entwurf",
            "entwurf",
            "antrag",
            "anfrage",
            "antwort",
            "mitteilung",
            "beschlussempf",
            "stellungnahme",
            "gutachten",
            "redeprotokoll",
            "tops",
            "tops-aend",
            "tops-ergz",
            "sonstig",
        }
        framework_values = {m.value for m in Doktyp}
        assert bawue_values == framework_values


class TestReservedGremiumNames:
    """Lock the literal values of ReservedGremium against wiki + spec + BY precedent.

    - `plenum`, `regierung`, `volk` come from the OpenAPI spec
      (`vendor/pazufa-collector-core/openapi.yaml`).
    - `gesetzesblatt` comes from the community DoD wiki and the BY reference
      scraper. It is NOT in the spec description, but the spec schema accepts
      any string, so the wiki/BY convention wins (see DD-021).

    If this set needs to change (spec update, new reserved name), this test
    fails and forces the audit to be redone.
    """

    def test_literal_values_match_spec(self):
        assert {m.value for m in ReservedGremium} == {
            "plenum",
            "regierung",
            "volk",
            "gesetzesblatt",
        }

    def test_strenum_is_str_subclass(self):
        # StrEnum members must be str instances so they pass StrictStr validation
        # on Gremium.name without conversion.
        assert isinstance(ReservedGremium.PLENUM, str)
        assert ReservedGremium.PLENUM == "plenum"


# Autor.organisation strings observed in the WP 17 production run
# (see docs/observed_station_types.md for the extraction procedure applied
# to Autor.organisation instead of Dokument.titel). The "expected" column
# is the canonical form canonicalize_organisation should return.
OBSERVED_ORGANISATIONS: list[tuple[str, str]] = [
    ("Landesregierung", "Landesregierung"),
    ("Fraktion GRÜNE", "Fraktion GRÜNE"),
    ("Fraktion der CDU", "Fraktion der CDU"),
    ("Fraktion der SPD", "Fraktion der SPD"),
    ("Fraktion der FDP/DVP", "Fraktion der FDP/DVP"),
    ("Fraktion der AfD", "Fraktion der AfD"),
    # Open-set organizations — pass through unchanged:
    ("Ständiger Ausschuss", "Ständiger Ausschuss"),
    (
        "Ministerium für Soziales, Gesundheit und Integration",
        "Ministerium für Soziales, Gesundheit und Integration",
    ),
    (
        "Ministerium des Inneren, für Digitalisierung und Kommunen",
        "Ministerium des Inneren, für Digitalisierung und Kommunen",
    ),
]


class TestCanonicalOrganisation:
    """DD-022: canonical-name mapping for Autor.organisation."""

    def test_enum_values_cover_landtag_bw_fraktionen(self):
        """Lock the finite set of canonical forms. Updating this requires an
        audit against https://www.landtag-bw.de/home/fraktionen/."""
        assert {m.value for m in CanonicalOrganisation} == {
            "Fraktion GRÜNE",
            "Fraktion der CDU",
            "Fraktion der SPD",
            "Fraktion der FDP/DVP",
            "Fraktion der AfD",
            "Landesregierung",
        }

    def test_strenum_is_str_subclass(self):
        assert isinstance(CanonicalOrganisation.FRAKTION_CDU, str)
        assert CanonicalOrganisation.FRAKTION_CDU == "Fraktion der CDU"

    @pytest.mark.parametrize("raw,expected", OBSERVED_ORGANISATIONS)
    def test_observed_organisations_map_to_expected(self, raw, expected):
        assert canonicalize_organisation(raw) == expected

    def test_idempotent_on_canonical_forms(self):
        for m in CanonicalOrganisation:
            assert canonicalize_organisation(m.value) == m.value

    @pytest.mark.parametrize(
        "variant,canonical",
        [
            # Capitalization / whitespace
            ("FRAKTION GRÜNE", CanonicalOrganisation.FRAKTION_GRUENE),
            ("  Fraktion der CDU  ", CanonicalOrganisation.FRAKTION_CDU),
            # Grammatical variant
            ("Fraktion der Grünen", CanonicalOrganisation.FRAKTION_GRUENE),
            ("BÜNDNIS 90/DIE GRÜNEN", CanonicalOrganisation.FRAKTION_GRUENE),
            # "<Partei>-Fraktion" short form (BY-style)
            ("CDU-Fraktion", CanonicalOrganisation.FRAKTION_CDU),
            ("SPD-Fraktion", CanonicalOrganisation.FRAKTION_SPD),
            ("AfD-Fraktion", CanonicalOrganisation.FRAKTION_AFD),
            ("FDP/DVP-Fraktion", CanonicalOrganisation.FRAKTION_FDP_DVP),
            # Expanded party name seen in BY fixtures
            ("Alternative für Deutschland (AfD)", CanonicalOrganisation.FRAKTION_AFD),
            # Landesregierung variants
            ("Baden-Württembergische Landesregierung", CanonicalOrganisation.LANDESREGIERUNG),
            ("Landesregierung Baden-Württemberg", CanonicalOrganisation.LANDESREGIERUNG),
        ],
    )
    def test_known_variants_map_to_canonical(self, variant, canonical):
        assert canonicalize_organisation(variant) == canonical

    def test_unknown_organisations_pass_through(self):
        """Open-set entries (ministries, external orgs) stay as-is."""
        ministry = "Ministerium für Umwelt, Klima und Energiewirtschaft"
        assert canonicalize_organisation(ministry) == ministry
        assert canonicalize_organisation("Verband der Podologen") == "Verband der Podologen"

    def test_empty_and_whitespace(self):
        assert canonicalize_organisation("") == ""
        assert canonicalize_organisation("   ") == ""


class TestIsVerfassungsaendernd:
    """DD-023: title-based heuristic for the `verfassungsaendernd` flag.

    PARLIS does not expose this attribute, and the community DoD's "omit
    object" rule cannot apply (would drop 100 % of Vorgänge). The heuristic
    recognises the two canonical German phrasings used when an Act amends
    the Landesverfassung: "Änderung der (Landes)?Verfassung" and the
    nominal compound "Verfassungsänderung". Everything else stays `False`.
    """

    @pytest.mark.parametrize(
        "titel",
        [
            "Gesetz zur Änderung der Verfassung des Landes Baden-Württemberg",
            "Zweites Gesetz zur Änderung der Verfassung des Landes Baden-Württemberg",
            "Gesetz zur Änderung der Landesverfassung",
            # Case and whitespace tolerance
            "GESETZ ZUR ÄNDERUNG DER VERFASSUNG DES LANDES BADEN-WÜRTTEMBERG",
            "  Gesetz zur  Änderung der  Verfassung  ",
            # Nominal compound form (rare but observed historically)
            "Gesetz zur Verfassungsänderung",
        ],
    )
    def test_positive_cases(self, titel):
        assert is_verfassungsaendernd(titel) is True

    @pytest.mark.parametrize(
        "titel",
        [
            # Generic titles with no constitutional reference
            "Gesetz zum Klimaschutz",
            "Klimaschutz- und Klimawandelanpassungsgesetz",
            # "Änderung" alone (ordinary amendment of another Act)
            "Gesetz zur Änderung des Schulgesetzes",
            "Drittes Gesetz zur Änderung des Landesbeamtengesetzes",
            # "Verfassung" as part of a different compound — NOT an amendment
            # of the Landesverfassung itself.
            "Gesetz über die Aufgaben des Landesverfassungsschutzes",
            "Gesetz zur Stärkung des Verfassungsschutzes",
            # Empty / whitespace
            "",
            "   ",
        ],
    )
    def test_negative_cases(self, titel):
        assert is_verfassungsaendernd(titel) is False

    def test_returns_bool_not_truthy(self):
        """Output must be a plain `bool` so it serialises as JSON `true/false`."""
        result = is_verfassungsaendernd("Gesetz zur Änderung der Verfassung")
        assert result is True
        assert isinstance(result, bool)
