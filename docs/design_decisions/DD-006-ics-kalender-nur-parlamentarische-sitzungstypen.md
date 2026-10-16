[← Index](../design_decisions.md)

# DD-006: ICS-Kalender — nur parlamentarische Sitzungstypen

**Datum:** 27.03.2026

**Kontext:** Der ICS-Feed des Landtags enthält ca. 8 Eventkategorien, die anhand
des SUMMARY-Präfix unterschieden werden. Nicht alle sind parlamentarische Sitzungen
im Sinne des PaZuFa-Datenmodells.

**Entscheidung** (überarbeitet 16.10.2026, GitHub Issue #117, DD-069): Übernommen werden
Plenarsitzungen und jeder im SUMMARY benannte Ausschuss:

| SUMMARY-Präfix                                           | Gremium                                  |
|----------------------------------------------------------|------------------------------------------|
| `Plenarsitzung:`                                         | `plenum` (reserviert)                    |
| `Fraktions- und Ausschusssitzungen: <Kürzel>, <Kürzel>…` | je Kürzel eine Sitzung, Name per Tabelle |
| `Haushaltsberatungen: <Kürzel>…`                         | wie oben                                 |

- **Ein Termin, mehrere Sitzungen.** Der Feed nennt die Ausschüsse inzwischen per Kürzel, oft
  mehrere je Termin (`FinA, BildungsA, VerkA`). Jeder wird eine eigene Sitzung mit Termin und
  Titel des Eintrags. Die `api_id` enthält deshalb neben UID und Datum (DD-069) das Gremium.
- **Name wie in PARLIS.** `COMMITTEE_NAMES` bildet das Kürzel auf den Namen ab, den die Seite des
  Ausschusses auf landtag-bw.de mit diesem Kürzel führt (geprüft am 16.10.2026, z. B. „Ausschuss für
  Kultus (BildungsA)“). PARLIS schreibt die Namen gleich; für Inneres, Ländlichen Raum und den
  Ständigen Ausschuss ist das an den Gremien der WP18-Vorgänge im Backend geprüft. Kalender und
  Stationen teilen sich so ein Gremium; das Backend sucht Gremien über den exakten Namen. Der
  frühere Name `Finanzausschuss` entfällt.
- **Unbekanntes Kürzel:** Warnung, übersprungen; die übrigen Ausschüsse des Eintrags bleiben. Ein
  neuer Ausschuss braucht einen Eintrag in der Tabelle, geraten wird nicht.
- **`Haushaltsberatungen:`** läuft durch dieselbe Tabelle. Im Feed steht derzeit kein solcher
  Termin; das bisher durchgereichte Suffix hätte einen Gremiennamen neben PARLIS erzeugt.

Ausgeschlossen bleiben: **Fraktionen** (parteiinterne Sitzungen), der **Sammel-Eintrag ohne
Ausschuss-Namen** (früher `Ausschuesse`, heute leeres Suffix; DoD-Regel „Namen MÜSSEN so spezifisch
wie möglich sein“, s. DD-022), **Präsidium** (Verwaltung), **Gedenktag**, **Wahl**
(Verfassungsereignis). Diese Events werden still übersprungen.

**Folgen:** Der Fingerprint (DD-066) enthält das Gremium, nach dem Deploy wird daher jeder Tag mit
Ausschüssen einmal neu hochgeladen (Feed vom 16.10.2026: 151 statt 47 Sitzungen, 71 statt 47
Tage). Je Sitzung ohne Nummer warnt `item_extractor` weiter einzeln (DD-064).

**Altes Gremium `Finanzausschuss`:** Alle seine Sitzungen liegen in der Zukunft (je 12 auf Staging
und Prod, 12.11.2026 bis 26.11.2027, geprüft am 16.10.2026). Der erste Lauf ersetzt sie alle. Das
Gremium bleibt danach ohne Sitzungen im Backend stehen (`GET /api/v2/gremien`) und wird per Admin
gelöscht: `DELETE /api/v2/gremien?p=BW&wp=18&gr=Finanzausschuss` (`gr` ist ein Teilstring).

**Bis 16.10.2026:** nur `…: FinA` (als `Finanzausschuss`) und `Haushaltsberatungen:` mit
durchgereichtem Suffix; der Feed nannte damals keine anderen Ausschüsse.

**Implementierung:** `ics_parser.extract_gremium_names`, `COMMITTEE_NAMES`, `parse_ics_feed`;
`api_id` in `BawueSitzungenScraper.item_extractor`.

**Tests:** `tests/unit/test_ics_parser.py::TestIssue117NamedCommittees`,
`tests/unit/test_bawue_sitzungen_scraper.py::TestIssue117NamedCommittees`
