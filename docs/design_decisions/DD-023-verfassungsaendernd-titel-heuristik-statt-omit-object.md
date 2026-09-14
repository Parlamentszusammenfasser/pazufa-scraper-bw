[← Index](../design_decisions.md)

# DD-023: `verfassungsaendernd` — Titel-Heuristik statt „omit object"

**Datum:** 22.04.2026

**Kontext:** Das Pflichtfeld `Vorgang.verfassungsaendernd` existiert im PaZuFa-
Datenmodell, aber die BaWue-Quellen exponieren es nicht:

- Die PARLIS-Suchmaske kennt keine entsprechende Facette (geprüft am
  22.04.2026: Wahlperiode, Zeitraum, Stand, Urheber, Deskriptor, Suchbegriff —
  kein Verfassungsänderungs-Flag).
- Die Detail-Metadaten liefern das Attribut ebenfalls nicht.
- Das Beteiligungsportal und das Gesetzblatt führen es nicht in ihren
  Process-Listen.

Die Community-DoD verlangt, dass Objekte ausgelassen werden müssen, wenn ein
Pflichtfeld nicht sinnvoll gefüllt werden kann („Wenn nicht-optionale Felder
aus euren Quellen nicht mit einem sinnvollen Wert gefüllt werden können, muss
das gesamte Objekt ausgelassen werden"). Wörtlich angewandt würde das für BaWue
bedeuten, **100 %** aller Vorgänge zu verwerfen, weil PARLIS das Attribut für
*keinen* Vorgang liefert. Das steht offensichtlich im Widerspruch zum
Projektziel.

**Entscheidung:** Der Wert wird aus dem `titel` **heuristisch abgeleitet**. Die
deutsche Gesetzgebungssprache nennt Verfassungsänderungen sehr stringent — Art.
64 der Landesverfassung BW verlangt eine 2/3-Mehrheit, und die entsprechenden
Gesetze tragen das im Titel:

| Phrasing                                                    | Behandlung |
|-------------------------------------------------------------|------------|
| `Änderung der Verfassung` / `Änderung der Landesverfassung` | `True`     |
| `Verfassungsänderung` (nominal compound)                    | `True`     |
| alles Übrige                                                | `False`    |

Die Heuristik ist mit Absicht konservativ: Titel wie „Gesetz zur Stärkung des
Verfassungsschutzes" oder „Landesverfassungsschutzgesetz" matchen nicht (eine
Wortgrenze-Guard verhindert das Matching im Inneren von Komposita).

**Implementierung:** `src/bawue/types.py`, Funktion `is_verfassungsaendernd()`.
Eingebunden in:

- `bawue_vorgaenge_scraper.py` (Vorgang aus PARLIS-Titel)
- `bawue_beteiligung_scraper.py` (Vorgang aus Beteiligungsportal-Titel)

Ein zukünftiger `bawue_gesetzblatt_scraper.py` sollte die Funktion analog
anwenden (Gesetzblatt-Titel folgen derselben Konvention).

**Empirische Basis:** WP 17 (Stand 22.04.2026) enthält nach dem vollen
Scrape keine Titel, die auf die Heuristik treffen — Verfassungsänderungen sind
in BaWue historisch extrem selten (letzte reguläre Änderung der
Landesverfassung: 2015, WP 15). Der Default-Pfad bleibt also praktisch
`False`, und die Heuristik schützt lediglich gegen künftige Fehlklassifikation,
sollte doch ein Verfassungsänderungs-Vorgang eingebracht werden.

**DoD-Konflikt:** Die Abweichung von der „omit object"-Regel ist hiermit
dokumentiert (DoD-Regel „Abweichungen MÜSSEN dokumentiert sein"). Wenn das
Backend-Schema das Feld künftig als optional deklariert oder eine
`unknown`-Semantik bekommt, ist DD-023 zurückzunehmen und die Heuristik zu
entfernen.

**Tests:** `tests/unit/test_enum_mapper.py::TestIsVerfassungsaendernd`:

- Positive: kanonische Titel inkl. Groß-/Kleinschreibungs- und
  Whitespace-Toleranz sowie Nominal-Kompositum.
- Negative: `Verfassungsschutz`-Kompositum, reguläre Änderungsgesetze
  ohne Verfassungsbezug, leere/whitespace-only-Strings.
- `test_returns_bool_not_truthy` — Rückgabewert ist ein echter `bool`
  (JSON `true`/`false`), kein Match-Object-Proxy.
