[← Index](../design_decisions.md)

# DD-050: Seiten-Marker-Regex verlangt Grenzzeichen, die am String-Rand fehlen (Issue #27)

**Datum:** 02.09.2026

**Kontext:** `_extract_relevant_pages` schneidet das `#page=N`-Fenster über
`_PAGE_MARKER_RE = r"\n\n<!-- PAGE (\d+) -->\n\n"` — ein Marker zählt nur, wenn er von
`\n\n` auf **beiden** Seiten umgeben ist. Kreuzbergs eigenes Markerformat trägt dieses
`\n\n` selbst, aber nur zwischen zwei Seiteninhalten; der Marker der **ersten** Seite
hat nichts davor. Bei GBl2026012.pdf (V-244180, `#page=1`) beginnt der extrahierte Text
direkt mit `<!-- PAGE 1 -->…` — kein Vortext, also kein führendes `\n\n`, also kein
Regex-Treffer für Marker 1. `_extract_relevant_pages` behandelt den kompletten
Seite-1-Text dann als „Text vor dem ersten Marker" (`parts[0]`), den die Schleife nie
ansieht, und verwirft ihn. Übrig blieb nur die Signaturseite (298 Zeichen) — über der
`MIN_TEXT_LENGTH`-Schwelle (DD-038), der Rückfall auf Volltext griff also nicht. Das
Ergebnis: `volltext` enthielt kommentarlos die falsche Seite, und das LLM fasste treu
die Unterschriftenzeile zusammen. Reproduziert direkt gegen die Original-PDF (siehe
Issue #27); Kreuzbergs Marker sind bestätigt 1-indiziert — kein Off-by-one, wie zunächst
vermutet.

Das Gegenstück existiert symmetrisch am **Dokumentende**: trägt die letzte Seite keinen
extrahierten Inhalt, fehlt das schließende `\n\n` nach ihrem Marker. Mit der alten Regex
matcht dieser Marker dann gar nicht — sein Text landet unabgetrennt im vorherigen
Seiteneintrag, samt der rohen `<!-- PAGE N -->`-Syntax als Literal.

**Entscheidung:** `_PAGE_MARKER_RE` verlangt weiterhin `\n\n` auf beiden Seiten,
akzeptiert aber ersatzweise den String-Rand:

```python
_PAGE_MARKER_RE = re.compile(r"(?:\A|\n\n)<!-- PAGE (\d+) -->(?:\n\n|\Z)")
```

Ein Marker zählt also, wenn er entweder von `\n\n` umgeben ist **oder** am Anfang/Ende
des extrahierten Textes steht. Der übrige `_extract_relevant_pages`-Ablauf (Fenster,
`MIN_TEXT_LENGTH`-Rückfall aus DD-038, Rückfall bei fehlenden Markern) bleibt
unverändert — nur die Markererkennung selbst wird grenzsicher.

**Bewusst nicht geändert:** Der `MIN_TEXT_LENGTH`-Rückfall (DD-038) bleibt die einzige
Absicherung gegen inhaltsarme Fenster; er fängt jetzt wieder tatsächlich leere
Randseiten ab, statt durch eine falsch zugeordnete Nachbarseite umgangen zu werden.

**Code:** `bawue_dok.py::_PAGE_MARKER_RE`, `_extract_relevant_pages`.

**Tests:** `tests/unit/test_bawue_dok.py::TestPageHintExtraction`:
`test_extract_relevant_pages_first_marker_at_string_start_issue27` (Marker der ersten
Seite ohne Vortext — reproduziert GBl2026012.pdf),
`test_extract_relevant_pages_trailing_marker_without_content_issue27` (Marker der
letzten Seite ohne Nachtext — verhindert Marker-Leakage in den vorherigen Seiteninhalt).
