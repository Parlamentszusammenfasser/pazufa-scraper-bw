[← Index](../design_decisions.md)

# DD-006: ICS-Kalender — nur parlamentarische Sitzungstypen

**Datum:** 27.03.2026

**Kontext:** Der ICS-Feed des Landtags enthält ca. 8 Eventkategorien, die anhand
des SUMMARY-Präfix unterschieden werden. Nicht alle sind parlamentarische Sitzungen
im Sinne des PaZuFa-Datenmodells.

**Entscheidung:** Nur folgende Eventtypen werden übernommen:

| SUMMARY-Präfix                            | Gremium                 |
|-------------------------------------------|-------------------------|
| `Plenarsitzung:`                          | `plenum` (reserviert)   |
| `Fraktions- und Ausschusssitzungen: FinA` | `Finanzausschuss`       |
| `Haushaltsberatungen:`                    | (aus Suffix extrahiert) |

Ausgeschlossen werden: **Fraktionen** (parteiinterne Sitzungen), **Ausschuesse**
(Sammel-Event ohne Ausschuss-Namen — DoD-Regel "Namen MÜSSEN so spezifisch wie
möglich sein", s. DD-022), **Präsidium** (Verwaltung), **Wahl**
(Verfassungsereignis). Diese Events werden still übersprungen.

**Implementierung:** `ics_parser.py`, Funktion `_classify_event()`.
