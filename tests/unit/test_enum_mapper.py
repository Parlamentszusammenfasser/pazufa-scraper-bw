"""Tests for the PARLIS→PaZuFa enum mapper."""

import pytest
from openapi_client.models.doktyp import Doktyp
from openapi_client.models.stationstyp import Stationstyp
from openapi_client.models.vorgangstyp import Vorgangstyp

from bawue.enum_mapper import (
    VORGANGSTYP_MAP,
    map_dokumententyp,
    map_stationstyp,
    map_vorgangstyp,
)


class TestVorgangstypMapping:
    @pytest.mark.parametrize(
        "parlis_typ,expected",
        [
            ("Gesetzgebung", Vorgangstyp.GG_MINUS_LAND_MINUS_PARL),
            ("Haushaltsgesetzgebung", Vorgangstyp.GG_MINUS_LAND_MINUS_PARL),
            ("Volksantrag", Vorgangstyp.GG_MINUS_LAND_MINUS_PARL),
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
            ("Erste Beratung   Plenarprotokoll 17/141 05.02.2026", None, Stationstyp.PARL_MINUS_VOLLVLSGN),
            ("Zweite Beratung   Plenarprotokoll 17/142 06.02.2026", None, Stationstyp.PARL_MINUS_VOLLVLSGN),
            (
                "Gesetzentwurf    Fraktion GRÜNE  04.02.2026 Drucksache 17/10266   (13 S.)",
                None,
                Stationstyp.PARL_MINUS_INITIATIV,
            ),
            (
                "Gesetzentwurf    Landesregierung  04.02.2026 Drucksache 17/10266",
                "Landesregierung",
                Stationstyp.PREPARL_MINUS_REGENT,
            ),
            (
                "Beschlussempfehlung und Bericht    Ausschuss für Wirtschaft  02.02.2026 Drucksache 17/10210",
                None,
                Stationstyp.PARL_MINUS_AUSSCHBER,
            ),
            (
                "Kleine Anfrage   Dr. Schweickert (FDP/DVP)  15.01.2026 Drucksache 17/10143",
                None,
                Stationstyp.PARL_MINUS_INITIATIV,
            ),
            (
                "Große Anfrage   Fraktion der SPD  10.01.2026 Drucksache 17/10100",
                None,
                Stationstyp.PARL_MINUS_INITIATIV,
            ),
            (
                "Mündliche Anfrage   Plenarprotokoll 17/141 05.02.2026",
                None,
                Stationstyp.PARL_MINUS_INITIATIV,
            ),
            ("Zustimmung   Plenarprotokoll 17/143", None, Stationstyp.PARL_MINUS_AKZEPTANZ),
            (
                "Gesetzesbeschluss des Landtags      05.02.2026 Drucksache 17/10267",
                None,
                Stationstyp.PARL_MINUS_AKZEPTANZ,
            ),
            (
                "Beschluss des Landtags in Zweiter Beratung      06.02.2026 Drucksache 17/2271",
                None,
                Stationstyp.PARL_MINUS_AKZEPTANZ,
            ),
            ("Ablehnung   Plenarprotokoll 17/143", None, Stationstyp.PARL_MINUS_ABLEHNUNG),
            ("Ausfertigung   10.03.2026", None, Stationstyp.POSTPARL_MINUS_VESJA),
            (
                "Gesetz  vom 10. Februar 2026 Gesetzblatt für Baden-Württemberg 2026 Nr. 22",
                None,
                Stationstyp.POSTPARL_MINUS_GSBLT,
            ),
            (
                "Bekanntmachung der Neufassung      12.05.2021",
                None,
                Stationstyp.POSTPARL_MINUS_GSBLT,
            ),
            ("Gesetzblatt   15.03.2026", None, Stationstyp.POSTPARL_MINUS_GSBLT),
            ("Inkrafttreten   01.04.2026", None, Stationstyp.POSTPARL_MINUS_KRAFT),
            (
                "Änderungsanträge    Fraktion der FDP/DVP  20.07.2021 Drucksache 17/569",
                None,
                Stationstyp.PARL_MINUS_INITIATIV,
            ),
            (
                "Bericht und Empfehlungen    Petitionsausschuss  15.03.2026 Drucksache 17/1234",
                None,
                Stationstyp.PARL_MINUS_AUSSCHBER,
            ),
        ],
    )
    def test_known_patterns(self, text, initiator, expected):
        assert map_stationstyp(text, initiator=initiator) == expected

    def test_unknown_and_empty_default_to_sonstig(self):
        assert map_stationstyp("unknown text") == Stationstyp.SONSTIG
        assert map_stationstyp("") == Stationstyp.SONSTIG

    def test_case_insensitive(self):
        assert map_stationstyp("erste beratung   Plenarprotokoll 17/141") == Stationstyp.PARL_MINUS_VOLLVLSGN

    def test_longer_keys_take_precedence_over_gesetz(self):
        """'Gesetzentwurf' and 'Gesetzesbeschluss' must match before shorter 'Gesetz'."""
        assert map_stationstyp("Gesetzentwurf    Fraktion GRÜNE") == Stationstyp.PARL_MINUS_INITIATIV
        assert map_stationstyp("Gesetzesbeschluss des Landtags      05.02.2026") == Stationstyp.PARL_MINUS_AKZEPTANZ
        assert map_stationstyp("Gesetzblatt   15.03.2026") == Stationstyp.POSTPARL_MINUS_GSBLT
        # Only bare "Gesetz" (enacted law) matches the short key
        assert map_stationstyp("Gesetz  vom 10. Februar 2026") == Stationstyp.POSTPARL_MINUS_GSBLT

    def test_antraege_plural_maps_to_initiativ(self):
        """Plural 'Änderungsanträge' (with umlaut ä) must not fall through to SONSTIG."""
        assert map_stationstyp("Änderungsanträge    Fraktion der FDP/DVP") == Stationstyp.PARL_MINUS_INITIATIV


class TestDokumententypMapping:
    @pytest.mark.parametrize(
        "context,is_vorparl,expected",
        [
            ("Gesetzentwurf", False, Doktyp.ENTWURF),
            ("Gesetzentwurf", True, Doktyp.PREPARL_MINUS_ENTWURF),
            ("Plenarprotokoll", False, Doktyp.REDEPROTOKOLL),
            ("Antrag", False, Doktyp.ANTRAG),
            ("Kleine Anfrage", False, Doktyp.ANFRAGE),
            ("Stellungnahme", False, Doktyp.STELLUNGNAHME),
            ("Beschlussempfehlung", False, Doktyp.BESCHLUSSEMPF),
        ],
    )
    def test_known_patterns(self, context, is_vorparl, expected):
        assert map_dokumententyp(context, is_vorparlamentarisch=is_vorparl) == expected

    def test_unknown_and_empty_default_to_sonstig(self):
        assert map_dokumententyp("unknown") == Doktyp.SONSTIG
        assert map_dokumententyp("") == Doktyp.SONSTIG


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
            "postparl-vesja",
            "postparl-gsblt",
            "postparl-kraft",
            "sonstig",
        }
        framework_values = {m.value for m in Stationstyp}
        assert bawue_values.issubset(framework_values)

    def test_all_vorgangstyp_values_valid(self):
        bawue_values = {"gg-land-parl", "bw-einsatz", "sonstig"}
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
