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
            ("Zustimmung   Plenarprotokoll 17/143", None, Stationstyp.PARL_MINUS_AKZEPTANZ),
            ("Ablehnung   Plenarprotokoll 17/143", None, Stationstyp.PARL_MINUS_ABLEHNUNG),
            ("Ausfertigung   10.03.2026", None, Stationstyp.POSTPARL_MINUS_VESJA),
            ("Gesetzblatt   15.03.2026", None, Stationstyp.POSTPARL_MINUS_GSBLT),
            ("Inkrafttreten   01.04.2026", None, Stationstyp.POSTPARL_MINUS_KRAFT),
        ],
    )
    def test_known_patterns(self, text, initiator, expected):
        assert map_stationstyp(text, initiator=initiator) == expected

    def test_unknown_and_empty_default_to_sonstig(self):
        assert map_stationstyp("unknown text") == Stationstyp.SONSTIG
        assert map_stationstyp("") == Stationstyp.SONSTIG

    def test_case_insensitive(self):
        assert map_stationstyp("erste beratung   Plenarprotokoll 17/141") == Stationstyp.PARL_MINUS_VOLLVLSGN


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
