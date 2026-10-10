[← Index](../design_decisions.md)

# DD-064: `Sitzung.nummer` bleibt `0`, wenn keine Quelle sie liefert (GitHub Issue #65)

**Datum:** 10.10.2026

**Kontext:** `Sitzung.nummer` ist Pflicht. `extract_session_number` liest sie per Regex
`(\d+)\.\s*Sitzung` aus dem ICS-SUMMARY. Das traf nur Plenarsitzungen, Ausschüsse bekamen
immer `0`. Geprüft am 10.10.2026, ob eine Quelle die Nummer liefert:

- **ICS-Feed:** Ein Event hat nur `UID`, `DTSTAMP`, `DTSTART`, `DTEND` und `SUMMARY`, kein
  `DESCRIPTION`, kein `URL`. Auch Plenarsitzungen heißen inzwischen nur `Plenarsitzung: `,
  der Regex trifft also derzeit nirgends.
- **Tagesordnungen** (`/de/aktuelles/tagesordnungen`): Ausschüsse „Zur Zeit liegen keine
  Tagesordnungen vor“. Fürs Plenum steht nur die nächste Sitzung da, die Nummer im
  Dateinamen (`2026-10-21_14_Plenarsitzung.pdf`).
- **Sitzungsplan-PDF:** Termine und Ausschuss-Kürzel, keine Sitzungsnummern.

**Entscheidung:**

1. Ohne Nummer bleibt `nummer=0` (Pflichtfeld, kein Weglassen möglich). Geraten wird nicht,
   auch nicht durch Abzählen der Plenartage.
2. Fail-loud: `item_extractor` loggt je Sitzung ohne Nummer eine Warnung mit Gremium und
   Datum. Weil Termine nach dem Upload gecacht sind, erscheint sie nur für neue Termine.
3. Der Regex bleibt. Liefert der Feed die Nummer wieder, wird sie ohne Codeänderung
   übernommen.

**Nicht umgesetzt:** Die Plenarnummer aus dem Dateinamen der Tagesordnung. Sie deckt nur die
nächste Sitzung ab und gehört zum TOP-Scraping (GitHub Issue #66).

**Code:** `BawueSitzungenScraper.item_extractor`, `ics_parser.extract_session_number`

**Tests:** `tests/unit/test_bawue_sitzungen_scraper.py::TestIssue65SessionNumber`
